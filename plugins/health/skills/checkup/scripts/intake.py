"""Intake for a checkup: draft a project's .claude/checkup/config.json and seed.md from evidence in the repo.
Nothing is written to the repo. The drafts, the evidence and the questions the evidence cannot settle go to
--out; the main loop fills gaps from CLAUDE.md and the docs, asks the user those few questions, and writes
the two files on the user's OK. --check validates an existing config and reports what changed since intake.

    python intake.py --out <scratch>/intake
    python intake.py --check
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import inventory as inv

TEST_DIR_NAMES = {"test", "tests", "spec", "specs", "__tests__", "testing", "e2e", "integration_tests"}
TEST_FILE_RX = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|Tests?\.(java|cs|kt)$)")
AUX_DIR_RX = re.compile(r"(?i)^(\..*|docs?|examples?|samples?|scripts?|\w*tools?|benchmarks?|bench|perf|demos?|"
                        r"fixtures|assets|resources|data)$")
SCRATCH_DIR_NAMES = ("WORK", "work", "scratch", "tmp", "temp", "sandbox")
DEFAULT_DATA_ROOT = ".checkup"
HEADER_LINES = 40
LICENSE_RX = re.compile(r"(?i)copyright\s*(?:\(c\)|©)?\s*(?:\d{4}[-–,\s\d]*)?(?P<holder>[^\n*#/]*)"
                        r"|licensed under|spdx-license-identifier|mit license|apache license|general public license")
OWN_HEADER_SHARE = 0.2     # when fewer files than this carry a license header, every headed file looks vendored
CATEGORY_WORDS = [
    ("async", r"thread|timer|worker|executor|future|pool|task|queue|runnable|scheduler|lock|mutex|semaphore"),
    ("events", r"signal|slot|listener|emitter|observer|subscri|dispatch"),
    ("db", r"session|transaction|connection|query"),
    ("state", r"cache|store|registry"),
    ("io", r"socket|stream|upload|download|client"),
]
MIN_MARKER_USES = 3
MAX_MARKERS = 8
LAYER_MAX_SHARE = 0.25     # an external package used by at most this share of packages is a layering candidate
TOP_CANDIDATES = 6
LIFECYCLE_RX = re.compile(r"(?i)^_*(on_?)?(switch|leav|release|reset|clear|teardown|close|remove|delete|forget|undo|"
                          r"redo|paste|cancel|shutdown|stop|unload|dispose|destroy|abort)"
                          r"|(finished|failed|cancelled|canceled|closed|done)$")
WRITE_RX = re.compile(r"(?i)^_*(save|write|export|flush|commit|store|persist|move|rename|import|insert|dump|upload)")
MAX_NAMED = 25
KNOWN_DOC_RX = re.compile(r"(?i)backlog|todo|roadmap|known[ _-]?(gaps|issues|limitations|bugs)|tech[ _-]?debt|limitations")
DECISION_DOC_RX = re.compile(r"(?i)\bdecisions?\b|\badrs?\b")
SPEC_DOC_RX = re.compile(r"(?i)architecture|design|spec")
DOC_SUFFIXES = (".md", ".rst")
SKIP_DOC_NAMES = re.compile(r"(?i)^(license|licence|copying|notice|changelog|changes|history|code_of_conduct)$")
SKIP_DOC_DIRS = re.compile(r"(?i)^(\.claude|\.github|archive|archived|old|deprecated|attic|node_modules)$")
FOREIGN_STYLE_RX = re.compile(r"[a-z][A-Z]")   # camelCase in a snake_case codebase: an override of a foreign API
AGENT_DIR = PurePosixPath(".claude/agents")
DRIFT_AGENT_RX = re.compile(r"(?i)drift|spec|docs?\b")
UI_CLASS_RX = re.compile(r"\bclass\s+\w*(Widget|Window|Dialog|Panel|View|Screen|Page|Component)\b")
SERVICE_RX = re.compile(r"@\w+\.(get|post|put|patch|delete|route)\(|\b(router|endpoint|middleware)\b|\bHandleFunc\(")
CLI_RX = re.compile(r"__name__\s*==\s*['\"]__main__['\"]|\bsys\.argv\b|\bprocess\.argv\b|\bargparse\b"
                    r"|\bfunc main\(|\bfn main\(")
GPU_RX = re.compile(r"(?i)\b(cuda|gpu|opencl|vulkan)\b")
VENVS = (".venv/Scripts/python.exe", ".venv/bin/python", "venv/Scripts/python.exe", "venv/bin/python")
JS_BUILTINS = {"fs", "path", "events", "child_process", "http", "https", "net", "os", "stream", "worker_threads",
               "url", "util", "crypto", "assert", "buffer", "timers", "zlib", "readline", "process"}
JS_NAMED_IMPORT_RX = re.compile(r"import\s+(?:\w+\s*,\s*)?\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
RUNNER_RX = re.compile(r"(?i)^(run_?all|run_?tests|runtests)\w*\.py$")
BENCH_RX = re.compile(r"(?i)(^|[/_])(bench|perf)")
TOOL_SECTION_RX = re.compile(r"^\[tool\.([\w-]+)", re.MULTILINE)
TOOL_CONFIG_RX = re.compile(r"^\.?[\w-]+(rc(\.\w+)?|\.config\.[cm]?[jt]s|\.toml|\.ya?ml|\.cfg|\.ini)$")
NOT_TOOL_CONFIGS = re.compile(r"(?i)^(pyproject\.toml|cargo\.toml|package(-lock)?\.json|.*lock.*|\.gitlab-ci\.ya?ml|"
                              r"docker-compose\.ya?ml|mkdocs\.ya?ml|codecov\.ya?ml)$")
MAKE_TARGETS = {"test": "test", "check": "test", "lint": "lint", "bench": "bench", "perf": "bench"}
SEED_TEMPLATE = """# Checkup seed: {name}

Drafted by intake.py on {date}. Keep what is true, fix what is not, and delete this line.

## Profiles
{profiles}

## Run rules
- Interpreter: {interpreter}
- How an agent runs a snippet headless, builds temp data, and which single test it may run: {run_rules}
- Never: {never}

## Lifecycle events
{lifecycle}

## Write paths
{writes}

## Hot paths and measurement
{hot}

## Established facts and decisions (not findings)
{facts}
"""


def tracked(root: Path) -> set[str] | None:
    """List the files git tracks (untracked scratch files say nothing about the project).
    Args:
        root: The repo root.
    Returns:
        Repo-relative paths, or None outside a git repo.
    """
    out = inv.git(root, "ls-files", "-z", "--cached")
    return None if out is None else {r for r in out.split("\0") if r}


def is_test_path(rel: str) -> bool:
    """Tell whether a path is test code by folder or file name.
    Args:
        rel: A repo-relative path.
    Returns:
        True for test code.
    """
    parts = rel.split("/")
    return any(p.lower() in TEST_DIR_NAMES for p in parts[:-1]) or bool(TEST_FILE_RX.search(parts[-1]))


def layout(files: list[inv.SourceFile]) -> dict:
    """Split the repo into source, test and auxiliary folders (tooling, docs, examples, dot folders).
    Args:
        files: Every tracked source file.
    Returns:
        src and tests prefixes, auxiliary folders with code, and lines per top-level folder.
    """
    lines: Counter = Counter()
    test_dirs: set[str] = set()
    for sf in files:
        parts = sf.rel.split("/")
        if is_test_path(sf.rel):
            idx = next((i for i, p in enumerate(parts[:-1]) if p.lower() in TEST_DIR_NAMES), None)
            if idx is not None:
                test_dirs.add("/".join(parts[:idx + 1]))
            continue
        lines[parts[0] if len(parts) > 1 else "."] += sf.lines
    src = sorted(d for d in lines if d != "." and not AUX_DIR_RX.match(d)) or ["."]
    aux = {d: n for d, n in lines.items() if d not in src and d != "."}
    tests = sorted(d for d in test_dirs if not any(d.startswith(t + "/") for t in test_dirs if t != d))
    return {"src": src, "tests": tests, "other_code": dict(sorted(aux.items(), key=lambda kv: -kv[1])),
            "lines_by_folder": dict(lines.most_common())}


def vendored(files: list[inv.SourceFile]) -> list[dict]:
    """Find files whose header carries a license or copyright notice unlike the project's own.
    Args:
        files: Source files in scope.
    Returns:
        Candidates to exclude, largest first.
    """
    headed: dict[str, str] = {}
    for sf in files:
        head = "\n".join(sf.text.splitlines()[:HEADER_LINES])
        m = LICENSE_RX.search(head)
        if m:
            headed[sf.rel] = " ".join((m.group("holder") or "").split()).lower()
    if not headed:
        return []
    lines = {sf.rel: sf.lines for sf in files}
    if len(headed) > OWN_HEADER_SHARE * len(files):
        own = Counter(h for h in headed.values() if h).most_common(1)
        headed = {r: h for r, h in headed.items() if not own or h != own[0][0]}
    return sorted(({"file": r, "lines": lines[r], "notice": h or "license text"} for r, h in headed.items()),
                  key=lambda c: -c["lines"])[:TOP_CANDIDATES * 2]


def external_imports(sf: inv.SourceFile, internal_tops: set[str]) -> list[tuple[str, tuple[str, ...]]]:
    """List a file's imports of external (non-repo, non-standard-library) packages.
    Args:
        sf: The source file.
        internal_tops: First components of the repo's own module names.
    Returns:
        (package, imported names) pairs; names only where the syntax lists them.
    """
    out = []
    if sf.lang == inv.PYTHON:
        for target, _, names in inv.import_targets(sf):
            top = target.split(".")[0]
            if top and top not in internal_tops and top not in sys.stdlib_module_names:
                out.append((top, names))
    elif sf.lang == "javascript":
        named = {m.group(2): tuple(n.split(" as ")[0].strip() for n in m.group(1).split(",") if n.strip())
                 for m in JS_NAMED_IMPORT_RX.finditer(sf.text)}
        for target, _, _ in inv.import_targets(sf):
            if target.startswith((".", "/", "node:")):
                continue
            pkg = "/".join(target.split("/")[:2]) if target.startswith("@") else target.split("/")[0]
            if pkg not in JS_BUILTINS:
                out.append((pkg, named.get(target, ())))
    return out


def framework_callbacks(files: list[inv.SourceFile]) -> list[dict]:
    """Find methods a framework calls by itself: public methods on classes with an external base, never referenced.
    Args:
        files: Source files in scope.
    Returns:
        Names with the number of classes defining them, one example site and "likely": in a codebase that names
        methods in snake_case, a camelCase one overrides a foreign API. Likely ones first (Python only).
    """
    internal = {n.name for sf in files if sf.tree for n in ast.walk(sf.tree) if isinstance(n, ast.ClassDef)}
    skip_bases = internal | set(dir(builtins))
    tokens: Counter = Counter()
    defs: Counter = Counter()
    sites: dict[str, list[str]] = defaultdict(list)
    for sf in files:
        tokens.update(inv.TOKEN_RX.findall(sf.text))
        if sf.tree is None:
            continue
        for node in ast.walk(sf.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "" for b in node.bases]
            external = any(b and b not in skip_bases for b in bases)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    defs[item.name] += 1
                    if external:
                        sites[item.name].append(f"{sf.rel}:{item.lineno}")
    snake = sum(1 for n in defs if not FOREIGN_STYLE_RX.search(n)) > len(defs) / 2
    out = [{"name": n, "classes": len(s), "example": s[0],
            "likely": not snake or bool(FOREIGN_STYLE_RX.search(n))}
           for n, s in sites.items() if tokens[n] <= defs[n]]
    return sorted(out, key=lambda c: (not c["likely"], -c["classes"], c["name"]))


def marker_suggestions(files: list[inv.SourceFile], internal_tops: set[str]) -> dict[str, dict[str, str]]:
    """Suggest project risk markers: external names whose words say thread, signal, session, cache and the like.
    Args:
        files: Source files in scope.
        internal_tops: First components of the repo's own module names.
    Returns:
        Language to category to regex, for names used at least MIN_MARKER_USES times and not already covered.
    """
    names: dict[str, set[str]] = defaultdict(set)
    for sf in files:
        for _, imported in external_imports(sf, internal_tops):
            names[sf.lang].update(n for n in imported if n and n != "*")
    text = {lang: "\n".join(sf.masked for sf in files if sf.lang == lang) for lang in names}
    out: dict[str, dict[str, str]] = {}
    for lang, found in names.items():
        table = inv.risk_patterns(lang)
        by_cat: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for name in found:
            cat = next((c for c, words in CATEGORY_WORDS if re.search(words, name, re.IGNORECASE)), None)
            if cat is None or (cat in table and table[cat].search(name)):
                continue
            uses = len(re.findall(rf"\b{re.escape(name)}\b", text[lang]))
            if uses >= MIN_MARKER_USES:
                by_cat[cat].append((uses, name))
        cats = {c: "|".join(rf"\b{re.escape(n)}\b" for _, n in sorted(v, reverse=True)[:MAX_MARKERS])
                for c, v in by_cat.items()}
        if cats:
            out[lang] = dict(sorted(cats.items()))
    return out


def layering(files: list[inv.SourceFile], internal_tops: set[str]) -> dict:
    """Find today's one-way dependencies, as candidate layering rules for the user to confirm.
    Args:
        files: Source files in scope.
        internal_tops: First components of the repo's own module names.
    Returns:
        External packages confined to a few packages, and internal package pairs imported one way only.
    """
    packages = sorted({inv.package_of(sf.rel) for sf in files} - {"."})
    use: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for sf in files:
        for ext, _ in external_imports(sf, internal_tops):
            use[ext][inv.package_of(sf.rel)].add(sf.rel)
    confined = []
    for ext, by_pkg in use.items():
        n_files = sum(len(v) for v in by_pkg.values())
        if n_files >= 2 and len(by_pkg) <= max(1, int(LAYER_MAX_SHARE * len(packages))):
            confined.append({"import": ext, "used_in": sorted(by_pkg), "files": n_files,
                             "rule_from": [p for p in packages if p not in by_pkg]})
    confined.sort(key=lambda c: -c["files"])
    graph = inv.import_graph([sf for sf in files if sf.lang == inv.PYTHON])
    edges: Counter = Counter()
    for a, targets in graph.items():
        for b in targets:
            pa, pb = inv.package_of(a), inv.package_of(b)
            if pa != pb and "." not in (pa, pb):
                edges[(pa, pb)] += 1
    one_way = [{"from": b, "import": a.replace("/", "."), "evidence": f"{a} imports {b} in {n} places; never the reverse"}
               for (a, b), n in edges.most_common() if edges[(b, a)] == 0]
    return {"confined_externals": confined[:TOP_CANDIDATES], "one_way_internal": one_way[:TOP_CANDIDATES]}


def named_functions(files: list[inv.SourceFile], rx: re.Pattern, need_io: bool = False) -> list[dict]:
    """List functions whose names match a pattern, ranked by call sites.
    Args:
        files: Source files in scope.
        rx: The name pattern.
        need_io: Only files with file or database markers.
    Returns:
        Name, where and call count, most called first.
    """
    calls: Counter = Counter()
    for sf in files:
        calls.update(inv.CALL_RX.findall(sf.masked))
    found = []
    for sf in files:
        if need_io:
            markers = inv.risk_markers(sf)
            if not markers.get("io") and not markers.get("db"):
                continue
        if sf.tree is not None:
            defs = [(n.name, n.lineno) for n in ast.walk(sf.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        elif sf.lang != inv.PYTHON:
            defs = [(f["name"], f["line"]) for f in inv.brace_functions(sf)]
        else:
            defs = []
        found += [{"name": n, "where": f"{sf.rel}:{line}", "calls": calls[n] - 1} for n, line in defs if rx.search(n)]
    return sorted(found, key=lambda f: (-f["calls"], f["name"]))[:MAX_NAMED]


def commands(root: Path, rels: set[str], tests: list[str]) -> dict:
    """Detect test, lint and bench commands from generic sources: package scripts, Makefile targets, the
    language toolchain, and runner or bench scripts. Tools configured in the repo are listed by name as found
    (pyproject [tool.*] sections, root config files), for the main loop to turn into commands.
    Args:
        root: The repo root.
        rels: Tracked (or listed) repo-relative paths.
        tests: Test folders.
    Returns:
        The interpreter, candidate commands per kind, and the configured tools.
    """
    found: dict[str, list[str]] = defaultdict(list)
    interpreter = next((v for v in VENVS if (root / v).is_file()), "")
    py = interpreter or "python"
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        found["tools"] += TOOL_SECTION_RX.findall(pyproject.read_text(encoding="utf-8", errors="replace"))
    found["tools"] += sorted(r for r in rels if "/" not in r and TOOL_CONFIG_RX.match(r) and not NOT_TOOL_CONFIGS.match(r))
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        for name in scripts:
            kind = "test" if name.startswith("test") else "lint" if name.startswith("lint") else \
                "bench" if BENCH_RX.search(name) else None
            if kind:
                found[kind].append(f"npm run {name}")
    make = root / "Makefile"
    if make.is_file():
        targets = re.findall(r"^([A-Za-z][\w-]*)\s*:", make.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        for t in targets:
            if t in MAKE_TARGETS:
                found[MAKE_TARGETS[t]].append(f"make {t}")
    if (root / "Cargo.toml").is_file():
        found["test"].append("cargo test")
    if (root / "go.mod").is_file():
        found["test"].append("go test ./...")
        found["lint"].append("go vet ./...")
    if (root / "CMakeLists.txt").is_file():
        found["test"].append("ctest")
    for rel in sorted(rels):
        p = PurePosixPath(rel)
        if any(inv.under(rel, [t]) for t in tests) and RUNNER_RX.match(p.name):
            found["test"].insert(0, f"{py} {rel}")
        elif p.suffix == ".py" and BENCH_RX.search(p.stem) and "/" in rel:
            found["bench"].append(f"{py} {rel}")
    return {"interpreter": interpreter, **{k: list(dict.fromkeys(v)) for k, v in found.items()}}


def docs(root: Path, rels: set[str]) -> dict:
    """Find the docs that hold known items, decisions and the architecture.
    Args:
        root: The repo root.
        rels: Tracked (or listed) repo-relative paths.
    Returns:
        known, decisions and specs as "path", "folder/" or "path (Heading)" entries.
    """
    out: dict[str, list[str]] = {"known": [], "decisions": [], "specs": []}
    adr_dirs: set[str] = set()
    for rel in sorted(r for r in rels if r.lower().endswith(DOC_SUFFIXES)):
        p = PurePosixPath(rel)
        if SKIP_DOC_NAMES.match(p.stem) or any(SKIP_DOC_DIRS.match(part) for part in p.parent.parts):
            continue
        if any(DECISION_DOC_RX.search(part) for part in p.parent.parts):
            adr_dirs.add(p.parent.as_posix())
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        headings = [ln.lstrip("#").strip() for ln in text.splitlines() if ln.startswith("#")][1:]
        for kind, rx in (("known", KNOWN_DOC_RX), ("decisions", DECISION_DOC_RX), ("specs", SPEC_DOC_RX)):
            if rx.search(p.stem):
                out[kind].append(rel)
            elif kind != "specs":
                out[kind] += [f"{rel} ({h})" for h in headings if rx.search(h)][:2]
    out["decisions"] += sorted(f"{d}/" for d in adr_dirs)
    return out


def agents(root: Path, specs: list[str]) -> list[dict]:
    """Suggest the project's own drift-checking agents as extras.
    Args:
        root: The repo root.
        specs: Architecture or spec docs, for the brief.
    Returns:
        Extra assignments for the config.
    """
    folder = root / AGENT_DIR
    out = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        head = path.read_text(encoding="utf-8", errors="replace")
        name = re.search(r"(?m)^name:\s*(\S+)", head)
        desc = re.search(r"(?m)^description:\s*(.+)$", head)
        if name and desc and DRIFT_AGENT_RX.search(desc.group(1)):
            target = specs[0] if specs else "the design docs"
            out.append({"id": "spec", "name": "Spec drift", "agentType": name.group(1),
                        "brief": f"Report drift between {target} and the code at HEAD."})
    return out[:1]


def data_root(root: Path) -> dict:
    """Pick the folder for checkup runs and say whether git ignores it.
    Args:
        root: The repo root.
    Returns:
        The path and its ignore status.
    """
    base = next((d for d in SCRATCH_DIR_NAMES if (root / d).is_dir()), None)
    path = f"{base}/checkup" if base else DEFAULT_DATA_ROOT
    ignored = inv.git(root, "check-ignore", "-q", f"{path}/run.json") is not None
    return {"path": path, "ignored": ignored}


def profile_signals(files: list[inv.SourceFile], root: Path, callbacks: list[dict]) -> dict:
    """Count the signals that suggest an app profile.
    Args:
        files: Source files in scope.
        root: The repo root.
        callbacks: Framework callbacks found.
    Returns:
        Signal counts, the suggested profiles, and whether the choice is clear.
    """
    ui = sum(len(UI_CLASS_RX.findall(sf.text)) for sf in files) + sum(c["classes"] for c in callbacks
                                                                     if c["name"].endswith("Event"))
    service = sum(len(SERVICE_RX.findall(sf.text)) for sf in files)
    cli = sum(len(CLI_RX.findall(sf.text)) for sf in files)
    gpu = sum(len(GPU_RX.findall(sf.masked)) for sf in files)
    lib = False
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        lib = "[project]" in text and "[project.scripts]" not in text and "[project.gui-scripts]" not in text
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            meta = json.loads(pkg.read_text(encoding="utf-8"))
            lib = lib or (("main" in meta or "exports" in meta) and "bin" not in meta)
        except json.JSONDecodeError:
            pass
    scores = {"interactive": ui, "service": service, "batch": cli, "library": 10 if lib else 0}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top = ranked[0][1]
    picks = [p for p, s in ranked if s and s >= max(3, 0.5 * top)] or ["batch"]
    return {"signals": {**scores, "gpu_mentions": gpu}, "suggested": picks, "clear": len(picks) == 1}


def draft(root: Path, out: Path) -> int:
    """Gather the evidence and write the drafts and questions to out.
    Args:
        root: The repo root.
        out: A scratch folder outside the repo (or ignored by git).
    Returns:
        The process exit code.
    """
    config = json.loads(json.dumps(inv.CONFIG_DEFAULTS))
    all_files, _ = inv.discover(root, config)
    keep = tracked(root)
    listed = keep if keep is not None else set(inv.walk(root, set(inv.tables()["vendored_dirs"])))
    all_files = [f for f in all_files if f.rel in listed]
    if not all_files:
        sys.stderr.write("no source files found\n")
        return 1
    lay = layout(all_files)
    files = [f for f in all_files if inv.under(f.rel, [s if s != "." else "" for s in lay["src"]])
             and not is_test_path(f.rel)]
    internal_tops = {f.module.split(".")[0] for f in all_files if f.lang == inv.PYTHON} | {f.rel.split("/")[0] for f in all_files}
    callbacks = framework_callbacks(files)
    markers = marker_suggestions(files, internal_tops)
    layers = layering(files, internal_tops)
    cmds = commands(root, listed, lay["tests"])
    doc = docs(root, listed)
    extras = agents(root, doc["specs"])
    where = data_root(root)
    prof = profile_signals(files, root, callbacks)
    vend = vendored(files)
    lifecycle = named_functions(files, LIFECYCLE_RX)
    writes = named_functions(files, WRITE_RX, need_io=True)

    config_draft = {
        "src": lay["src"], "exclude": [], "tests": lay["tests"], "data_root": where["path"],
        "profiles": prof["suggested"], "framework_names": [c["name"] for c in callbacks if c["likely"]], "risk_markers": markers,
        "forbidden_imports": [],
        "commands": {k: v[0] for k, v in cmds.items() if k not in ("interpreter", "tools") and v},
        "exclusive_resources": ["GPU (confirm: one job at a time?)"] if prof["signals"]["gpu_mentions"] else [],
        "known_docs": doc["known"], "decision_docs": doc["decisions"], "extras": extras, "history_months": 12,
    }
    questions = [{"id": "run_limits", "ask": "What must agents never run or touch (the app, the full suite, "
                                             "exclusive resources, network)?", "skip_if": "CLAUDE.md already says"}]
    if layers["confined_externals"] or layers["one_way_internal"]:
        questions.append({"id": "layering", "ask": "Which of these one-way dependencies are rules?",
                          "candidates": layers})
    if not prof["clear"]:
        questions.append({"id": "profiles", "ask": "Which profiles describe the app?", "candidates": prof["suggested"]})
    if vend:
        questions.append({"id": "exclude", "ask": "Which of these look vendored and should be skipped?", "candidates": vend})
    bullet = lambda items: "\n".join(f"- {i['name']} @ {i['where']} (calls {i['calls']})" for i in items) or "- (none found)"
    seed = SEED_TEMPLATE.format(
        name=root.name, date=datetime.now(timezone.utc).astimezone().date().isoformat(),
        profiles=", ".join(prof["suggested"]) + f" (signals: {json.dumps(prof['signals'])})",
        interpreter=cmds.get("interpreter") or "(not found)", run_rules="(from CLAUDE.md, or ask)",
        never="(from CLAUDE.md, or the interview)",
        lifecycle="Candidates by name, most called first; keep the real ones and add what is missing.\n" + bullet(lifecycle),
        writes="Candidates in files that do file or database work; keep the real ones.\n" + bullet(writes),
        hot="- Bench commands: " + (", ".join(cmds.get("bench", [])[:5]) or "(none found)") + "\n- Hot paths: (from the docs, or ask)",
        facts="- (from CLAUDE.md and the docs: tuned values and deliberate choices a reviewer would otherwise flag)")
    evidence = {"layout": lay, "commands": cmds, "docs": doc, "framework_callbacks": callbacks,
                "risk_markers": markers, "layering": layers, "vendored": vend, "profiles": prof, "data_root": where,
                "lifecycle_candidates": lifecycle, "write_candidates": writes, "questions": questions}
    inv.write_json(out / "config.json", config_draft)
    (out / "seed.md").write_text(seed, encoding="utf-8")
    inv.write_json(out / "intake.json", evidence)

    w = sys.stdout.write
    w(f"src {lay['src']}; tests {lay['tests']}; other code: {lay['other_code'] or 'none'}\n")
    w(f"profiles {prof['suggested']} ({'clear' if prof['clear'] else 'ask'}); signals {prof['signals']}\n")
    w(f"commands: {json.dumps({k: v for k, v in cmds.items() if v})}\n")
    likely = [c["name"] for c in callbacks if c["likely"]]
    w(f"framework callbacks: {len(likely)} likely ({', '.join(likely[:8])}{' ...' if len(likely) > 8 else ''}), "
      f"{len(callbacks) - len(likely)} possible (in intake.json)\n")
    w(f"risk markers: {json.dumps(markers)}\n")
    for c in layers["confined_externals"]:
        w(f"layering candidate: {c['import']} only in {', '.join(c['used_in'])} ({c['files']} files)\n")
    for c in layers["one_way_internal"]:
        w(f"layering candidate: {c['from']} must not import {c['import']} ({c['evidence']})\n")
    w(f"vendored candidates: {[v['file'] for v in vend] or 'none'}\n")
    w(f"docs: known {doc['known']}; decisions {doc['decisions']}; specs {doc['specs'][:5]}\n")
    w(f"extras: {[e['agentType'] for e in extras] or 'none'}; data_root {where['path']} "
      f"({'ignored' if where['ignored'] else 'NOT ignored by git'})\n")
    w(f"questions for the user: {[q['id'] for q in questions]}\ndrafts in {out}\n")
    return 0


def check(root: Path, path: Path | None) -> int:
    """Validate an existing config and report drift since intake.
    Args:
        root: The repo root.
        path: The config path, or None for the default.
    Returns:
        1 when the config has errors, else 0 (drift is reported, not fatal).
    """
    config_path = path or root / inv.CONFIG_RELPATH
    if not config_path.is_file():
        sys.stderr.write(f"no config at {config_path}: run intake first\n")
        return 1
    config = inv.load_config(root, config_path)
    errors, drift = [], []
    for key in ("src", "tests"):
        errors += [f"{key} path missing: {p}" for p in config.get(key, []) if p not in (".", "") and not (root / p).exists()]
    drift += [f"exclude path gone: {p}" for p in config.get("exclude", []) if not (root / p).exists()]
    for lang, cats in config.get("risk_markers", {}).items():
        for cat, rx in cats.items():
            try:
                re.compile(rx)
            except re.error as exc:
                errors.append(f"risk_markers {lang}.{cat} does not compile: {exc}")
    errors += [f"forbidden_imports 'from' missing: {r['from']}" for r in config.get("forbidden_imports", [])
               if not (root / r["from"]).exists()]
    for key in ("known_docs", "decision_docs"):
        drift += [f"{key} gone: {d}" for d in config.get(key, []) if not any(root.glob(d.split(" (")[0].rstrip("/")))]
    if inv.git(root, "check-ignore", "-q", f"{config['data_root']}/run.json") is None:
        drift.append(f"data_root {config['data_root']} is not ignored by git")
    files, _ = inv.discover(root, dict(config, src=["."], exclude=[]))
    keep = tracked(root)
    files = [f for f in files if keep is None or f.rel in keep]
    lay = layout(files)
    covered = config.get("src", []) + config.get("tests", []) + config.get("exclude", [])
    drift += [f"source folder not in src, tests or exclude: {d}" for d in lay["src"]
              if d != "." and not inv.under(d, covered) and "." not in covered]
    in_scope = [f for f in files if inv.under(f.rel, [s if s != "." else "" for s in config["src"]])]
    known_names = set(config.get("framework_names", []))
    new_cb = [c["name"] for c in framework_callbacks(in_scope) if c["likely"] and c["name"] not in known_names]
    if new_cb:
        drift.append(f"framework callbacks not in framework_names: {', '.join(new_cb[:10])}")
    listed = keep if keep is not None else set(inv.walk(root, set(inv.tables()["vendored_dirs"])))
    doc = docs(root, listed)
    listed_docs = [d.split(" (")[0] for d in config.get("known_docs", []) + config.get("decision_docs", [])]
    new_docs = sorted({d.split(" (")[0] for d in doc["known"] + doc["decisions"]
                       if not any(fnmatch(d.split(" (")[0], pat) for pat in listed_docs)})
    if new_docs:
        drift.append(f"docs that may hold known items or decisions: {', '.join(new_docs[:8])}")
    w = sys.stdout.write
    for e in errors:
        w(f"ERROR  {e}\n")
    for d in drift:
        w(f"drift  {d}\n")
    if not errors and not drift:
        w("config ok, no drift\n")
    return 1 if errors else 0


def main() -> int:
    """Parse arguments and draft or check.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(), help="repo root (default: the current directory)")
    ap.add_argument("--config", type=Path, help="the config to check (default: <root>/.claude/checkup/config.json)")
    ap.add_argument("--out", type=Path, help="folder for the drafts and evidence (outside the repo, or ignored)")
    ap.add_argument("--check", action="store_true", help="validate the existing config and report drift")
    args = ap.parse_args()
    root = args.root.resolve()
    if args.check:
        return check(root, args.config)
    if not args.out:
        ap.error("--out is required unless --check")
    return draft(root, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
