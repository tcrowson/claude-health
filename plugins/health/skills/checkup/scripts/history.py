"""Read the git history for the evidence the trajectory lens needs: churn, hotspots, change coupling,
change footprint per package, fix recurrence, and when a pattern first appeared.
Change coupling finds files that keep changing together; a pair with no import between them is
hidden coupling. The footprint shows how many files and packages a typical change to a package
touches (poor locality spreads wide). Fix recurrence shows where fix commits concentrate, recent
against older. --origin reports the commit that first introduced a string.

    python history.py --out <run>/history.json [--months 12] [--origin "class SessionCache" --origin "_pending"]
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import inventory as inv

FIX_RX = re.compile(r"\b(?:fix(?:es|ed)?|bug|regression|crash|hotfix|broken)\b", re.IGNORECASE)
MAX_COMMIT_FILES = 30     # larger commits are sweeps (renames, formatting) and say nothing about coupling
MIN_SHARED = 4            # commits two files must share before their coupling is reported
MIN_DEGREE = 0.5          # shared commits / the mean of both files' commits
RECENT_DAYS = 90
TOP_N = 20
FORMAT_ORIGIN = "%h%x1f%ad%x1f%s"


def by_count(counts: Counter) -> list[tuple[str, int]]:
    """Order a counter most first, ties by name, so the same history always gives the same output.
    Args:
        counts: Name to count.
    Returns:
        (name, count) pairs.
    """
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def coupling(commits: list[inv.Commit], scope: set[str], graph: dict[str, set[str]]) -> list[dict]:
    """Find file pairs that change together.
    Args:
        commits: The commits.
        scope: Files in scope.
        graph: The import graph, to mark pairs that import each other.
    Returns:
        Pairs with shared commits, degree and whether an import links them, strongest first.
    """
    alone: Counter = Counter()
    pairs: Counter = Counter()
    for c in commits:
        files = sorted(f for f in set(c.files) if f in scope)
        if len(files) > MAX_COMMIT_FILES:
            continue
        alone.update(files)
        pairs.update(combinations(files, 2))
    out = []
    for (a, b), shared in pairs.items():
        degree = shared / ((alone[a] + alone[b]) / 2)
        if shared >= MIN_SHARED and degree >= MIN_DEGREE:
            linked = b in graph.get(a, set()) or a in graph.get(b, set())
            out.append({"a": a, "b": b, "shared": shared, "degree": round(degree, 2), "imports": linked})
    out.sort(key=lambda p: (-p["shared"], -p["degree"], p["a"], p["b"]))
    return out


def footprint(commits: list[inv.Commit], scope: set[str]) -> list[dict]:
    """Measure, per package, how far a typical change reaches.
    Args:
        commits: The commits.
        scope: Files in scope.
    Returns:
        Per package: commits, median files and median packages touched by a commit that touches it.
    """
    files_n: dict[str, list[int]] = defaultdict(list)
    pkgs_n: dict[str, list[int]] = defaultdict(list)
    for c in commits:
        files = [f for f in set(c.files) if f in scope]
        if not files or len(files) > MAX_COMMIT_FILES:
            continue
        pkgs = {inv.package_of(f) for f in files}
        for p in pkgs:
            files_n[p].append(len(files))
            pkgs_n[p].append(len(pkgs))
    out = [{"package": p, "commits": len(files_n[p]), "median_files": statistics.median(files_n[p]),
            "median_packages": statistics.median(pkgs_n[p])} for p in files_n]
    out.sort(key=lambda r: (-r["median_packages"], -r["commits"], r["package"]))
    return out


def fixes(commits: list[inv.Commit], scope: set[str]) -> list[dict]:
    """Count fix commits per file, recent against older.
    Args:
        commits: The commits.
        scope: Files in scope.
    Returns:
        Files with fix commits, most first, with the recent count and the last fix date.
    """
    cutoff = (datetime.now(timezone.utc).astimezone().date() - timedelta(days=RECENT_DAYS)).isoformat()
    total: Counter = Counter()
    recent: Counter = Counter()
    last: dict[str, str] = {}
    for c in commits:
        if not FIX_RX.search(c.subject):
            continue
        for f in set(c.files):
            if f in scope:
                total[f] += 1
                recent[f] += c.date >= cutoff
                last[f] = max(last.get(f, ""), c.date)
    return [{"file": f, "fixes": n, "recent": recent[f], "last": last[f]} for f, n in by_count(total)[:TOP_N]]


def origin(root: Path, pattern: str, paths: list[str]) -> dict:
    """Find the commit that first introduced a string.
    Args:
        root: The repo root.
        pattern: The literal string (git log -S).
        paths: Path prefixes to search.
    Returns:
        The pattern with the commit's hash, date and subject, or found=False.
    """
    out = inv.git(root, "log", "--reverse", "--date=short", f"--format={FORMAT_ORIGIN}", f"-S{pattern}",
                  "--", *(paths or ["."]))
    first = out.splitlines()[0] if out and out.strip() else ""
    if not first:
        return {"pattern": pattern, "found": False}
    sha, day, subject = (first.split("\x1f", 2) + ["", ""])[:3]
    return {"pattern": pattern, "found": True, "sha": sha, "date": day, "subject": subject}


def main() -> int:
    """Parse arguments, read the history, write JSON and print a summary.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    ap.add_argument("--months", type=int, help="how far back to read (default: config history_months)")
    ap.add_argument("--origin", action="append", default=[], help="a string whose first commit to find")
    ap.add_argument("--out", type=Path, help="write history.json here")
    args = ap.parse_args()
    ctx = inv.load(args)
    months = args.months or ctx.config["history_months"]
    commits = inv.git_log(ctx.root, months, ctx.config["src"])
    if commits is None:
        sys.stderr.write("not a git repo, or git is missing: no history\n")
        return 1
    scope = {f.rel for f in ctx.files}
    lines = {f.rel: f.lines for f in ctx.files}
    churn = inv.churn(commits)
    graph = inv.import_graph(ctx.files)
    result = {
        "months": months, "commits": len(commits),
        "churn": [{"file": f, "commits": n} for f, n in by_count(churn) if f in scope][:TOP_N],
        "hotspots": sorted(({"file": f, "commits": churn[f], "lines": lines[f], "score": churn[f] * lines[f]}
                            for f in scope if churn[f]), key=lambda h: (-h["score"], h["file"]))[:TOP_N],
        "coupling": coupling(commits, scope, graph)[:TOP_N * 2],
        "footprint": footprint(commits, scope),
        "fixes": fixes(commits, scope),
        "origins": [origin(ctx.root, p, ctx.config["src"]) for p in args.origin],
    }
    if args.out:
        inv.write_json(args.out, result)
    w = sys.stdout.write
    w(f"{len(commits)} commits in {months} months\n\nhotspots (commits x lines):\n")
    for h in result["hotspots"][:10]:
        w(f"  {h['commits']:4} commits  {h['lines']:6} lines  {h['file']}\n")
    hidden = [p for p in result["coupling"] if not p["imports"]]
    w(f"\nchange coupling: {len(result['coupling'])} pairs, {len(hidden)} with no import between them\n")
    for p in result["coupling"][:10]:
        w(f"  {p['shared']:4} shared  degree {p['degree']:.2f}  {'import' if p['imports'] else 'HIDDEN':6}  "
          f"{p['a']}  <->  {p['b']}\n")
    w("\nwidest footprint (median packages per change):\n")
    for r in result["footprint"][:8]:
        w(f"  {r['median_packages']:4g} pkgs  {r['median_files']:4g} files  {r['commits']:4} commits  "
          f"{r['package']}\n")
    w("\nfix recurrence:\n")
    for f in result["fixes"][:10]:
        w(f"  {f['fixes']:4} fixes ({f['recent']} in {RECENT_DAYS} days, last {f['last']})  {f['file']}\n")
    for o in result["origins"]:
        w(f"\norigin of {o['pattern']!r}: " + (f"{o['sha']} {o['date']} {o['subject']}" if o["found"] else "not found"))
    if result["origins"]:
        w("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
