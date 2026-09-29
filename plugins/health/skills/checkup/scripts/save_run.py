"""Save a finished checkup workflow run into its run folder and update the cross-run files.
Reads the record Claude Code keeps for a workflow run (~/.claude/projects/*/*/workflows/<run id>.json),
or a result saved with --result, and writes:
  <run>/review.json        the whole result plus the run's cost (agents, tokens, duration)
  <run>/findings.json      every finding in the shared schema (/treatment reads and updates it)
  <data_root>/ledger.json  which files a reader read in full, at which commit (partition.py reads it)
  <data_root>/chart.json   the patient chart: the run's findings merged into the conditions of every visit,
                           and the visit's row of vitals (chart.py)
  <data_root>/known.tsv    every charted condition except treated and cured ones, for agents to grep
A killed run has no result: its agents' outputs are copied from the run journal to review.partial.json.
A resumed run keeps one record, which counts only its last pass; the cost line says so.
--refresh-known re-adds a run to the chart (after the main loop re-grades its findings or writes its
trajectory.json) and rebuilds known.tsv; without --run-dir it only rebuilds known.tsv, building the chart
from every run folder first when there is none yet.

    python save_run.py --run-id wf_xxxx --run-dir <data_root>/<date>
    python save_run.py --refresh-known [--run-dir <data_root>/<date>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import chart as ch
import inventory as inv

RUN_RECORDS = Path.home() / ".claude" / "projects"
REVIEW_FILE = "review.json"
PARTIAL_FILE = "review.partial.json"
FINDINGS_FILE = "findings.json"
PLAN_FILE = "plan.json"
LEDGER_FILE = "ledger.json"
EXIT_PARTIAL = 2


def find_record(run_id: str) -> Path | None:
    """Locate the workflow run record.
    Args:
        run_id: The run id from the Workflow result (wf_...).
    Returns:
        The record path, or None.
    """
    return next(iter(sorted(RUN_RECORDS.glob(f"*/*/workflows/{run_id}.json"))), None)


def find_journal(run_id: str) -> Path | None:
    """Locate a run's agent journal.
    Args:
        run_id: The run id.
    Returns:
        The journal path, or None.
    """
    return next(iter(sorted(RUN_RECORDS.glob(f"*/*/subagents/workflows/{run_id}/journal.jsonl"))), None)


def salvage(run_id: str, run_dir: Path) -> int:
    """Copy a killed run's agent results from its journal.
    Args:
        run_id: The run id.
        run_dir: The run folder.
    Returns:
        The exit code (EXIT_PARTIAL when something was salvaged, 1 when nothing was found).
    """
    journal = find_journal(run_id)
    if journal is None:
        sys.stderr.write(f"no result and no journal for {run_id}\n")
        return 1
    labels, results = {}, []
    for line in journal.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "started":
            labels[rec["agentId"]] = rec.get("label", "")
        elif rec.get("type") == "result":
            results.append({"label": labels.get(rec["agentId"], rec["agentId"]), "result": rec.get("result")})
    inv.write_json(run_dir / PARTIAL_FILE, results)
    sys.stderr.write(f"run {run_id} has no final result; {len(results)} agent results saved to "
                     f"{run_dir / PARTIAL_FILE}. Resume the workflow with resumeFromRunId to finish it.\n")
    return EXIT_PARTIAL


def update_ledger(data_root: Path, result: dict, run_name: str) -> int:
    """Record the files readers read in full at this run's commit.
    Args:
        data_root: The folder holding all runs.
        result: The workflow result.
        run_name: The run folder's name.
    Returns:
        The number of files recorded.
    """
    path = data_root / LEDGER_FILE
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    n = 0
    for reader in result.get("readers", []):
        for rel in reader.get("read_in_full", []):
            ledger[rel.replace("\\", "/")] = {"commit": result.get("head"), "date": result.get("runDate"),
                                              "run": run_name}
            n += 1
    inv.write_json(path, dict(sorted(ledger.items())))
    return n


def plan_visit(run_dir: Path) -> str | None:
    """Read the visit kind partition.py recorded in the run's plan.
    Args:
        run_dir: The run folder.
    Returns:
        baseline, second-opinion, follow-up or routine, or None when there is no plan.
    """
    path = run_dir / PLAN_FILE
    return json.loads(path.read_text(encoding="utf-8")).get("visit") if path.is_file() else None


def refresh_known(data_root: Path, run_dir: Path | None = None) -> tuple[int, dict | None]:
    """Re-add a run to the chart (or build the chart when there is none) and rewrite known.tsv from it.
    Args:
        data_root: The folder holding all runs.
        run_dir: A run to re-add, or None.
    Returns:
        The number of known items, and the run's chart counts when a run was added.
    """
    chart = ch.load_chart(data_root)
    counts = None
    if run_dir is not None:
        counts = ch.add_run(chart, run_dir, plan_visit(run_dir))
    elif not chart["conditions"]:
        for folder in ch.run_folders(data_root):
            ch.add_run(chart, folder, plan_visit(folder))
    ch.save_chart(data_root, chart)
    return ch.write_known(data_root, chart), counts


def agents_started(run_id: str) -> int | None:
    """Count the agents a run started across all its passes (a resume adds a pass to the same journal).
    Args:
        run_id: The run id.
    Returns:
        The count, or None when there is no journal.
    """
    journal = find_journal(run_id)
    if journal is None:
        return None
    lines = journal.read_text(encoding="utf-8").splitlines()
    return sum(json.loads(line).get("type") == "started" for line in lines)


def summary(result: dict, meta: dict) -> str:
    """Summarize a saved run for the report.
    Args:
        result: The workflow result.
        meta: The run's cost fields.
    Returns:
        Text: counts by kind and status, yield by lens, cost, what was not covered.
    """
    counts = Counter((f["kind"], f["status"]) for f in result.get("findings", []))
    out = ["findings by kind and status:"]
    for kind in sorted({k for k, _ in counts}):
        out.append(f"  {kind}: " + ", ".join(f"{s} {n}" for (k, s), n in sorted(counts.items()) if k == kind))
    out.append("yield by lens (kept = confirmed or accepted):")
    for y in result.get("yield", []):
        out.append(f"  {y['lens']:24} agents {y['agents']:3}  raw {y['raw']:4}  kept {y['kept']:4}")
    minutes = round((meta.get("durationMs") or 0) / 60000, 1)
    out.append(f"cost: {meta.get('agentCount')} agents, {meta.get('totalTokens')} tokens, {minutes} min, "
               f"status {meta.get('status')}")
    started = meta.get("agentsStarted")
    if started and started > (meta.get("agentCount") or 0):
        out.append(f"  resumed: {started} agents started across passes and the record counts only the last "
                   f"pass; add the earlier passes' tokens and minutes from their completion notices")
    nc =result.get("not_covered", {})
    out.append(f"not covered: {len(nc.get('files', []))} files unread; "
               f"{len(nc.get('hunt', []))} hunter items and {len(nc.get('lens', []))} lens items not reached"
               + (f"; {len(nc['recheck'])} re-checks unanswered" if nc.get("recheck") else ""))
    return "\n".join(out)


def chart_line(run_dir: Path, counts: dict) -> str:
    """Describe what a run changed on the chart.
    Args:
        run_dir: The run folder.
        counts: add_run's counts.
    Returns:
        One line.
    """
    rechecked = counts["recheck_cured"] + counts["recheck_reopened"] + counts["recheck_uncertain"]
    return (f"chart: {run_dir.name} opened {counts['added']} conditions ({counts['new_serious']} serious), saw "
            f"{counts['matched']} again ({counts['review']} weak matches to check), reopened {counts['reopened']}"
            + (f"; re-checks: {counts['recheck_cured']} cured, {counts['recheck_reopened']} still present, "
               f"{counts['recheck_uncertain']} uncertain" if rechecked else "") + "\n")


def main() -> int:
    """Parse arguments, save the run, update the ledger and known items, and print the summary.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    ap.add_argument("--run-id", help="the workflow run id (wf_...)")
    ap.add_argument("--result", type=Path, help="a JSON file holding the workflow result, instead of --run-id")
    ap.add_argument("--run-dir", type=Path, help="the run folder (holds plan.json and BRIEF.md)")
    ap.add_argument("--refresh-known", action="store_true",
                    help="re-add --run-dir to the chart (when given) and rebuild known.tsv")
    args = ap.parse_args()
    root = args.root.resolve()
    data_root = root / inv.load_config(root, args.config)["data_root"]
    run_dir = None if not args.run_dir else args.run_dir if args.run_dir.is_absolute() else root / args.run_dir
    if args.refresh_known:
        known, counts = refresh_known(data_root, run_dir)
        sys.stdout.write((chart_line(run_dir, counts) if counts else "") + f"{known} items in {data_root / inv.KNOWN_FILE}\n")
        return 0
    if not run_dir or not (args.run_id or args.result):
        ap.error("--run-dir and one of --run-id / --result are required")
    if args.result:
        record = {"result": json.loads(args.result.read_text(encoding="utf-8")), "status": "given"}
    else:
        path = find_record(args.run_id)
        if path is None:
            sys.stderr.write(f"no run record for {args.run_id} under {RUN_RECORDS}; save the Workflow "
                             f"result to a file and pass --result\n")
            return 1
        record = json.loads(path.read_text(encoding="utf-8"))
    result = record.get("result")
    if not result:
        return salvage(args.run_id, run_dir) if args.run_id else 1
    meta = {k: record.get(k) for k in ("runId", "status", "agentCount", "totalTokens", "durationMs", "timestamp")}
    if args.run_id:
        meta["agentsStarted"] = agents_started(args.run_id)
    inv.write_json(run_dir / REVIEW_FILE, {"meta": meta, **result})
    findings = [dict(f, run=run_dir.name) for f in result.get("findings", [])]
    inv.write_json(run_dir / FINDINGS_FILE, findings)
    recorded = update_ledger(data_root, result, run_dir.name)
    known, counts = refresh_known(data_root, run_dir)
    sys.stdout.write(f"saved {len(findings)} findings to {run_dir / FINDINGS_FILE}; ledger +{recorded} files; "
                     f"{known} known items\n{chart_line(run_dir, counts)}{summary(result, meta)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
