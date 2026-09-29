"""The patient chart: every condition a checkup has found, carried across visits, with its status and vitals.
Each visit's findings are matched to the conditions already on the chart (the same scoring as compare.py): a
sure match adds a sighting; a weak one opens a new condition linked to its candidate ("maybe C0042") for the
main loop to decide, since a weak match is as often a different bug; anything else opens a new condition with a
stable id (C0001). Statuses move only in known ways: a treated condition seen again is reopened, a follow-up
visit's re-check marks it cured, and a decision (deferred, wontfix) stands until someone changes it. The chart is
the only source of the known-items file, so an open condition can never drop out between visits, and /treatment
works from it. Each visit also appends a row of vitals: open serious conditions, new serious ones, reopened,
cured, cost.

    python chart.py add --run <data_root>/<date>          merge a saved run (idempotent; save_run.py calls it)
    python chart.py init                                   build the chart from every run folder, oldest first
    python chart.py set C0042 treated --commit abc --base def [--note "..."]
    python chart.py merge C0042 C0077                      fold C0077 into C0042 (one condition seen twice)
    python chart.py distinct C0077                         C0077 is not the same as its possible match
    python chart.py detach C0042 <run>/<finding id>        split a wrong sure match off into its own condition
    python chart.py status                                 vitals, open serious conditions, possible matches
    python chart.py known                                  rewrite known.tsv from the chart
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import compare
import inventory as inv

CHART_FILE = "chart.json"
FINDINGS_FILE = "findings.json"
TRAJECTORY_FILE = "trajectory.json"
REVIEW_FILE = "review.json"
METRICS_FILE = "metrics.json"
CLONES_FILE = "clones.json"
CHART_VERSION = 1
SERIOUS = ("critical", "high")
MINOR = ("low",)
SEVERITIES = compare.SEVERITIES
VITALS_ROWS = 8
# Condition statuses. Open ones are what treatment works on; closed ones are settled.
OPEN = ("open", "reopened", "watch", "documented", "uncertain", "proposed")
TREATED = "treated"
CURED = "cured"
REOPENED = "reopened"
DECIDED = ("deferred", "wontfix")
DISMISSED = ("refuted", "rejected")
STATUSES = (*OPEN, TREATED, CURED, *DECIDED, *DISMISSED)
# The known list hides everything except treated and cured conditions, so a treated bug that comes back is
# reported again (and reopens its condition) while open ones are not re-found at the cost of a whole visit.
NOT_KNOWN = (TREATED, CURED)
SKIP_FINDINGS = {"duplicate", "merged", "unverified"}
DETAIL_KEYS = ("evidence", "failure_scenario", "fix", "change", "impact", "effort", "risk", "strength",
               "measurement", "trigger_frequency", "consequence", "category", "lens", "other_sites",
               "symptoms", "requirement", "options", "path", "files")
RECHECK_STATUS = {"cured": CURED, "still_present": REOPENED}


def today() -> str:
    """Today's local date.
    Returns:
        The date as YYYY-MM-DD.
    """
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def load_chart(data_root: Path) -> dict:
    """Load the chart, or an empty one.
    Args:
        data_root: The folder holding all runs.
    Returns:
        The chart document.
    """
    path = data_root / CHART_FILE
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": CHART_VERSION, "next_id": 1, "conditions": [], "visits": []}


def save_chart(data_root: Path, chart: dict) -> None:
    """Write the chart.
    Args:
        data_root: The folder holding all runs.
        chart: The chart document.
    """
    inv.write_json(data_root / CHART_FILE, chart)


def initial_status(item: dict) -> str | None:
    """Map a finding's or a design card's status to a condition status.
    Args:
        item: A finding (defect or improvement) or a trajectory card.
    Returns:
        The condition status, or None when the item is not charted (a duplicate, merged or unverified).
    """
    status = item.get("status")
    if status in SKIP_FINDINGS:
        return None
    if status == "fixed":
        return TREATED
    if status in (*DECIDED, *DISMISSED, CURED, TREATED):
        return status
    if status == "declined":
        return "wontfix"
    if status == "known":
        return "documented"
    if status in ("uncertain", "proposed", "for-trajectory"):
        return "proposed" if status == "for-trajectory" else status
    if item.get("kind") == "defect":
        return "watch" if item.get("severity") in MINOR else "open"
    return "open"          # an accepted improvement or an accepted design card


def as_match_input(item: dict) -> dict:
    """Shape a finding, card or condition for compare.score.
    Args:
        item: The item.
    Returns:
        The fields compare.score reads.
    """
    files = item.get("files") or []
    return {"kind": item.get("kind"), "title": item.get("title"), "evidence": item.get("evidence") or
            " ".join(str(s) for s in item.get("symptoms") or []), "file": item.get("file") or (files[0] if files else ""),
            "line": item.get("line"), "other_sites": item.get("other_sites") or files[1:]}


def refresh_details(cond: dict, item: dict, run: str) -> None:
    """Copy an item's descriptive fields onto its condition (the latest sighting describes it best), unless
    the item comes from a run older than the one that last described it (a re-added older run).
    Args:
        cond: The condition.
        item: The finding or card.
        run: The run it came from (run folders are named by date, so names sort by age).
    """
    if run < cond.get("last_seen", ""):
        return
    cond["title"] = item.get("title", cond.get("title"))
    cond["file"] = item.get("file") or (item.get("files") or [cond.get("file")])[0]
    cond["line"] = item.get("line", cond.get("line"))
    if item.get("severity"):
        cond["severity"] = item["severity"]
    cond["details"] = {k: item[k] for k in DETAIL_KEYS if item.get(k) not in (None, "", [])}
    cond["last_seen"] = run


def new_condition(chart: dict, item: dict, run: str, status: str) -> dict:
    """Open a condition for an item no condition matches.
    Args:
        chart: The chart.
        item: The finding or card.
        run: The run it came from.
        status: Its starting status.
    Returns:
        The condition.
    """
    cond = {"id": f"C{chart['next_id']:04d}", "kind": item.get("kind"), "status": status, "status_by": "run",
            "first_seen": run, "sightings": [], "notes": []}
    chart["next_id"] += 1
    refresh_details(cond, item, run)
    chart["conditions"].append(cond)
    return cond


def sighting(item: dict, run: str, score: float | None) -> dict:
    """Record one visit's report of a condition.
    Args:
        item: The finding or card.
        run: The run.
        score: The match score, or None for the finding that opened the condition.
    Returns:
        The sighting.
    """
    s = {"run": run, "id": item.get("id"), "status": item.get("status"), "severity": item.get("severity")}
    if score is not None:
        s["score"] = round(score, 2)
    return s


def transition(cond: dict, item: dict, run: str, first_visit: bool) -> str | None:
    """Move a condition's status after a new sighting.
    Args:
        cond: The condition.
        item: The new sighting's finding.
        run: Its run.
        first_visit: Whether this run opened the condition (its status is still the run's to set).
    Returns:
        A note describing the change, or None.
    """
    seen = initial_status(item)
    was = cond["status"]
    if seen is None:
        return None
    if first_visit and cond.get("status_by") == "run":
        cond["status"] = seen
        return None
    live = seen in ("open", "watch", REOPENED)
    if was in (TREATED, CURED) and live:
        cond["status"], cond["status_by"] = REOPENED, "run"
        return f"{run}: seen again after treatment: reopened"
    if was in (*DISMISSED, "uncertain") and live:
        cond["status"], cond["status_by"] = seen, "run"
        return f"{run}: confirmed again after being {was}"
    if was in ("open", "watch") and live and cond.get("status_by") == "run":
        cond["status"] = seen
    return None


def add_run(chart: dict, run_dir: Path, kind: str | None = None) -> dict:
    """Merge one saved run into the chart. Re-adding a run updates its sightings instead of duplicating them.
    Args:
        chart: The chart.
        run_dir: The run folder (findings.json, and trajectory.json when the main loop wrote one).
        kind: The visit kind (baseline, second-opinion, follow-up, routine), recorded with the vitals.
    Returns:
        This visit's counts.
    """
    run = run_dir.name
    items = []
    for name in (FINDINGS_FILE, TRAJECTORY_FILE):
        path = run_dir / name
        if path.is_file():
            items += json.loads(path.read_text(encoding="utf-8"))
    by_sighting = {(s["run"], s["id"]): c for c in chart["conditions"] for s in c["sightings"]}
    counts = {"matched": 0, "review": 0, "skipped": 0}
    fresh = []
    for item in items:
        if initial_status(item) is None:
            counts["skipped"] += 1
            continue
        cond = by_sighting.get((run, item.get("id")))
        if cond is None:
            fresh.append(item)
            continue
        for s in cond["sightings"]:
            if s["run"] == run and s["id"] == item.get("id"):
                s.update(status=item.get("status"), severity=item.get("severity"))
        refresh_details(cond, item, run)
        transition(cond, item, run, cond["first_seen"] == run)
    pool = [c for c in chart["conditions"] if not any(s["run"] == run for s in c["sightings"])]
    ranked = sorted(((compare.score(as_match_input(c), as_match_input(it)), n, i)
                     for n, it in enumerate(fresh) for i, c in enumerate(pool)), reverse=True)
    best: dict[int, tuple[float, int]] = {}
    taken: set[int] = set()
    for score, n, i in ranked:
        if score >= compare.MIN_SCORE and n not in best and i not in taken:
            best[n] = (score, i)
            taken.add(i)
    for n, item in enumerate(fresh):
        score, i = best.get(n, (0.0, -1))
        if score >= compare.SURE_SCORE:
            cond = pool[i]
            cond["sightings"].append(sighting(item, run, score))
            note = transition(cond, item, run, False)
            if note:
                cond["notes"].append(note)
            refresh_details(cond, item, run)
            counts["matched"] += 1
            continue
        # A weak match is as often a different bug as the same one: chart it as new, linked to the candidate,
        # so nothing is hidden as "seen before" and nothing treated is reopened until the main loop decides.
        cond = new_condition(chart, item, run, initial_status(item))
        cond["sightings"].append(sighting(item, run, None))
        if n in best:
            cond["maybe"] = {"id": pool[i]["id"], "score": round(score, 2)}
            counts["review"] += 1
    rechecked = apply_rechecks(chart, run_dir)
    row = record_visit(chart, run_dir, kind)
    return dict(counts, added=row["new"], new_serious=row["new_serious"], reopened=row["reopened"],
                **{f"recheck_{k}": v for k, v in rechecked.items()})


def apply_rechecks(chart: dict, run_dir: Path) -> dict:
    """Apply a follow-up visit's re-checks of treated conditions: cured, or reopened.
    Args:
        chart: The chart.
        run_dir: The run folder; its review.json holds the workflow's rechecks.
    Returns:
        Counts of cured and reopened conditions.
    """
    out = {"cured": 0, "reopened": 0, "uncertain": 0}
    path = run_dir / REVIEW_FILE
    if not path.is_file():
        return out
    by_id = {c["id"]: c for c in chart["conditions"]}
    for r in json.loads(path.read_text(encoding="utf-8")).get("rechecks", []):
        cond = by_id.get(r.get("condition"))
        new = RECHECK_STATUS.get(r.get("verdict"))
        if cond is None or cond["status"] != TREATED:
            continue
        if new is None:
            out["uncertain"] += 1
            cond["notes"].append(f"{run_dir.name}: re-check uncertain: {r.get('reason', '')}")
            continue
        cond["status"], cond["status_by"] = new, "recheck"
        cond["notes"].append(f"{run_dir.name}: re-check {r.get('verdict')}: {r.get('reason', '')}")
        out["cured" if new == CURED else "reopened"] += 1
    return out


def snapshot(chart: dict) -> dict:
    """Count the chart's conditions by what matters for health.
    Args:
        chart: The chart.
    Returns:
        Open serious, open, watch, treated defects awaiting a follow-up, and cured.
    """
    conds = chart["conditions"]
    defects = [c for c in conds if c["kind"] == "defect"]
    return {"open_serious": sum(c["status"] in ("open", REOPENED) and c.get("severity") in SERIOUS for c in defects),
            "open_defects": sum(c["status"] in ("open", REOPENED) for c in defects),
            "watch": sum(c["status"] == "watch" for c in defects),
            "open_improvements": sum(c["status"] == "open" for c in conds if c["kind"] == "improvement"),
            "awaiting_follow_up": sum(c["status"] == TREATED for c in defects),
            "cured": sum(c["status"] == CURED for c in conds)}


def run_metrics(run_dir: Path) -> dict:
    """Pick the headline code metrics a run measured, when it measured them.
    Args:
        run_dir: The run folder.
    Returns:
        A few numbers that trend: long functions, import cycles, clone groups, untested risky files.
    """
    out = {}
    path = run_dir / METRICS_FILE
    if path.is_file():
        m = json.loads(path.read_text(encoding="utf-8"))
        out["functions_over_100"] = m.get("over_100")
        out["import_cycles"] = len(m.get("import_cycles") or [])
        out["forbidden_imports"] = len(m.get("forbidden_imports") or [])
        out["untested_risky"] = len(m.get("untested_risky") or [])
    path = run_dir / CLONES_FILE
    if path.is_file():
        c = json.loads(path.read_text(encoding="utf-8"))
        out["clone_groups"] = len(c.get("groups", c) if isinstance(c, dict) else c)
    return out


def record_visit(chart: dict, run_dir: Path, kind: str | None) -> dict:
    """Add or replace this run's row of vitals. Every count is derived from the chart, so re-adding a run
    (after the main loop re-grades its findings) recomputes the row instead of adding to it.
    Args:
        chart: The chart.
        run_dir: The run folder.
        kind: The visit kind, or None to keep the one already recorded.
    Returns:
        The row.
    """
    run = run_dir.name
    meta, head = {}, None
    path = run_dir / REVIEW_FILE
    if path.is_file():
        review = json.loads(path.read_text(encoding="utf-8"))
        meta, head = review.get("meta", {}), review.get("head")
    old = next((v for v in chart["visits"] if v["run"] == run), {})
    born = [c for c in chart["conditions"] if c["first_seen"] == run]

    def noted(text: str) -> int:
        """Count the conditions carrying this run's note that starts with text.
        Args:
            text: The note's start after the run name.
        Returns:
            The count.
        """
        return sum(any(n.startswith(f"{run}: {text}") for n in c["notes"]) for c in chart["conditions"])

    row = {"run": run, "kind": kind or old.get("kind") or "baseline", "head": head,
           "date": old.get("date") or today(),
           "new": len(born),
           "new_serious": sum(c["kind"] == "defect" and c.get("severity") in SERIOUS and c["status"] in OPEN for c in born),
           "reopened": noted("seen again after treatment") + noted("re-check still_present"),
           "cured": noted("re-check cured"),
           "agents": meta.get("agentsStarted") or meta.get("agentCount"), "tokens": meta.get("totalTokens"),
           "minutes": round((meta.get("durationMs") or 0) / 60000, 1) or None,
           **snapshot(chart), "metrics": run_metrics(run_dir)}
    chart["visits"] = [v for v in chart["visits"] if v["run"] != run] + [row]
    return row


def find(chart: dict, cid: str) -> dict:
    """Look up a condition by id.
    Args:
        chart: The chart.
        cid: E.g. C0042.
    Returns:
        The condition.
    """
    cond = next((c for c in chart["conditions"] if c["id"] == cid), None)
    if cond is None:
        raise SystemExit(f"no condition {cid}")
    return cond


def set_status(chart: dict, cid: str, status: str, commit: str | None, base: str | None, note: str | None,
               severity: str | None) -> None:
    """Set a condition's status by hand (treatment, a decision, a re-grade).
    Args:
        chart: The chart.
        cid: The condition id.
        status: The new status.
        commit: The fixing commit, for treated.
        base: The commit before the treatment, which the follow-up visit compares against.
        note: Why, kept in the condition's notes.
        severity: A corrected severity.
    """
    cond = find(chart, cid)
    if status == TREATED and not commit:
        raise SystemExit("treated needs --commit (and --base, the head before the treatment)")
    cond["status"], cond["status_by"] = status, "manual"
    if status == TREATED:
        cond["treated"] = {"commit": commit, "base": base, "date": today()}
    if severity:
        cond["severity"] = severity
    if note or status == TREATED:
        cond["notes"].append(f"{today()}: {status}" + (f": {note}" if note else ""))


def merge(chart: dict, keep: str, drop: str) -> None:
    """Fold one condition into another (the same problem charted twice).
    Args:
        chart: The chart.
        keep: The condition that stays.
        drop: The condition folded into it.
    """
    a, b = find(chart, keep), find(chart, drop)
    a["sightings"] += b["sightings"]
    a["notes"] += b["notes"] + [f"merged {drop} into {keep}"]
    if SEVERITIES.index(b.get("severity") or "low") < SEVERITIES.index(a.get("severity") or "low"):
        a["severity"] = b["severity"]
    later = max(s["run"] for s in b["sightings"]) if b["sightings"] else ""
    if a["status"] in (TREATED, CURED) and b["status"] in ("open", "watch", REOPENED) and later > a.get("last_seen", ""):
        a["status"], a["status_by"] = REOPENED, "run"
        a["notes"].append(f"{later}: seen again after treatment: reopened (merged {drop})")
    for c in chart["conditions"]:
        if (c.get("maybe") or {}).get("id") in (keep, drop):
            c.pop("maybe")
    a.pop("maybe", None)
    chart["conditions"].remove(b)


def distinct(chart: dict, cid: str) -> None:
    """Record that a weakly matched condition is not the same problem as its candidate.
    Args:
        chart: The chart.
        cid: The condition holding the "maybe" link.
    """
    cond = find(chart, cid)
    maybe = cond.pop("maybe", None)
    if maybe is None:
        raise SystemExit(f"{cid} has no possible match to dismiss")
    cond["notes"].append(f"not the same as {maybe['id']} (checked)")


def detach(chart: dict, cid: str, ref: str) -> str:
    """Split a wrongly matched sighting off into a condition of its own.
    Args:
        chart: The chart.
        cid: The condition holding the sighting.
        ref: The sighting as <run>/<finding id>.
    Returns:
        The new condition's id.
    """
    cond = find(chart, cid)
    run, _, fid = ref.partition("/")
    s = next((s for s in cond["sightings"] if s["run"] == run and s["id"] == fid), None)
    if s is None or len(cond["sightings"]) < 2:
        raise SystemExit(f"{cid} has no other sighting {ref} to detach")
    cond["sightings"].remove(s)
    item = {"kind": cond["kind"], "title": cond["title"], "status": s["status"], "severity": s.get("severity"),
            "file": cond.get("file"), "line": cond.get("line"), "id": fid}
    new = new_condition(chart, item, run, initial_status(item) or "open")
    s.pop("score", None)
    s.pop("review", None)
    new["sightings"].append(s)
    new["notes"].append(f"detached from {cid}; `chart.py add --run` on {run} refreshes its title and details")
    return new["id"]


def known_rows(chart: dict) -> list[tuple[str, ...]]:
    """List the conditions agents must not re-report: every one except treated and cured.
    Args:
        chart: The chart.
    Returns:
        Rows of file, line, status, kind, id, title.
    """
    rows = []
    for c in chart["conditions"]:
        if c["status"] in NOT_KNOWN:
            continue
        rows.append((c.get("file") or "*", str(c.get("line") or ""), c["status"], c["kind"] or "", c["id"],
                     " ".join(str(c.get("title", "")).split())))
    return sorted(rows)


def write_known(data_root: Path, chart: dict) -> int:
    """Rewrite known.tsv from the chart.
    Args:
        data_root: The folder holding all runs.
        chart: The chart.
    Returns:
        The number of items listed.
    """
    rows = known_rows(chart)
    inv.write_text(data_root / inv.KNOWN_FILE, inv.LF.join([inv.KNOWN_HEADER] + ["\t".join(r) for r in rows]) + inv.LF)
    return len(rows)


def run_folders(data_root: Path) -> list[Path]:
    """List the run folders that hold findings, oldest first.
    Args:
        data_root: The folder holding all runs.
    Returns:
        The folders.
    """
    return sorted(p for p in data_root.iterdir() if p.is_dir() and (p / FINDINGS_FILE).is_file())


def status_text(chart: dict) -> str:
    """Format the vitals trend and the open serious conditions.
    Args:
        chart: The chart.
    Returns:
        The text.
    """
    cols = ("open_serious", "new_serious", "reopened", "cured", "open_defects", "watch", "awaiting_follow_up", "agents")
    lines = ["visit                kind            " + "  ".join(f"{c[:12]:>12}" for c in cols)]
    for v in chart["visits"][-VITALS_ROWS:]:
        lines.append(f"{v['run']:20} {v['kind']:15} " + "  ".join(f"{v.get(c) if v.get(c) is not None else '-'!s:>12}"
                                                            for c in cols))
    now = snapshot(chart)
    lines.append("now: " + ", ".join(f"{k} {v}" for k, v in now.items()))
    serious = [c for c in chart["conditions"] if c["kind"] == "defect" and c["status"] in ("open", REOPENED)
               and c.get("severity") in SERIOUS]
    if serious:
        lines.append("open serious conditions:")
        lines += [f"  {c['id']} [{c['status']}] {c['file']}:{c.get('line')}  {c['title']}" for c in serious]
    maybe = [c for c in chart["conditions"] if c.get("maybe")]
    if maybe:
        by_id = {c["id"]: c for c in chart["conditions"]}
        lines.append(f"{len(maybe)} possible matches to decide (`merge <old> <new>` when the same, `distinct <new>` "
                     "when not):")
        for c in maybe:
            old = by_id.get(c["maybe"]["id"], {})
            lines.append(f"  {c['id']} {c.get('file')}:{c.get('line')} {c['title']}\n"
                         f"    maybe {old.get('id')} [{old.get('status')}] {old.get('file')}:{old.get('line')} "
                         f"{old.get('title')} (score {c['maybe']['score']})")
    return "\n".join(lines)


def main() -> int:
    """Parse arguments and run one chart command.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="merge a saved run into the chart")
    a.add_argument("--run", type=Path, required=True)
    a.add_argument("--visit", choices=("baseline", "second-opinion", "follow-up", "routine"))
    sub.add_parser("init", help="build the chart from every run folder, oldest first")
    s = sub.add_parser("set", help="set a condition's status")
    s.add_argument("condition")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--commit")
    s.add_argument("--base")
    s.add_argument("--note")
    s.add_argument("--severity", choices=SEVERITIES)
    m = sub.add_parser("merge", help="fold one condition into another")
    m.add_argument("keep")
    m.add_argument("drop")
    d = sub.add_parser("detach", help="split a sighting off into its own condition")
    d.add_argument("condition")
    d.add_argument("sighting", help="<run>/<finding id>")
    x = sub.add_parser("distinct", help="a possible match is a different problem")
    x.add_argument("condition")
    sub.add_parser("status", help="vitals and open serious conditions")
    sub.add_parser("known", help="rewrite known.tsv from the chart")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # titles carry arrows and quotes; consoles may not
    root = args.root.resolve()
    data_root = root / inv.load_config(root, args.config)["data_root"]
    chart = load_chart(data_root)
    w = sys.stdout.write
    if args.cmd == "add":
        run_dir = args.run if args.run.is_absolute() else root / args.run
        counts = add_run(chart, run_dir, args.visit)
        w(f"{run_dir.name}: {counts['added']} new conditions ({counts['new_serious']} serious; {counts['review']} "
          f"possible matches to decide), {counts['matched']} seen before, {counts['reopened']} reopened\n")
    elif args.cmd == "init":
        if chart["conditions"]:
            raise SystemExit(f"{data_root / CHART_FILE} already has conditions; add runs one at a time instead")
        for run_dir in run_folders(data_root):
            counts = add_run(chart, run_dir)
            w(f"{run_dir.name}: {counts['added']} new ({counts['review']} possible matches), "
              f"{counts['matched']} seen before\n")
    elif args.cmd == "set":
        set_status(chart, args.condition, args.status, args.commit, args.base, args.note, args.severity)
    elif args.cmd == "merge":
        merge(chart, args.keep, args.drop)
    elif args.cmd == "distinct":
        distinct(chart, args.condition)
    elif args.cmd == "detach":
        w(f"new condition {detach(chart, args.condition, args.sighting)}\n")
    if args.cmd in ("add", "init", "set", "merge", "distinct", "detach"):
        save_chart(data_root, chart)
        n = write_known(data_root, chart)
        w(f"chart: {len(chart['conditions'])} conditions; known.tsv: {n} items\n")
    if args.cmd == "status":
        w(status_text(chart) + "\n")
    if args.cmd == "known":
        w(f"{write_known(data_root, chart)} items in {data_root / inv.KNOWN_FILE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
