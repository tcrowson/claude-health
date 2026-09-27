"""Shared inventory for the checkup scripts: config, file discovery, language scanning, imports and git.
Every script imports this module, so the language table, the skip rules and the git parsing live in one
place. Standard library only; Python 3.10 or newer.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import json
import posixpath
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path, PurePosixPath

SCRIPTS_DIR = Path(__file__).resolve().parent
LANGUAGES_FILE = SCRIPTS_DIR / "languages.json"
CONFIG_RELPATH = PurePosixPath(".claude/checkup/config.json")
CONFIG_DEFAULTS: dict = {
    "src": ["."],
    "exclude": [],
    "tests": [],
    "data_root": "checkup",
    "profiles": [],
    "extensions": {},
    "framework_names": [],
    "risk_markers": {},
    "forbidden_imports": [],
    "extras": [],
    "history_months": 12,
}
PYTHON = "python"
PACKAGE_DEPTH = 2
MAX_BOOST = 2.0           # a file's weight is at most (1 + MAX_BOOST) x its lines
BOOST_PER_MARKER = 0.04   # weight boost per risk marker per 100 lines
STATE_CATEGORIES = ("events", "async", "state")
SNIFF_CHARS = 600
GIT = "git"
RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"
STRING_STYLES: dict[str, str] = {
    "python": r"""[rRbBuUfF]{0,2}(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')""",
    "double": r'"(?:\\.|[^"\\\n])*"',
    "single": r"'(?:\\.|[^'\\\n])*'",
    "char": r"'(?:\\.|[^'\\\n])'",
    "backtick": r"`(?:\\.|[^`\\])*`",
    "textblock": r'"""[\s\S]*?"""',
    "verbatim": r'@"(?:""|[^"])*"',
}
IDENT = r"[A-Za-z_$][\w$]*"
NUMBER = r"\d[\w.]*"
BRACES = re.compile(r"[{}]")
NEWLINE = re.compile(r"\n")
NOT_NEWLINE = re.compile(r"[^\n]")
TOKEN_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CALL_RX = re.compile(r"([A-Za-z_]\w*)\s*\(")   # a name followed by "(": a call, or a definition's header


@lru_cache(maxsize=1)
def tables() -> dict:
    """Load the language table once.
    Returns:
        The parsed languages.json.
    """
    return json.loads(LANGUAGES_FILE.read_text(encoding="utf-8"))


def language(lang: str) -> dict:
    """Return one language's entry from the table.
    Args:
        lang: The language key, e.g. "python".
    Returns:
        Its spec dict.
    """
    return tables()["languages"][lang]


@cache
def extension_map(extra: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    """Map file extensions to language keys.
    Args:
        extra: Project mappings from the config's "extensions", as sorted pairs.
    Returns:
        Lower-case extension (with the dot) to language key.
    """
    mapping = {ext: name for name, spec in tables()["languages"].items() for ext in spec["extensions"]}
    mapping.update(dict(extra))
    return mapping


@lru_cache(maxsize=1)
def control_words() -> frozenset[str]:
    """Return the keywords a function-header regex can mistake for a function name.
    Returns:
        The control words from the table.
    """
    return frozenset(tables()["control_words"])


@cache
def blank_pattern(lang: str) -> re.Pattern | None:
    """Build the regex that finds comments and strings, for masking.
    Args:
        lang: The language key.
    Returns:
        The compiled pattern, or None when the language has neither.
    """
    spec = language(lang)
    parts = [re.escape(c) + r"[^\n]*" for c in spec.get("line_comment", [])]
    parts += [re.escape(a) + r"[\s\S]*?" + re.escape(b) for a, b in spec.get("block_comment", [])]
    parts += [STRING_STYLES[s] for s in spec.get("strings", [])]
    return re.compile("|".join(parts)) if parts else None


@cache
def token_pattern(lang: str) -> re.Pattern:
    """Build the tokenizer regex: comments (c), strings (s), identifiers (i), numbers (n), punctuation (p).
    Args:
        lang: The language key.
    Returns:
        The compiled pattern; the matched group name is the token kind.
    """
    spec = language(lang)
    comments = [re.escape(c) + r"[^\n]*" for c in spec.get("line_comment", [])]
    comments += [re.escape(a) + r"[\s\S]*?" + re.escape(b) for a, b in spec.get("block_comment", [])]
    strings = [STRING_STYLES[s] for s in spec.get("strings", [])]
    parts = []
    if comments:
        parts.append(f"(?P<c>{'|'.join(comments)})")
    if strings:
        parts.append(f"(?P<s>{'|'.join(strings)})")
    parts += [f"(?P<i>{IDENT})", f"(?P<n>{NUMBER})", r"(?P<p>[^\s\w])"]
    return re.compile("|".join(parts))


@cache
def compiled(lang: str, key: str) -> re.Pattern | None:
    """Compile a single-regex entry of a language (e.g. "branch", "swallow").
    Args:
        lang: The language key.
        key: The entry name.
    Returns:
        The multiline pattern, or None when the entry is empty.
    """
    source = language(lang).get(key) or ""
    return re.compile(source, re.MULTILINE) if source else None


@cache
def compiled_list(lang: str, key: str) -> tuple[re.Pattern, ...]:
    """Compile a list-of-regexes entry of a language (e.g. "function", "imports").
    Args:
        lang: The language key.
        key: The entry name.
    Returns:
        The multiline patterns.
    """
    return tuple(re.compile(s, re.MULTILINE) for s in language(lang).get(key, []))


@cache
def risk_patterns(lang: str, extra: tuple[tuple[str, str], ...] = ()) -> dict[str, re.Pattern]:
    """Compile a language's risk-marker regexes, extended by the project's own.
    Args:
        lang: The language key.
        extra: (category, regex) pairs from config "risk_markers"; a known category gains the regex as an
            alternative, a new one is added.
    Returns:
        Category name to multiline pattern.
    """
    merged = dict(language(lang).get("risk", {}))
    for cat, rx in extra:
        merged[cat] = f"{merged[cat]}|{rx}" if cat in merged else rx
    return {cat: re.compile(rx, re.MULTILINE) for cat, rx in merged.items()}


def mask(text: str, lang: str) -> str:
    """Blank out comments and strings, keeping every offset and newline in place.
    Args:
        text: The source text.
        lang: The language key.
    Returns:
        The text with comment and string characters replaced by spaces.
    """
    rx = blank_pattern(lang)
    return rx.sub(lambda m: NOT_NEWLINE.sub(" ", m.group()), text) if rx else text


def module_name(rel: str) -> str:
    """Map a repo-relative path to a dotted module name.
    Args:
        rel: The forward-slash path relative to the root.
    Returns:
        The dotted name; a package __init__ maps to the package.
    """
    parts = list(PurePosixPath(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(rel: str, depth: int = PACKAGE_DEPTH) -> str:
    """Return the package key a file belongs to.
    Args:
        rel: The forward-slash path relative to the root.
        depth: How many directory levels form the key.
    Returns:
        The directory prefix, or "." for a top-level file.
    """
    parts = PurePosixPath(rel).parent.parts[:depth]
    return "/".join(parts) if parts else "."


@dataclass
class SourceFile:
    """One source file with lazily computed views of its text."""

    rel: str
    path: Path
    lang: str
    text: str
    lines: int = 0
    extra_risk: tuple[tuple[str, str], ...] = ()
    _masked: str | None = field(default=None, repr=False)
    _tree: ast.Module | None = field(default=None, repr=False)
    _parsed: bool = field(default=False, repr=False)
    _starts: list[int] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Count the lines once."""
        self.lines = self.text.count("\n") + 1

    @property
    def masked(self) -> str:
        """Return the text with comments and strings blanked.
        Returns:
            The masked text.
        """
        if self._masked is None:
            self._masked = mask(self.text, self.lang)
        return self._masked

    @property
    def tree(self) -> ast.Module | None:
        """Return the Python syntax tree, parsed once.
        Returns:
            The module node, or None for other languages and files that do not parse.
        """
        if not self._parsed:
            self._parsed = True
            if self.lang == PYTHON:
                try:
                    self._tree = ast.parse(self.text)
                except (SyntaxError, ValueError):
                    self._tree = None
        return self._tree

    @property
    def module(self) -> str:
        """Return the dotted module name.
        Returns:
            The module name derived from the path.
        """
        return module_name(self.rel)

    def line_of(self, pos: int) -> int:
        """Convert a character offset to a 1-based line number.
        Args:
            pos: The offset into the text.
        Returns:
            The line number.
        """
        if self._starts is None:
            self._starts = [0] + [m.end() for m in NEWLINE.finditer(self.text)]
        return bisect.bisect_right(self._starts, pos)


def risk_markers(sf: SourceFile) -> dict[str, int]:
    """Count the risk markers of each category in a file's code (comments and strings excluded).
    Args:
        sf: The source file.
    Returns:
        Category name to count.
    """
    return {cat: len(rx.findall(sf.masked)) for cat, rx in risk_patterns(sf.lang, sf.extra_risk).items()}


def file_stats(sf: SourceFile) -> dict:
    """Count a file's lines and risk markers and derive its weight.
    Args:
        sf: The source file.
    Returns:
        Lines, markers by category, marker density and stateful density per 100 lines, and the weight
        (lines scaled up by marker density, at most (1 + MAX_BOOST) x lines).
    """
    markers = risk_markers(sf)
    per100 = 100.0 / max(sf.lines, 1)
    density = per100 * sum(markers.values())
    stateful = per100 * sum(markers.get(c, 0) for c in STATE_CATEGORIES)
    boost = min(MAX_BOOST, BOOST_PER_MARKER * density)
    return {"lines": sf.lines, "markers": markers, "density": round(density, 1),
            "stateful": round(stateful, 2), "weight": round(sf.lines * (1.0 + boost))}


def match_brace(masked: str, pos: int) -> int | None:
    """Find the brace that closes the one at pos.
    Args:
        masked: Text with comments and strings blanked.
        pos: Offset of an opening brace.
    Returns:
        Offset of the matching closing brace, or None when unbalanced.
    """
    depth = 0
    for m in BRACES.finditer(masked, pos):
        depth += 1 if m.group() == "{" else -1
        if depth == 0:
            return m.start()
    return None


def brace_functions(sf: SourceFile) -> list[dict]:
    """Find functions in a brace language by header regex and brace matching (approximate).
    Args:
        sf: A non-Python source file.
    Returns:
        Dicts with name, line, lines and approximate complexity (cc), in file order.
    """
    control = control_words()
    branch = compiled(sf.lang, "branch")
    masked = sf.masked
    found: dict[int, dict] = {}
    for rx in compiled_list(sf.lang, "function"):
        for m in rx.finditer(masked):
            name, brace = m.group(1), m.end() - 1
            if name in control or brace in found:
                continue
            end = match_brace(masked, brace)
            if end is None:
                continue
            first, last = sf.line_of(m.start(1)), sf.line_of(end)
            cc = 1 + (len(branch.findall(masked, brace, end + 1)) if branch else 0)
            found[brace] = {"name": name, "line": first, "lines": last - first + 1, "cc": cc}
    return sorted(found.values(), key=lambda f: f["line"])


def tokens(sf: SourceFile) -> list[tuple[str, str, int]]:
    """Tokenize a file, dropping comments.
    Args:
        sf: The source file.
    Returns:
        (kind, text, line) triples; kind is s (string), i (identifier), n (number) or p (punctuation).
    """
    out = []
    for m in token_pattern(sf.lang).finditer(sf.text):
        if m.lastgroup != "c":
            out.append((m.lastgroup, m.group(), sf.line_of(m.start())))
    return out


def import_targets(sf: SourceFile, module_level_only: bool = False) -> list[tuple[str, int, tuple[str, ...]]]:
    """List a file's imports as written.
    Args:
        sf: The source file.
        module_level_only: For Python, only imports at module level (the ones that can form load cycles).
    Returns:
        (target, line, imported names) triples; relative Python imports are made absolute.
    """
    if sf.lang == PYTHON:
        tree = sf.tree
        if tree is None:
            return []
        base_pkg = sf.module if sf.rel.endswith("__init__.py") else sf.module.rpartition(".")[0]
        out = []
        for node in (tree.body if module_level_only else ast.walk(tree)):
            if isinstance(node, ast.Import):
                out += [(alias.name, node.lineno, ()) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    pkg = base_pkg.split(".") if base_pkg else []
                    pkg = pkg[: len(pkg) - (node.level - 1)]
                    target = ".".join([*pkg, node.module] if node.module else pkg)
                else:
                    target = node.module or ""
                out.append((target, node.lineno, tuple(alias.name for alias in node.names)))
        return out
    out = []
    for rx in compiled_list(sf.lang, "imports"):
        for m in rx.finditer(sf.text):
            target = next((g for g in m.groups() if g), None)
            if target:
                out.append((target, sf.line_of(m.start()), ()))
    block = language(sf.lang).get("import_block")
    if block:
        outer, inner = re.compile(block[0]), re.compile(block[1])
        for m in outer.finditer(sf.text):
            for im in inner.finditer(m.group(1)):
                out.append((im.group(1), sf.line_of(m.start(1) + im.start()), ()))
    return out


class ImportIndex:
    """Resolves import targets to repo files, for the languages whose imports name files or modules."""

    def __init__(self, files: list[SourceFile]) -> None:
        """Index the files by path, module, basename and directory.
        Args:
            files: Every source file in scope.
        """
        self.rels = {f.rel for f in files}
        self.modules = {f.module: f.rel for f in files if f.lang == PYTHON}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.by_dir: dict[str, list[str]] = defaultdict(list)
        self.by_stem: dict[str, str] = {}
        for f in files:
            p = PurePosixPath(f.rel)
            self.by_name[p.name].append(f.rel)
            self.by_dir[p.parent.as_posix()].append(f.rel)
            self.by_stem[p.with_suffix("").as_posix()] = f.rel

    def resolve(self, sf: SourceFile, target: str, names: tuple[str, ...]) -> set[str]:
        """Map one import to the repo files it loads.
        Args:
            sf: The importing file.
            target: The import target as written (made absolute for Python).
            names: For Python "from x import a, b", the imported names.
        Returns:
            Repo-relative paths; empty when the target is external or the language cannot resolve.
        """
        mode = language(sf.lang).get("resolve", "none")
        out: set[str] = set()
        if mode == PYTHON:
            for name in names:
                sub = f"{target}.{name}"
                if sub in self.modules:
                    out.add(self.modules[sub])
                elif target in self.modules:
                    out.add(self.modules[target])
            if not names and target in self.modules:
                out.add(self.modules[target])
        elif mode == "relative":
            here = PurePosixPath(sf.rel).parent.as_posix()
            if target.startswith("."):
                base = posixpath.normpath(posixpath.join(here, target))
                exts = language(sf.lang)["extensions"]
                for cand in [base, *(base + e for e in exts), *(f"{base}/index{e}" for e in exts)]:
                    if cand in self.rels:
                        out.add(cand)
                        break
            elif not target.startswith(("@", "/")) and "." in PurePosixPath(target).name:
                local = posixpath.normpath(posixpath.join(here, target))
                if local in self.rels:
                    out.add(local)
                else:
                    out.update(r for r in self.by_name.get(PurePosixPath(target).name, []) if r.endswith("/" + target) or r == target)
        elif mode == "dotted_path":
            stem = target.replace(".", "/")
            out.update(r for s, r in self.by_stem.items() if s == stem or s.endswith("/" + stem))
        elif mode == "dir_suffix":
            for d, rels in self.by_dir.items():
                if d == target or d.endswith("/" + target):
                    out.update(rels)
        out.discard(sf.rel)
        return out


def import_graph(files: list[SourceFile], module_level_only: bool = False) -> dict[str, set[str]]:
    """Build the file-level import graph inside the repo.
    Args:
        files: Every source file in scope.
        module_level_only: For Python, only module-level imports.
    Returns:
        Repo-relative path to the repo files it imports.
    """
    index = ImportIndex(files)
    graph = {}
    for f in files:
        edges: set[str] = set()
        for target, _, names in import_targets(f, module_level_only):
            edges |= index.resolve(f, target, names)
        graph[f.rel] = edges
    return graph


def git(root: Path, *args: str) -> str | None:
    """Run a git command in the repo.
    Args:
        root: The repo root.
        *args: The git arguments.
    Returns:
        Its stdout, or None when git is missing or the command fails.
    """
    try:
        done = subprocess.run([GIT, "-C", str(root), "-c", "core.quotepath=false", *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def head_commit(root: Path) -> str | None:
    """Return the short hash of HEAD.
    Args:
        root: The repo root.
    Returns:
        The hash, or None outside a git repo.
    """
    out = git(root, "rev-parse", "--short", "HEAD")
    return out.strip() if out else None


def changed_since(root: Path, commit: str) -> set[str] | None:
    """List files that differ between a commit and HEAD.
    Args:
        root: The repo root.
        commit: The older commit.
    Returns:
        Repo-relative paths, or None when git cannot answer.
    """
    out = git(root, "diff", "--name-only", commit, "HEAD")
    return None if out is None else {line.strip() for line in out.splitlines() if line.strip()}


@dataclass
class Commit:
    """One commit from git log."""

    sha: str
    date: str
    subject: str
    files: list[str]


def git_log(root: Path, months: int, paths: list[str]) -> list[Commit] | None:
    """Read the non-merge commits of the last months that touch the given paths.
    Args:
        root: The repo root.
        months: How far back to read.
        paths: Path prefixes to restrict to ("." for everything).
    Returns:
        The commits, newest first, or None outside a git repo.
    """
    fmt = f"{RECORD_SEP}%h{FIELD_SEP}%ad{FIELD_SEP}%s"
    out = git(root, "log", "--no-merges", f"--since={months}.months.ago", "--date=short",
              f"--format={fmt}", "--name-only", "--", *(paths or ["."]))
    if out is None:
        return None
    commits = []
    for record in out.split(RECORD_SEP)[1:]:
        head, _, body = record.partition("\n")
        sha, day, subject = (head.split(FIELD_SEP, 2) + ["", ""])[:3]
        commits.append(Commit(sha, day, subject, [ln.strip() for ln in body.splitlines() if ln.strip()]))
    return commits


def churn(commits: list[Commit]) -> Counter:
    """Count the commits that touched each file.
    Args:
        commits: Commits from git_log.
    Returns:
        Path to commit count.
    """
    return Counter(f for c in commits for f in set(c.files))


def _norm_prefix(p: str) -> str:
    """Normalize a configured path prefix.
    Args:
        p: A path as written in the config or on the command line.
    Returns:
        A forward-slash prefix without "./" or a trailing slash; "" means everything.
    """
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return "" if p in (".", "") else p.rstrip("/")


def under(rel: str, prefixes: list[str]) -> bool:
    """Tell whether a path lies under any of the prefixes.
    Args:
        rel: A repo-relative path.
        prefixes: Normalized prefixes; "" matches everything.
    Returns:
        True when one prefix covers the path.
    """
    return any(p == "" or rel == p or rel.startswith(p + "/") for p in prefixes)


def walk(root: Path, vendored: set[str]) -> list[str]:
    """List files under root without git, skipping vendored directories.
    Args:
        root: The repo root.
        vendored: Directory names never descended into.
    Returns:
        Repo-relative forward-slash paths.
    """
    out, stack = [], [root]
    while stack:
        d = stack.pop()
        for p in d.iterdir():
            if p.is_dir():
                if p.name not in vendored and not p.name.startswith("."):
                    stack.append(p)
            else:
                out.append(p.relative_to(root).as_posix())
    return out


def discover(root: Path, config: dict) -> tuple[list[SourceFile], dict[str, list[str]]]:
    """Find the source files in scope.
    Args:
        root: The repo root.
        config: The loaded config (src, exclude, extensions, risk_markers).
    Returns:
        The files, and the skipped paths by reason (vendored, generated, unreadable).
    """
    t = tables()
    ext_map = extension_map(tuple(sorted(config.get("extensions", {}).items())))
    extra_risk = {lang: tuple(sorted(cats.items())) for lang, cats in config.get("risk_markers", {}).items()}
    vendored = set(t["vendored_dirs"])
    markers = t["generated_markers"]
    suffixes = tuple(t["generated_suffixes"])
    srcs = [_norm_prefix(s) for s in config["src"]]
    excludes = [_norm_prefix(x) for x in config["exclude"]]
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    rels = [r for r in listed.split("\0") if r] if listed is not None else walk(root, vendored)
    files: list[SourceFile] = []
    skipped: dict[str, list[str]] = defaultdict(list)
    for rel in sorted(set(rels)):
        lang = ext_map.get(PurePosixPath(rel).suffix.lower())
        if lang is None or not under(rel, srcs) or (excludes and under(rel, excludes)):
            continue
        if any(part in vendored or part.startswith(".") for part in rel.split("/")[:-1]):
            skipped["vendored"].append(rel)
            continue
        if rel.lower().endswith(suffixes):
            skipped["generated"].append(rel)
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped["unreadable"].append(rel)
            continue
        if any(m in text[:SNIFF_CHARS] for m in markers):
            skipped["generated"].append(rel)
            continue
        files.append(SourceFile(rel, root / rel, lang, text, extra_risk=extra_risk.get(lang, ())))
    return files, dict(skipped)


def load_config(root: Path, path: Path | None) -> dict:
    """Load the project config over the defaults.
    Args:
        root: The repo root.
        path: An explicit config path, or None for <root>/.claude/checkup/config.json when present.
    Returns:
        The merged config.
    """
    config = json.loads(json.dumps(CONFIG_DEFAULTS))
    candidate = path or root / CONFIG_RELPATH
    if candidate.is_file():
        config.update(json.loads(candidate.read_text(encoding="utf-8")))
    elif path is not None:
        raise SystemExit(f"config not found: {path}")
    return config


def framework_names(config: dict) -> set[str]:
    """Collect the names a runtime or framework calls by itself (never dead code).
    Args:
        config: The loaded config; its "framework_names" extend the table's.
    Returns:
        The names.
    """
    return set(tables()["framework_names"]) | set(config.get("framework_names", []))


def add_common_args(ap: argparse.ArgumentParser) -> None:
    """Add the arguments every script shares.
    Args:
        ap: The parser to extend.
    """
    ap.add_argument("--root", type=Path, default=Path(), help="repo root (default: the current directory)")
    ap.add_argument("--config", type=Path,
                    help=f"project config (default: <root>/{CONFIG_RELPATH} when it exists)")
    ap.add_argument("--src", nargs="+", help="source directories; overrides the config")
    ap.add_argument("--exclude", nargs="*", help="path prefixes to skip; overrides the config")
    ap.add_argument("--tests", nargs="*", help="test directories; overrides the config")


@dataclass
class Context:
    """What a script works on: the root, the config and the files in scope."""

    root: Path
    config: dict
    files: list[SourceFile]
    skipped: dict[str, list[str]]

    @property
    def data_root(self) -> Path:
        """Return the folder that holds every run and the cross-run files.
        Returns:
            <root>/<config data_root>.
        """
        return self.root / self.config["data_root"]


def load(args: argparse.Namespace) -> Context:
    """Resolve the root and config from the common arguments and discover the files.
    Args:
        args: Parsed arguments from a parser built with add_common_args.
    Returns:
        The context.
    """
    root = args.root.resolve()
    config = load_config(root, args.config)
    if args.src:
        config["src"] = args.src
    if args.exclude is not None:
        config["exclude"] = args.exclude
    if args.tests is not None:
        config["tests"] = args.tests
    files, skipped = discover(root, config)
    return Context(root, config, files, skipped)


def write_json(path: Path, data: object) -> None:
    """Write JSON, creating the folder.
    Args:
        path: The output file.
        data: A JSON-ready value.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
