"""Plan a checkup: partition the source into reader units and size the run.
A unit is a group of files one reader can read in full. A file's weight is its line count scaled by how
dense it is in the constructs where lifecycle, data and concurrency bugs live, so stateful code gets
smaller units. Small units merge with the units they import from or share a folder with. The tier sets
the unit cap, the reader limit and the lenses; the size picks the mode: inline (small enough for the
main loop), full (every unit read) or rolling (the units ranked highest by risk, churn and staleness
this run, the rest on later runs via the coverage ledger). Writes plan.json, including ready-made
workflow args, and prints the agent count by model. Also copies checkup.workflow.js into the run folder
(the Workflow tool runs only scripts inside the working directory, and the copy records what this run
ran) and creates the known-items file when it is missing, so no agent goes looking for one.

    python partition.py --tier standard --out <data_root>/<date>/plan.json [--base SHA] [--since SHA]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import inventory as inv

# Readers run on Opus except in lean: in a measured comparison on one unit, Opus readers found about twice the
# verified real bugs per review of Sonnet readers given the same prompt, for about a fifth more tokens.
TIERS: dict[str, dict] = {
    "lean": {"cap": 12000, "max_readers": 6, "hunters": [], "lenses": [], "cartographer": False,
             "reader_model": "sonnet"},
    "standard": {"cap": 12000, "max_readers": 15, "hunters": ["lifecycle", "failure"],
                 "lenses": ["duplication", "performance"], "cartographer": True, "reader_model": "opus"},
    "deep": {"cap": 7000, "max_readers": 25, "hunters": ["lifecycle", "failure", "security"],
             "lenses": ["duplication", "performance"], "cartographer": True, "reader_model": "opus"},
}
SECURITY_PROFILES = ("service",)
MERGE_BELOW = 0.6         # units lighter than this share of the cap look for a partner
INLINE_MAX_UNITS = 2      # at or below this many units the main loop reads the code itself
STATEFUL_DENSITY = 1.0    # state/event/async markers per 100 lines that make a file stateful
STATEFUL_MIN_LINES = 3000
CARTO_MAX_LINES = 30000
FOLLOWUPS = 2
FINDINGS_PER_AGENT = 12   # 2026-09-26 run: 244 raw findings from 20 agents
HEAVY_SHARE = 0.12        # the same run: 28 of 244 critical or high
OPUS_BATCH = 10
SONNET_BATCH = 40
STALENESS = {"never": 2.0, "changed": 1.5, "unchanged": 0.3}
LEDGER_FILE = "ledger.json"
WORKFLOW_FILE = "checkup.workflow.js"
EXTRA_MODEL = "sonnet"


def first_fit(files: list[str], stats: dict[str, dict], cap: int, package: str) -> list[dict]:
    """Pack one package's files into bins of at most cap weight, heaviest first.
    Args:
        files: The package's files.
        stats: Per-file stats.
        cap: The weight budget per unit.
        package: The package key, recorded on each bin.
    Returns:
        The bins.
    """
    bins: list[dict] = []
    for rel in sorted(files, key=lambda r: -stats[r]["weight"]):
        w = stats[rel]["weight"]
        target = next((b for b in bins if b["weight"] + w <= cap), None)
        if target is None:
            target = {"packages": [package], "files": [], "weight": 0}
            bins.append(target)
        target["files"].append(rel)
        target["weight"] += w
    return bins


def shared_prefix(a: list[str], b: list[str]) -> int:
    """Count the leading path parts two units' first packages share.
    Args:
        a: One unit's packages.
        b: The other's.
    Returns:
        The number of shared leading directories.
    """
    pa, pb = a[0].split("/"), b[0].split("/")
    n = 0
    while n < min(len(pa), len(pb)) and pa[n] == pb[n]:
        n += 1
    return n


def merge_small(bins: list[dict], graph: dict[str, set[str]], cap: int) -> list[dict]:
    """Merge light units with the unit they share the most imports (or folder depth) with, within the cap.
    Args:
        bins: Units from first_fit.
        graph: The file import graph.
        cap: The weight budget per unit.
    Returns:
        The merged units.
    """
    units = {i: {**b, "files": list(b["files"])} for i, b in enumerate(bins)}
    owner = {f: i for i, b in units.items() for f in b["files"]}
    links: dict[int, Counter] = defaultdict(Counter)
    for f, targets in graph.items():
        i = owner.get(f)
        for g in targets:
            j = owner.get(g)
            if i is not None and j is not None and i != j:
                links[i][j] += 1
                links[j][i] += 1
    merged_any = True
    while merged_any:
        merged_any = False
        for i in sorted(units, key=lambda k: units[k]["weight"]):
            a = units.get(i)
            if a is None or a["weight"] >= MERGE_BELOW * cap:
                continue
            best, best_score = None, 0.0
            for j, b in units.items():
                if j == i or a["weight"] + b["weight"] > cap:
                    continue
                score = links[i].get(j, 0) + 0.5 * shared_prefix(a["packages"], b["packages"])
                if score > best_score:
                    best, best_score = j, score
            if best is None:
                continue
            b = units.pop(best)
            a["packages"] = sorted(set(a["packages"]) | set(b["packages"]))
            a["files"] += b["files"]
            a["weight"] += b["weight"]
            for k, n in links.pop(best, Counter()).items():
                links[k].pop(best, None)
                if k != i:
                    links[i][k] += n
                    links[k][i] += n
            merged_any = True
    return list(units.values())


def staleness(ctx: inv.Context, rels: list[str]) -> dict[str, float]:
    """Weight each file by when a reader last read it in full, from the coverage ledger.
    Args:
        ctx: The context (for the data root and git).
        rels: The files.
    Returns:
        File to staleness factor.
    """
    path = ctx.data_root / LEDGER_FILE
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    changed_by_commit: dict[str, set[str] | None] = {}
    out = {}
    for rel in rels:
        entry = ledger.get(rel)
        if not entry:
            out[rel] = STALENESS["never"]
            continue
        commit = entry.get("commit") or ""
        if commit not in changed_by_commit:
            changed_by_commit[commit] = inv.changed_since(ctx.root, commit) if commit else None
        changed = changed_by_commit[commit]
        out[rel] = STALENESS["changed"] if changed is None or rel in changed else STALENESS["unchanged"]
    return out


def focus_hint(files: list[str], stats: dict[str, dict]) -> str:
    """Summarize a unit's strongest risk markers as a starting point for its focus line.
    Args:
        files: The unit's files.
        stats: Per-file stats.
    Returns:
        E.g. "state 31, events 22, async 9".
    """
    totals: dict[str, int] = {}
    for rel in files:
        for cat, n in stats[rel]["markers"].items():
            totals[cat] = totals.get(cat, 0) + n
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:3]
    return ", ".join(f"{cat} {n}" for cat, n in top if n) or "no risk markers"


def plan_agents(readers: int, extras: int, hunters: list[str], lenses: list[str],
                cartographer: bool, mode: str, reader_model: str) -> dict:
    """Estimate the agent count by model.
    Args:
        readers: Reader units this run.
        extras: Extra assignments from the config.
        hunters: Hunters that will run.
        lenses: Improvement lens agents that will run.
        cartographer: Whether the cartographer runs.
        mode: inline, full or rolling.
        reader_model: The model readers and follow-up readers run on.
    Returns:
        Items as [what, count, model] and totals by model; verifier counts are estimates.
    """
    if mode == "inline":
        return {"items": [["verifier (critical/high)", 1, "opus"]], "sonnet": 0, "opus": 1, "total": 1,
                "note": "inline: the main loop reads the code; one Opus verifier for critical and high"}
    finders = readers + FOLLOWUPS + extras + len(hunters)
    findings = FINDINGS_PER_AGENT * finders
    heavy = math.ceil(HEAVY_SHARE * findings / OPUS_BATCH)
    rest = math.ceil((1 - HEAVY_SHARE) * findings / SONNET_BATCH)
    items = [["readers", readers, reader_model], ["follow-up readers (up to)", FOLLOWUPS, reader_model],
             ["extras", extras, EXTRA_MODEL], ["cartographer", int(cartographer), "sonnet"],
             ["hunters: " + (", ".join(hunters) or "none"), len(hunters), "opus"],
             ["lenses: " + (", ".join(lenses) or "none"), len(lenses), "opus"],
             ["verifiers critical/high (est.)", heavy, "opus"], ["verifiers and evaluators (est.)", rest, "sonnet"]]
    items = [i for i in items if i[1]]
    sonnet = sum(n for _, n, m in items if m == "sonnet")
    opus = sum(n for _, n, m in items if m == "opus")
    return {"items": items, "sonnet": sonnet, "opus": opus, "total": sonnet + opus,
            "note": f"verifier counts assume ~{FINDINGS_PER_AGENT} findings per finding agent"}


def main() -> int:
    """Parse arguments, partition the tree, size the run and write plan.json.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    ap.add_argument("--tier", choices=sorted(TIERS), default="standard")
    ap.add_argument("--cap", type=int, help="weighted lines per unit (default: the tier's)")
    ap.add_argument("--max-readers", type=int, help="reader limit (default: the tier's)")
    ap.add_argument("--base", help="base commit: adds the regression hunter")
    ap.add_argument("--since", help="delta run: readers cover files changed since this commit and their importers")
    ap.add_argument("--depth", type=int, default=inv.PACKAGE_DEPTH, help="package depth units start from")
    ap.add_argument("--out", type=Path, required=True, help="plan.json path inside the run folder")
    args = ap.parse_args()

    ctx = inv.load(args)
    tier = dict(TIERS[args.tier])
    cap = args.cap or tier["cap"]
    max_readers = args.max_readers or tier["max_readers"]
    if not ctx.files:
        sys.stderr.write("no source files in scope: check src, exclude and extensions\n")
        return 1
    stats = {f.rel: inv.file_stats(f) for f in ctx.files}
    graph = inv.import_graph(ctx.files)

    scope = [f.rel for f in ctx.files]
    if args.since:
        changed = inv.changed_since(ctx.root, args.since)
        if changed is None:
            sys.stderr.write(f"git cannot diff against {args.since}\n")
            return 1
        touched = {r for r in scope if r in changed}
        importers = {r for r in scope if graph.get(r, set()) & touched}
        scope = sorted(touched | importers)

    by_pkg: dict[str, list[str]] = {}
    for rel in scope:
        by_pkg.setdefault(inv.package_of(rel, args.depth), []).append(rel)
    bins = [b for pkg in sorted(by_pkg) for b in first_fit(by_pkg[pkg], stats, cap, pkg)]
    bins = merge_small(bins, graph, cap)
    stale = staleness(ctx, scope)
    churn = inv.churn(inv.git_log(ctx.root, ctx.config["history_months"], ctx.config["src"]) or [])
    top_churn = max(churn.values(), default=1)
    for b in bins:
        b["lines"] = sum(stats[r]["lines"] for r in b["files"])
        b["priority"] = round(sum(stats[r]["weight"] * (1 + churn.get(r, 0) / top_churn) * stale[r]
                                  for r in b["files"]))
        b["oversize"] = b["weight"] > cap
    bins.sort(key=lambda b: (-b["priority"], b["packages"][0]))
    for i, b in enumerate(bins, 1):
        first = b["packages"][0].replace("/", "-").strip(".-") or "root"
        b["id"] = f"u{i:02d}-{first}" + (f"+{len(b['packages']) - 1}" if len(b["packages"]) > 1 else "")

    mode = "inline" if len(bins) <= INLINE_MAX_UNITS else "full" if len(bins) <= max_readers else "rolling"
    selected = bins if mode != "rolling" else bins[:max_readers]
    uncovered = [] if mode != "rolling" else bins[max_readers:]

    stateful_files = sorted((r for r in scope if stats[r]["stateful"] >= STATEFUL_DENSITY),
                            key=lambda r: -stats[r]["stateful"] * stats[r]["lines"])
    stateful_lines = sum(stats[r]["lines"] for r in stateful_files)
    carto_files, total = [], 0
    for r in stateful_files:
        if total + stats[r]["lines"] > CARTO_MAX_LINES:
            break
        carto_files.append(r)
        total += stats[r]["lines"]
    cartographer = bool(tier["cartographer"]) and stateful_lines >= STATEFUL_MIN_LINES
    hunters = list(tier["hunters"])
    if not cartographer and "lifecycle" in hunters:
        hunters.remove("lifecycle")
    if any(p in SECURITY_PROFILES for p in ctx.config.get("profiles", [])) and "security" not in hunters \
            and args.tier != "lean":
        hunters.append("security")
    if args.base:
        hunters.append("regression")
    elif args.tier == "lean":
        hunters.append("failure")
    lenses = list(tier["lenses"])
    extras = ctx.config.get("extras", [])
    agents = plan_agents(len(selected), len(extras), hunters, lenses, cartographer, mode, tier["reader_model"])

    run_dir = args.out.resolve().parent
    try:
        data_dir = run_dir.relative_to(ctx.root).as_posix()
    except ValueError:
        data_dir = run_dir.as_posix()
    data_root = ctx.config["data_root"].rstrip("/")
    units = [{"id": b["id"], "name": ", ".join(b["packages"]), "files": b["files"],
              "weights": [stats[r]["weight"] for r in b["files"]], "focus": focus_hint(b["files"], stats)}
             for b in selected]
    workflow_args = {
        "dataDir": data_dir,
        "head": inv.head_commit(ctx.root), "base": args.base, "runDate": datetime.now(timezone.utc).astimezone().date().isoformat(),
        "profile": ctx.config.get("profiles", []), "cap": cap, "units": units,
        "extras": extras, "cartographer": cartographer, "cartoFiles": carto_files if cartographer else [],
        "hunters": hunters, "lenses": lenses, "followups": FOLLOWUPS, "readerModel": tier["reader_model"],
        "knownPath": f"{data_root}/{inv.KNOWN_FILE}",
    }
    plan = {
        "tier": args.tier, "mode": mode, "cap": cap, "max_readers": max_readers, "since": args.since,
        "totals": {"files": len(ctx.files), "lines": sum(s["lines"] for s in stats.values()),
                   "weight": sum(s["weight"] for s in stats.values()), "scope_files": len(scope),
                   "units": len(bins)},
        "skipped": ctx.skipped, "agents": agents,
        "units": [{k: b[k] for k in ("id", "packages", "files", "lines", "weight", "priority", "oversize")}
                  for b in selected],
        "uncovered": [{"id": b["id"], "packages": b["packages"], "files": b["files"], "lines": b["lines"]}
                      for b in uncovered],
        "stateful": {"files": len(stateful_files), "lines": stateful_lines},
        "hottest": [{"file": r, "lines": stats[r]["lines"], "density": stats[r]["density"]}
                    for r in sorted(scope, key=lambda r: -stats[r]["density"])[:15]],
        "workflow_script": f"{data_dir}/{WORKFLOW_FILE}",
        "workflow_args": workflow_args,
    }
    inv.write_json(args.out, plan)
    inv.write_text(run_dir / WORKFLOW_FILE, (inv.SCRIPTS_DIR / WORKFLOW_FILE).read_text(encoding="utf-8"))
    if run_dir.is_relative_to((ctx.root / data_root).resolve()):
        inv.ensure_known(ctx.root / data_root)

    t = plan["totals"]
    w = sys.stdout.write
    skipped = ", ".join(f"{len(v)} {k}" for k, v in ctx.skipped.items()) or "none"
    w(f"{t['files']} files, {t['lines']} lines (weight {t['weight']}); skipped: {skipped}\n")
    if args.since:
        w(f"delta since {args.since}: {t['scope_files']} files in reader scope\n")
    w(f"tier {args.tier}, mode {mode}: {len(bins)} units at cap {cap}, reader limit {max_readers}\n")
    w(f"cartographer: {'yes' if cartographer else 'no'} (stateful: {len(stateful_files)} files, "
      f"{stateful_lines} lines)\n")
    w(f"agents: {agents['total']} ({agents['sonnet']} Sonnet, {agents['opus']} Opus); {agents['note']}\n")
    for what, n, model in agents["items"]:
        w(f"  {n:3}  {model:6}  {what}\n")
    w(f"workflow script: {plan['workflow_script']}\n")
    w("units:\n")
    for b, u in zip(selected, units):
        flag = "  OVERSIZE: page it by line range" if b["oversize"] else ""
        w(f"  {b['id']:34} {b['lines']:6} lines  weight {b['weight']:6}  {len(b['files']):3} files  "
          f"{u['focus']}{flag}\n")
    if uncovered:
        w(f"not covered this run ({len(uncovered)} units, {sum(b['lines'] for b in uncovered)} lines): "
          + ", ".join(b["id"] for b in uncovered) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
