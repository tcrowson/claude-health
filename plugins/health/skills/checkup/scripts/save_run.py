"""Save a finished checkup workflow run into its run folder and update the cross-run files.
Reads the record Claude Code keeps for a workflow run (~/.claude/projects/*/*/workflows/<run id>.json),
or a result saved with --result, and writes:
  <run>/review.json        the whole result plus the run's cost (agents, tokens, duration)
  <run>/findings.json      every finding in the shared schema (/treatment reads and updates it)
  <data_root>/ledger.json  which files a reader read in full, at which commit (partition.py reads it)
  <data_root>/known.tsv    open, refuted and declined items of every run, one per line, for agents to grep
A killed run has no result: its agents' outputs are copied from the run journal to review.partial.json.
--refresh-known only rebuilds known.tsv (after /treatment or the trajectory step changes statuses).

    python save_run.py --run-id wf_xxxx --run-dir <data_root>/<date>
    python save_run.py --refresh-known
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import inventory as inv

RUN_RECORDS = Path.home() / ".claude" / "projects"
REVIEW_FILE = "review.json"
PARTIAL_FILE = "review.partial.json"
FINDINGS_FILE = "findings.json"
TRAJECTORY_FILE = "trajectory.json"
LEDGER_FILE = "ledger.json"
KNOWN_FILE = "known.tsv"
KNOWN_HEADER = "file\tline\tstatus\tkind\tid\ttitle"
# Items agents must not re-report: still open, or already judged not worth it. Fixed items stay out,
# so a fixed bug that comes back is reported as new.
KNOWN_STATUSES = {"confirmed", "accepted", "uncertain", "for-trajectory", "proposed", "deferred",
                  "refuted", "rejected", "wontfix", "declined"}
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


def refresh_known(data_root: Path) -> int:
    """Rebuild known.tsv from every run's findings and trajectory items.
    Args:
        data_root: The folder holding all runs.
    Returns:
        The number of items listed.
    """
    rows = []
    for run in sorted(p for p in data_root.iterdir() if p.is_dir()):
        for name in (FINDINGS_FILE, TRAJECTORY_FILE):
            path = run / name
            if not path.is_file():
                continue
            for f in json.loads(path.read_text(encoding="utf-8")):
                if f.get("status") not in KNOWN_STATUSES:
                    continue
                where = f.get("file") or (f.get("files") or ["*"])[0]
                title = " ".join(str(f.get("title", "")).split())
                rows.append((where, str(f.get("line") or ""), f["status"], f.get("kind", ""),
                             f"{run.name}/{f.get('id', '')}", title))
    rows.sort()
    lines = [KNOWN_HEADER] + ["\t".join(r) for r in rows]
    (data_root / KNOWN_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


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
    nc = result.get("not_covered", {})
    out.append(f"not covered: {len(nc.get('files', []))} files unread; "
               f"{len(nc.get('hunt', []))} hunter items and {len(nc.get('lens', []))} lens items not reached")
    return "\n".join(out)


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
    ap.add_argument("--refresh-known", action="store_true", help="only rebuild known.tsv")
    args = ap.parse_args()
    root = args.root.resolve()
    data_root = root / inv.load_config(root, args.config)["data_root"]
    if args.refresh_known:
        sys.stdout.write(f"{refresh_known(data_root)} items in {data_root / KNOWN_FILE}\n")
        return 0
    if not args.run_dir or not (args.run_id or args.result):
        ap.error("--run-dir and one of --run-id / --result are required")
    run_dir = args.run_dir if args.run_dir.is_absolute() else root / args.run_dir
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
    inv.write_json(run_dir / REVIEW_FILE, {"meta": meta, **result})
    findings = [dict(f, run=run_dir.name) for f in result.get("findings", [])]
    inv.write_json(run_dir / FINDINGS_FILE, findings)
    recorded = update_ledger(data_root, result, run_dir.name)
    known = refresh_known(data_root)
    sys.stdout.write(f"saved {len(findings)} findings to {run_dir / FINDINGS_FILE}; ledger +{recorded} files; "
                     f"{known} known items\n{summary(result, meta)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
