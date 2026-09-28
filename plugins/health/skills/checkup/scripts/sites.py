"""List each reader unit's risk sites: the places where lifecycle, data and concurrency bugs live, one per
function, so a reader answers every one and coverage can be counted.
A site is a function (or a class body, or module-level code) holding risk markers, found with the same
per-language markers (languages.json, extended by config risk_markers) that size the units, by kind:
  io      file and database access           what if it fails halfway, or reads another item's data?
  async   threads, workers, timers            what if the context changed before the result lands?
  state   long-lived containers and caches    what resets them on each lifecycle event?
  error   exception handlers                  does it hide a failure or skip the cleanup after it?
Signal and callback lines join a function's async lines only when it also has a thread, worker or timer
marker: on their own they are mostly UI wiring, and listing them buried the sites that matter.
The workflow cannot read files, so the sites are written into plan.json's workflow_args, one list per unit.

    python sites.py --plan <run>/plan.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

import inventory as inv

SITE_KINDS: dict[str, tuple[str, ...]] = {
    "io": ("io", "db"),
    "async": ("async",),
    "state": ("state",),
    "error": ("errors",),
}
COMPANIONS: dict[str, tuple[str, ...]] = {"async": ("events",)}   # counted only beside the kind's own markers
MODULE_LEVEL = "<module>"
Span = tuple[int, int, str]


def python_spans(tree: ast.Module) -> list[Span]:
    """List every function's line span with its qualified name.
    Args:
        tree: The parsed module.
    Returns:
        (first line, last line, Class.method, function or class name), outer spans before inner ones.
    """
    spans: list[Span] = []

    def walk(node: ast.AST, prefix: str) -> None:
        """Collect the functions and classes under a node.
        Args:
            node: The node to search.
            prefix: The enclosing class or function names, dotted.
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                spans.append((child.lineno, child.end_lineno or child.lineno, name))
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return spans


def function_spans(sf: inv.SourceFile) -> list[Span]:
    """List a file's function spans (Python by its syntax tree, other languages by brace matching).
    Args:
        sf: The source file.
    Returns:
        (first line, last line, name) spans.
    """
    if sf.lang == inv.PYTHON:
        return python_spans(sf.tree) if sf.tree else []
    return [(f["line"], f["line"] + f["lines"] - 1, f["name"]) for f in inv.brace_functions(sf)]


def import_lines(sf: inv.SourceFile) -> set[int]:
    """List the lines of a file's import statements, whose names match markers without being risks.
    Args:
        sf: The source file.
    Returns:
        1-based line numbers.
    """
    if sf.lang == inv.PYTHON:
        nodes = [n for n in ast.walk(sf.tree) if isinstance(n, (ast.Import, ast.ImportFrom))] if sf.tree else []
        return {line for n in nodes for line in range(n.lineno, (n.end_lineno or n.lineno) + 1)}
    return {sf.line_of(m.start()) for rx in inv.compiled_list(sf.lang, "imports") for m in rx.finditer(sf.masked)}


def enclosing(spans: list[Span], line: int) -> str:
    """Name the innermost function containing a line.
    Args:
        spans: The file's function spans.
        line: A 1-based line.
    Returns:
        The function name, or MODULE_LEVEL.
    """
    inside = [s for s in spans if s[0] <= line <= s[1]]
    return min(inside, key=lambda s: s[1] - s[0])[2] if inside else MODULE_LEVEL


def marker_lines(sf: inv.SourceFile, spans: list[Span], cats: tuple[str, ...], skip: set[int]) -> dict[str, set[int]]:
    """Find the lines holding markers of some categories, by enclosing function.
    Args:
        sf: The source file.
        spans: The file's function spans.
        cats: Risk-marker categories.
        skip: Lines to ignore (imports).
    Returns:
        Function name to the lines.
    """
    patterns = inv.risk_patterns(sf.lang, sf.extra_risk)
    found: dict[str, set[int]] = {}
    for cat in cats:
        rx = patterns.get(cat)
        for m in rx.finditer(sf.masked) if rx else ():
            line = sf.line_of(m.start())
            if line not in skip:
                found.setdefault(enclosing(spans, line), set()).add(line)
    return found


def file_sites(sf: inv.SourceFile) -> list[dict]:
    """Find one file's risk sites.
    Args:
        sf: The source file.
    Returns:
        Sites (file, function, kinds: kind to lines), in file order.
    """
    spans = function_spans(sf)
    skip = import_lines(sf)
    found: dict[str, dict[str, set[int]]] = {}
    for kind, cats in SITE_KINDS.items():
        own = marker_lines(sf, spans, cats, skip)
        extra = marker_lines(sf, spans, COMPANIONS.get(kind, ()), skip)
        for fn, lines in own.items():
            found.setdefault(fn, {})[kind] = lines | extra.get(fn, set())
    sites = [{"file": sf.rel, "function": fn, "kinds": {k: sorted(v) for k, v in kinds.items()}}
             for fn, kinds in found.items()]
    return sorted(sites, key=lambda s: min(min(v) for v in s["kinds"].values()))


def main() -> int:
    """Parse arguments, list every unit's sites into plan.json and print the counts.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    ap.add_argument("--plan", type=Path, required=True, help="the run's plan.json (its workflow_args gain the sites)")
    args = ap.parse_args()
    ctx = inv.load(args)
    by_rel = {sf.rel: sf for sf in ctx.files}
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    total: Counter = Counter()
    count = 0
    w = sys.stdout.write
    for unit in plan["workflow_args"]["units"]:
        sites = [s for rel in unit["files"] if rel in by_rel for s in file_sites(by_rel[rel])]
        for n, s in enumerate(sites, 1):
            s["id"] = f"S{n:03d}"
        unit["sites"] = sites
        kinds = Counter(k for s in sites for k in s["kinds"])
        total.update(kinds)
        count += len(sites)
        w(f"  {unit['id']:34} {len(sites):4} sites  " + ", ".join(f"{k} {kinds[k]}" for k in SITE_KINDS) + "\n")
    inv.write_json(args.plan, plan)
    w(f"{count} sites (functions with: " + ", ".join(f"{k} {total[k]}" for k in SITE_KINDS) + f"); written to {args.plan}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
