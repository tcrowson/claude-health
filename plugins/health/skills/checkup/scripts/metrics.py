"""Objective code-health metrics for a source tree, with a trend against a previous run.
Python is measured exactly from its syntax tree: function length and complexity, type-hint and
docstring coverage, import cycles, exception smells and pass-through functions. The other languages
in languages.json are measured approximately from tokens: function length and complexity and
swallowed errors. For every language: dead-code candidates, fan-in of public names, forbidden
imports from the config, and risky files no test mentions. Deterministic, so two runs compare.

    python metrics.py --out <run>/metrics.json [--compare <prev>/metrics.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path

import inventory as inv

BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.AsyncWith,
                ast.IfExp, ast.Assert, ast.match_case)
FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
MIN_NAME = 4             # shorter public names collide with builtins and locals too often to count
TODO_WORDS = r"\b(?:TODO|FIXME|XXX|HACK)\b"
LENGTHS = (60, 100, 200)
TOP_N = 15
DEAD_LIST = 60
PASS_LIST = 40
SINGLE_USE_LIST = 40
RISKY_TOP = 40
BROAD_EXCEPTIONS = ("Exception", "BaseException")


@cache
def todo_pattern(lang: str) -> re.Pattern:
    """Build the regex that finds TODO-style markers in a language's comments.
    Args:
        lang: The language key.
    Returns:
        The compiled pattern (one match per comment line).
    """
    spec = inv.language(lang)
    starts = [re.escape(c) for c in spec.get("line_comment", [])]
    starts += [re.escape(a) for a, _ in spec.get("block_comment", [])]
    return re.compile(f"(?:{'|'.join(starts) or '#'}).*{TODO_WORDS}")


def complexity(node: ast.AST) -> int:
    """Approximate the cyclomatic complexity of a Python function.
    Args:
        node: The function node.
    Returns:
        1 plus one per branch, extra boolean operand and comprehension condition.
    """
    score = 1
    for sub in ast.walk(node):
        if isinstance(sub, BRANCH_NODES):
            score += 1
        elif isinstance(sub, ast.BoolOp):
            score += len(sub.values) - 1
        elif isinstance(sub, ast.comprehension):
            score += len(sub.ifs)
    return score


def fully_hinted(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Tell whether every parameter (bar self / cls) and the return are annotated.
    Args:
        fn: The function node.
    Returns:
        True when the signature is fully annotated.
    """
    a = fn.args
    params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    if params and params[0].arg in ("self", "cls"):
        params = params[1:]
    params += [p for p in (a.vararg, a.kwarg) if p is not None]
    return fn.returns is not None and all(p.annotation is not None for p in params)


def is_pass_through(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Tell whether a function only forwards its own parameters to one call (the deletion test's easy case).
    Args:
        fn: The function node.
    Returns:
        True for an undecorated, non-dunder function whose whole body (after a docstring) is one call
        passing exactly its parameters.
    """
    if fn.decorator_list or (fn.name.startswith("__") and fn.name.endswith("__")):
        return False
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], (ast.Return, ast.Expr)):
        return False
    call = body[0].value
    if isinstance(call, ast.Await):
        call = call.value
    if not isinstance(call, ast.Call):
        return False
    a = fn.args
    params = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    params += [p.arg for p in (a.vararg, a.kwarg) if p is not None]
    passed = []
    for arg in call.args:
        node = arg.value if isinstance(arg, ast.Starred) else arg
        if not isinstance(node, ast.Name):
            return False
        passed.append(node.id)
    for kw in call.keywords:
        if not isinstance(kw.value, ast.Name) or (kw.arg is not None and kw.arg != kw.value.id):
            return False
        passed.append(kw.value.id)
    return sorted(passed) == sorted(params)


class Collector:
    """Accumulates the per-file measurements."""

    def __init__(self, framework: set[str]) -> None:
        """Start empty.
        Args:
            framework: Names a framework calls by itself.
        """
        self.framework = framework
        self.funcs: list[dict] = []
        self.classes: list[dict] = []
        self.smells: Counter = Counter()
        self.per_pkg: dict[str, Counter] = defaultdict(Counter)
        self.lines_per_pkg: Counter = Counter()
        self.tokens: Counter = Counter()
        self.defs: Counter = Counter()
        self.calls: Counter = Counter()
        self.def_calls: Counter = Counter()
        self.languages: dict[str, Counter] = defaultdict(Counter)
        self.pass_through: list[str] = []

    def python_file(self, sf: inv.SourceFile, pkg: str) -> None:
        """Measure one Python file from its syntax tree.
        Args:
            sf: The file.
            pkg: Its package key.
        """
        tree = sf.tree
        if tree is None:
            self.smells["syntax_errors"] += 1
            sys.stderr.write(f"skip {sf.rel}: does not parse\n")
            return
        for node in ast.walk(tree):
            if isinstance(node, FUNC_NODES):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                self.funcs.append({"where": f"{sf.rel}:{node.lineno}", "name": node.name, "lines": length,
                                   "cc": complexity(node)})
                self.defs[node.name] += 1
                self.def_calls[node.name] += 1
                c = self.per_pkg[pkg]
                c["functions"] += 1
                c["hinted"] += fully_hinted(node)
                c["docstring"] += ast.get_docstring(node) is not None
                if is_pass_through(node):
                    self.pass_through.append(f"{sf.rel}:{node.lineno} {node.name}")
            elif isinstance(node, ast.ClassDef):
                methods = sum(isinstance(b, FUNC_NODES) for b in node.body)
                self.classes.append({"where": f"{sf.rel}:{node.lineno}", "name": node.name,
                                     "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                                     "methods": methods})
                self.defs[node.name] += 1
                self.def_calls[node.name] += bool(node.bases or node.keywords)
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    self.smells["bare_except"] += 1
                elif isinstance(node.type, ast.Name) and node.type.id in BROAD_EXCEPTIONS:
                    self.smells["except_exception"] += 1
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    self.smells["except_pass"] += 1
            elif isinstance(node, ast.Global):
                self.smells["global"] += 1

    def other_file(self, sf: inv.SourceFile) -> None:
        """Measure one non-Python file approximately.
        Args:
            sf: The file.
        """
        found = inv.brace_functions(sf)
        for f in found:
            self.funcs.append({"where": f"{sf.rel}:{f['line']}", "name": f["name"], "lines": f["lines"],
                               "cc": f["cc"], "approximate": True})
            self.defs[f["name"]] += 1
            self.def_calls[f["name"]] += 1
        self.languages[sf.lang]["functions"] += len(found)
        swallow = inv.compiled(sf.lang, "swallow")
        if swallow:
            self.smells["empty_catch"] += len(swallow.findall(sf.masked))

    def add(self, sf: inv.SourceFile) -> None:
        """Measure one source file.
        Args:
            sf: The file.
        """
        pkg = inv.package_of(sf.rel)
        self.lines_per_pkg[pkg] += sf.lines
        self.languages[sf.lang]["files"] += 1
        self.languages[sf.lang]["lines"] += sf.lines
        self.tokens.update(inv.TOKEN_RX.findall(sf.text))
        self.calls.update(inv.CALL_RX.findall(sf.masked))
        self.smells["todo"] += len(todo_pattern(sf.lang).findall(sf.text))
        if sf.lang == inv.PYTHON:
            before = len(self.funcs)
            self.python_file(sf, pkg)
            self.languages[sf.lang]["functions"] += len(self.funcs) - before
        else:
            self.other_file(sf)


def test_files(ctx: inv.Context) -> list[inv.SourceFile]:
    """Load every source file under the configured test folders.
    Args:
        ctx: The context.
    Returns:
        The test files (none when no test folder is configured).
    """
    config = dict(ctx.config, src=ctx.config.get("tests", []), exclude=[])
    return inv.discover(ctx.root, config)[0] if config["src"] else []


def referenced_by_tests(sf: inv.SourceFile, tests: str) -> bool:
    """Tell whether the test code mentions a source file.
    Args:
        sf: The source file.
        tests: All test text.
    Returns:
        True for a dotted-module or from-import match (Python) or the file stem as a word (others).
    """
    stem = Path(sf.rel).stem
    if sf.lang == inv.PYTHON:
        mod = sf.module
        pkg = mod.rpartition(".")[0]
        if mod and mod in tests:
            return True
        return bool(pkg and re.search(rf"from\s+{re.escape(pkg)}\s+import\s+\(?[\w\s,]*\b{re.escape(stem)}\b", tests))
    return bool(re.search(rf"\b{re.escape(stem)}\b", tests))


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Find import cycles with Tarjan's strongly connected components.
    Args:
        graph: Node to the nodes it imports.
    Returns:
        Every component with more than one node, sorted.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    comps: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        """Run Tarjan's recursion from one node.
        Args:
            v: The node to visit.
        """
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                comps.append(sorted(comp))

    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    for v in sorted(graph):
        if v not in index:
            visit(v)
    return sorted(comps)


def forbidden(ctx: inv.Context) -> list[dict]:
    """Check the config's forbidden import rules.
    Args:
        ctx: The context; config "forbidden_imports" is a list of {"from": path prefix, "import": target}.
    Returns:
        One entry per violating import, with file:line.
    """
    out = []
    for rule in ctx.config.get("forbidden_imports", []):
        prefix, banned = rule["from"].rstrip("/"), rule["import"]
        for sf in ctx.files:
            if not inv.under(sf.rel, [prefix]):
                continue
            for target, line, _ in inv.import_targets(sf):
                if target == banned or target.startswith((banned + ".", banned + "/")):
                    out.append({"where": f"{sf.rel}:{line}", "import": target, "rule": f"{prefix} -/-> {banned}"})
    return out


def collect(ctx: inv.Context) -> dict:
    """Measure every source file and compute the metrics.
    Args:
        ctx: The context.
    Returns:
        The metrics as a JSON-ready dict.
    """
    col = Collector(inv.framework_names(ctx.config))
    for sf in ctx.files:
        col.add(sf)
    tfiles = test_files(ctx)
    tests = "\n".join(f.text for f in tfiles)
    for f in tfiles:
        col.tokens.update(inv.TOKEN_RX.findall(f.text))
        col.calls.update(inv.CALL_RX.findall(f.masked))
    by_rel = {sf.rel: sf for sf in ctx.files}
    graph = inv.import_graph(ctx.files, module_level_only=True)
    name = {rel: (sf.module if sf.lang == inv.PYTHON else rel) for rel, sf in by_rel.items()}
    named_graph = {name[r]: {name[t] for t in ts} for r, ts in graph.items()}
    importers = Counter(t for ts in inv.import_graph(ctx.files).values() for t in ts)

    defined = [f for f in col.funcs if not f["name"].startswith("__") and f["name"] not in col.framework]
    defined += col.classes
    dead = sorted({d["where"] + " " + d["name"] for d in defined if col.tokens[d["name"]] <= 1})
    public = {d["name"]: d["where"] for d in defined
              if not d["name"].startswith("_") and len(d["name"]) >= MIN_NAME and col.defs[d["name"]] == 1}
    single = sorted(f"{public[n]} {n}" for n in public if col.calls[n] - col.def_calls[n] == 1)

    stats = {rel: inv.file_stats(sf) for rel, sf in by_rel.items()}
    risky = sorted(stats, key=lambda r: -(stats[r]["weight"] - stats[r]["lines"]))[:RISKY_TOP]
    untested = [{"file": r, "lines": stats[r]["lines"], "density": stats[r]["density"]}
                for r in risky if tests and not referenced_by_tests(by_rel[r], tests)]

    lengths = [f["lines"] for f in col.funcs]
    approx = sorted(lang for lang in col.languages if lang != inv.PYTHON)
    return {
        "lines_per_package": dict(sorted(col.lines_per_pkg.items())),
        "languages": {lang: dict(c, approximate=lang != inv.PYTHON) for lang, c in sorted(col.languages.items())},
        "approximate_languages": approx,
        "functions": len(col.funcs),
        **{f"over_{n}": sum(x > n for x in lengths) for n in LENGTHS},
        "longest": sorted(col.funcs, key=lambda f: -f["lines"])[:TOP_N],
        "most_complex": sorted(col.funcs, key=lambda f: -f["cc"])[:TOP_N],
        "coverage": {p: {"functions": c["functions"],
                         "hinted_pct": round(100 * c["hinted"] / max(c["functions"], 1), 1),
                         "docstring_pct": round(100 * c["docstring"] / max(c["functions"], 1), 1)}
                     for p, c in sorted(col.per_pkg.items())},
        "import_cycles": cycles(named_graph),
        "smells": dict(col.smells),
        "largest_classes": sorted(col.classes, key=lambda c: -c["lines"])[:10],
        "dead_code_candidates": dead[:DEAD_LIST],
        "dead_code_candidate_count": len(dead),
        "pass_through": sorted(col.pass_through)[:PASS_LIST],
        "pass_through_count": len(col.pass_through),
        "fan_in": {"most_imported": [{"file": r, "importers": n} for r, n in importers.most_common(TOP_N)],
                   "single_use_public": single[:SINGLE_USE_LIST], "single_use_public_count": len(single)},
        "forbidden_imports": forbidden(ctx),
        "untested_risky": untested,
        "tests_scanned": bool(tests),
    }


def report(m: dict, prev: dict | None) -> str:
    """Render the metrics as compact text, with deltas when a previous run is given.
    Args:
        m: This run's metrics.
        prev: A previous run's metrics, or None.
    Returns:
        The text report.
    """
    def delta(key: str) -> str:
        """Format the change in one top-level number.
        Args:
            key: The metric key.
        Returns:
            A "(+n)" suffix, or "" without a previous value.
        """
        if not prev or key not in prev:
            return ""
        d = m[key] - prev[key]
        return f" ({d:+d})" if d else " (=)"

    langs = "; ".join(f"{lang} {c['files']} files {c['lines']} lines{' (approximate)' if c['approximate'] else ''}"
                      for lang, c in m["languages"].items())
    out = [f"languages: {langs}",
           (f"functions {m['functions']}{delta('functions')} | >60 lines {m['over_60']}{delta('over_60')}"
            f" | >100 {m['over_100']}{delta('over_100')} | >200 {m['over_200']}{delta('over_200')}"),
           "import cycles: " + ("none" if not m["import_cycles"] else
                                "; ".join(" <-> ".join(c) for c in m["import_cycles"])),
           "smells: " + ", ".join(f"{k} {v}" for k, v in sorted(m["smells"].items())),
           f"dead-code candidates (unconfirmed): {m['dead_code_candidate_count']}{delta('dead_code_candidate_count')}",
           f"pass-through functions: {m['pass_through_count']}{delta('pass_through_count')}",
           f"public functions with one call site: {m['fan_in']['single_use_public_count']}",
           "forbidden imports: " + ("none" if not m["forbidden_imports"] else
                                    "; ".join(f"{v['where']} {v['import']}" for v in m["forbidden_imports"])),
           "", "longest functions:"]
    out += [f"  {f['lines']:4}  cc {f['cc']:3}  {f['where']} {f['name']}" for f in m["longest"][:10]]
    out += ["", "most complex:"] + [f"  cc {f['cc']:3}  {f['lines']:4} lines  {f['where']} {f['name']}"
                                    for f in m["most_complex"][:10]]
    out += ["", "most-imported modules (fan-in by importing files):"]
    out += [f"  {w['importers']:4}  {w['file']}" for w in m["fan_in"]["most_imported"][:10]]
    if m["tests_scanned"]:
        out += ["", f"risky files no test mentions ({len(m['untested_risky'])} of the {RISKY_TOP} riskiest):"]
        out += [f"  {u['lines']:5} lines  density {u['density']:5}  {u['file']}" for u in m["untested_risky"][:15]]
    if m["coverage"]:
        out += ["", "coverage (hints / docstrings):"]
        for p, c in m["coverage"].items():
            was = prev.get("coverage", {}).get(p) if prev else None
            trend = f"  (was {was['hinted_pct']} / {was['docstring_pct']})" if was else ""
            out.append(f"  {p:22} {c['functions']:5} fn  {c['hinted_pct']:5}% / {c['docstring_pct']:5}%{trend}")
    return "\n".join(out)


def main() -> int:
    """Parse arguments, compute the metrics, write JSON and print the text report.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    ap.add_argument("--out", type=Path, help="write the metrics JSON here")
    ap.add_argument("--compare", type=Path, help="a previous run's metrics JSON")
    args = ap.parse_args()
    ctx = inv.load(args)
    m = collect(ctx)
    prev = json.loads(args.compare.read_text(encoding="utf-8")) if args.compare and args.compare.is_file() else None
    if args.out:
        inv.write_json(args.out, m)
    sys.stdout.write(report(m, prev) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
