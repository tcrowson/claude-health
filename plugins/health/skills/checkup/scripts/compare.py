"""Compare checkup runs of the same code: how much each finds, how much they agree, and how much all miss.
Two runs of one skill on one commit should find mostly the same bugs; where they do not, the overlap tells
how much a single run misses. `pair` groups the findings of several runs into issues (one issue = one
problem, with the finding ids each run reported for it) and marks weak matches for review; the main loop
checks the pairing by editing issues.json. `score` then reports, per pair of runs: overlap, the
capture-recapture estimate of how many real defects exist (Chapman), each run's estimated recall, how
often each lens's findings recur, and severity and verdict agreement. An issue may carry "truth" (real,
not-real or uncertain) from an independent check; with it, `score` reports each run's recall and
precision against that reference. `pair --reference` adds new runs to an existing issues.json.

    python compare.py pair A=<run dir> B=<run dir> [C=...] --out issues.json [--reference issues.json]
    python compare.py score issues.json [--out stats.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import inventory as inv

FINDINGS_FILE = "findings.json"
DEFECT = "defect"
IMPROVEMENT = "improvement"
REAL = "real"
SKIP_STATUSES = {"duplicate", "merged"}
KEPT_IMPROVEMENTS = {"accepted", "for-trajectory"}
SEVERITIES = ["critical", "high", "medium", "low"]
MIN_SCORE = 0.9           # below this a finding starts its own issue
SURE_SCORE = 1.6          # at or above this a match needs no review
NEAR_LINES = 25
WORD_RX = re.compile(r"[a-z_]{4,}")
SITE_RX = re.compile(r"\s*([\w./\\-]+\.\w+)")
STOP = frozenset({"when", "that", "with", "from", "this", "into", "then", "only", "each", "every", "same", "than",
                  "while", "after", "before", "never", "their", "which", "still", "does", "have", "over"})


def words(text: object) -> set[str]:
    """Return the distinctive lowercase words (four letters or more) of a text.
    Args:
        text: Any finding text.
    Returns:
        The words, stop words removed.
    """
    return {w for w in WORD_RX.findall(str(text or "").lower()) if w not in STOP}


def norm(path: object) -> str:
    """Normalize a repo-relative path for matching.
    Args:
        path: A path as a finding writes it.
    Returns:
        Lowercase, forward slashes.
    """
    return str(path or "").replace("\\", "/").lower()


def sites(f: dict) -> set[str]:
    """Return every file a finding cites: its own and those in other_sites.
    Args:
        f: A finding.
    Returns:
        Normalized paths.
    """
    out = {norm(f.get("file"))}
    for s in f.get("other_sites") or []:
        m = SITE_RX.match(str(s))
        if m:
            out.add(norm(m.group(1)))
    return out


def score(a: dict, b: dict) -> float:
    """Score how likely two findings describe the same problem.
    Args:
        a: A finding.
        b: A finding from another run.
    Returns:
        0 when they cannot match (different kind, no shared file); higher is likelier.
    """
    if a["kind"] != b["kind"] or not (sites(a) & sites(b)):
        return 0.0
    wa, wb = words(a.get("title")), words(b.get("title"))
    title = len(wa & wb) / max(1, min(len(wa), len(wb)))
    ea, eb = words(a.get("evidence")) | wa, words(b.get("evidence")) | wb
    body = len(ea & eb) / max(1, len(ea | eb))
    same_file = norm(a.get("file")) == norm(b.get("file"))
    near = same_file and abs((a.get("line") or 0) - (b.get("line") or 0)) <= NEAR_LINES
    return title + 2 * body + (0.4 if same_file else 0.15) + (0.3 if near else 0.0)


def is_real(f: dict) -> bool:
    """Tell whether a run judged a defect real (confirmed, or known with a real verdict).
    Args:
        f: A finding.
    Returns:
        True for a real defect.
    """
    return f["kind"] == DEFECT and (f.get("verdict") or {}).get("verdict") == REAL


def is_kept(f: dict) -> bool:
    """Tell whether a run kept a finding: a real defect or an accepted improvement.
    Args:
        f: A finding.
    Returns:
        True when kept.
    """
    return is_real(f) if f["kind"] == DEFECT else f.get("status") in KEPT_IMPROVEMENTS


def load_run(path: Path) -> dict[str, dict]:
    """Load a run's findings, dropping those folded into another finding.
    Args:
        path: The run folder.
    Returns:
        Finding id to finding.
    """
    found = json.loads((path / FINDINGS_FILE).read_text(encoding="utf-8"))
    return {f["id"]: f for f in found if f.get("status") not in SKIP_STATUSES}


def parse_runs(specs: list[str]) -> dict[str, Path]:
    """Parse LABEL=path arguments (a bare path is labeled by its folder name).
    Args:
        specs: The arguments.
    Returns:
        Label to run folder, in order.
    """
    runs: dict[str, Path] = {}
    for spec in specs:
        label, _, path = spec.rpartition("=")
        label = label or Path(path).name
        if label in runs:
            raise SystemExit(f"two runs labeled {label}: use LABEL=path")
        runs[label] = Path(path)
    return runs


def pair(runs: dict[str, Path], reference: dict | None, min_score: float) -> dict:
    """Group the findings of several runs into issues, greedily by match score.
    Args:
        runs: Label to run folder, for the runs to add.
        reference: An existing issues.json to extend, or None.
        min_score: The lowest score that joins a finding to an issue.
    Returns:
        The issues document.
    """
    doc = reference or {"runs": {}, "issues": []}
    known_runs = {label: Path(p) for label, p in doc["runs"].items()}
    clash = set(runs) & set(known_runs)
    if clash:
        raise SystemExit(f"already in the reference: {', '.join(sorted(clash))}")
    loaded = {label: load_run(p) for label, p in {**known_runs, **runs}.items()}
    issues = doc["issues"]
    for label in runs:
        members = [(i, loaded[r][fid]) for i, iss in enumerate(issues) for r, ids in iss["members"].items()
                   for fid in ids if fid in loaded[r]]
        cands = sorted(((score(m, f), i, fid) for fid, f in loaded[label].items() for i, m in members), reverse=True)
        best: dict[str, tuple[float, int]] = {}
        taken: set[int] = set()
        for s, i, fid in cands:
            if s < min_score or fid in best or i in taken:
                continue
            best[fid] = (s, i)
            taken.add(i)
        for fid, f in loaded[label].items():
            if fid in best:
                s, i = best[fid]
                issues[i]["members"][label] = [fid]
                issues[i].setdefault("scores", {})[label] = round(s, 2)
                issues[i]["review"] = issues[i].get("review", False) or s < SURE_SCORE
            else:
                issues.append({"id": "", "kind": f["kind"], "title": f.get("title"), "file": f.get("file"),
                               "line": f.get("line"), "members": {label: [fid]}, "scores": {}, "review": False})
    for n, iss in enumerate(issues, 1):
        iss["id"] = iss["id"] or f"X{n:03d}"
    doc["runs"] = {label: p.as_posix() for label, p in {**known_runs, **runs}.items()}
    return doc


def per_run(doc: dict) -> tuple[dict[str, dict[str, dict]], list[dict]]:
    """Resolve every issue's members to findings.
    Args:
        doc: The issues document.
    Returns:
        The loaded runs, and per issue: its fields plus found[label] = the run's findings for it.
    """
    loaded = {label: load_run(Path(p)) for label, p in doc["runs"].items()}
    rows = []
    for iss in doc["issues"]:
        found = {r: [loaded[r][fid] for fid in ids if fid in loaded[r]] for r, ids in iss["members"].items()}
        rows.append(dict(iss, found={r: fs for r, fs in found.items() if fs}))
    return loaded, rows


def top_severity(findings: list[dict]) -> str | None:
    """Return the most serious severity among a run's findings for one issue.
    Args:
        findings: The findings.
    Returns:
        The severity, or None.
    """
    ranks = [SEVERITIES.index(f["severity"]) for f in findings if f.get("severity") in SEVERITIES]
    return SEVERITIES[min(ranks)] if ranks else None


def chapman(n1: int, n2: int, m: int) -> float:
    """Estimate the population size from two samples and their overlap (Chapman's form of Lincoln-Petersen).
    Args:
        n1: Items the first run found.
        n2: Items the second run found.
        m: Items both found.
    Returns:
        The estimated number of items there are to find.
    """
    return (n1 + 1) * (n2 + 1) / (m + 1) - 1


def score_doc(doc: dict) -> dict:
    """Compute the agreement statistics of an adjudicated issues document.
    Args:
        doc: The issues document.
    Returns:
        The statistics.
    """
    loaded, rows = per_run(doc)
    labels = list(doc["runs"])
    defects = [r for r in rows if r["kind"] == DEFECT]
    kept = {lab: {r["id"] for r in defects if any(is_kept(f) for f in r["found"].get(lab, []))} for lab in labels}
    improved = {lab: {r["id"] for r in rows if r["kind"] == IMPROVEMENT
                      and any(is_kept(f) for f in r["found"].get(lab, []))} for lab in labels}
    out: dict = {"runs": {}, "pairs": [], "found_by": {}, "reference": None}
    for lab in labels:
        lens = Counter(f["lens"] for f in loaded[lab].values() if is_real(f))
        out["runs"][lab] = {"real_defects": len(kept[lab]), "by_lens": dict(sorted(lens.items())),
                            "improvements": len(improved[lab])}
    for x, y in combinations(labels, 2):
        m = len(kept[x] & kept[y])
        est = chapman(len(kept[x]), len(kept[y]), m)
        sev, status, repro = Counter(), Counter(), {}
        for r in defects:
            fx, fy = r["found"].get(x, []), r["found"].get(y, [])
            if not (fx and fy):
                continue
            kx, ky = any(map(is_kept, fx)), any(map(is_kept, fy))
            if kx and ky:
                sx, sy = top_severity(fx), top_severity(fy)
                if sx and sy:
                    gap = abs(SEVERITIES.index(sx) - SEVERITIES.index(sy))
                    sev["same" if gap == 0 else "one step" if gap == 1 else "two or more"] += 1
            elif kx or ky:
                other = fy if kx else fx
                status[f"real in one, {other[0].get('status')} in the other"] += 1
        for a, b in ((x, y), (y, x)):
            per_lens: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for r in defects:
                for f in r["found"].get(a, []):
                    if is_real(f):
                        per_lens[f["lens"]][0] += 1
                        per_lens[f["lens"]][1] += r["id"] in kept[b]
            repro[f"{a} found again by {b}"] = {k: f"{v[1]}/{v[0]}" for k, v in sorted(per_lens.items())}
        out["pairs"].append({
            "runs": [x, y], "both": m, "only": {x: len(kept[x]) - m, y: len(kept[y]) - m},
            "jaccard": round(m / max(1, len(kept[x] | kept[y])), 2), "estimated_total": round(est, 1),
            "estimated_recall": {x: round(len(kept[x]) / est, 2), y: round(len(kept[y]) / est, 2)} if est else {},
            "severity_agreement": dict(sev), "verdict_disagreement": dict(status), "reproduced_by_lens": repro,
            "improvements_both": len(improved[x] & improved[y]),
        })
    counts = Counter(sum(r["id"] in kept[lab] for lab in labels) for r in defects)
    out["found_by"] = {f"{k} of {len(labels)} runs": counts[k] for k in sorted(counts, reverse=True) if k}
    if len(labels) > 2:
        out["share_of_others"] = {}
        for lab in labels:
            others = set().union(*(kept[o] for o in labels if o != lab))
            out["share_of_others"][lab] = {"found": len(kept[lab] & others), "of": len(others),
                                           "new": len(kept[lab] - others)}
    truth = [r for r in defects if r.get("truth")]
    if truth:
        real = {r["id"] for r in truth if r["truth"] == REAL}
        judged = {r["id"] for r in truth if r["truth"] in (REAL, "not-real")}
        out["reference"] = {"real": len(real), "judged": len(judged), "runs": {
            lab: {"recall": round(len(kept[lab] & real) / max(1, len(real)), 2),
                  "precision": round(len(kept[lab] & real) / max(1, len(kept[lab] & judged)), 2),
                  "judged_kept": len(kept[lab] & judged)} for lab in labels}}
    return out


def render(stats: dict) -> str:
    """Format the statistics as text.
    Args:
        stats: The output of score_doc.
    Returns:
        The text.
    """
    lines = ["real defects per run:"]
    for lab, r in stats["runs"].items():
        lens = ", ".join(f"{k} {v}" for k, v in r["by_lens"].items())
        lines.append(f"  {lab}: {r['real_defects']} ({lens}); {r['improvements']} improvements kept")
    for p in stats["pairs"]:
        x, y = p["runs"]
        lines.append(f"{x} vs {y}: both {p['both']}, only {x} {p['only'][x]}, only {y} {p['only'][y]}; "
                     f"overlap {p['jaccard']:.0%}; estimated total {p['estimated_total']}; estimated recall "
                     + ", ".join(f"{k} {v:.0%}" for k, v in p["estimated_recall"].items()))
        for k, v in p["reproduced_by_lens"].items():
            lines.append(f"  {k}: " + ", ".join(f"{lens} {n}" for lens, n in v.items()))
        lines.append(f"  severity on shared defects: {p['severity_agreement'] or 'none shared'}")
        if p["verdict_disagreement"]:
            lines.append(f"  verdict disagreements: {p['verdict_disagreement']}")
        lines.append(f"  improvements kept by both: {p['improvements_both']}")
    lines.append(f"found by: {stats['found_by']}")
    for lab, s in stats.get("share_of_others", {}).items():
        lines.append(f"  {lab} found {s['found']} of the {s['of']} real defects the other runs found, and {s['new']} "
                     f"nobody else did")
    ref = stats["reference"]
    if ref:
        lines.append(f"reference: {ref['real']} real of {ref['judged']} judged")
        for lab, r in ref["runs"].items():
            lines.append(f"  {lab}: recall {r['recall']:.0%}, precision {r['precision']:.0%} "
                         f"({r['judged_kept']} judged findings)")
    return "\n".join(lines)


def main() -> int:
    """Parse arguments and run pair or score.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="group several runs' findings into issues")
    p.add_argument("runs", nargs="+", help="LABEL=<run folder> (holds findings.json)")
    p.add_argument("--out", type=Path, required=True, help="write issues.json here")
    p.add_argument("--reference", type=Path, help="an issues.json to extend with these runs")
    p.add_argument("--min", type=float, default=MIN_SCORE, help="lowest match score that joins an issue")
    s = sub.add_parser("score", help="agreement statistics of an adjudicated issues.json")
    s.add_argument("issues", type=Path)
    s.add_argument("--out", type=Path, help="also write the statistics as JSON")
    args = ap.parse_args()
    if args.cmd == "pair":
        ref = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
        doc = pair(parse_runs(args.runs), ref, args.min)
        inv.write_json(args.out, doc)
        shared = sum(len(i["members"]) > 1 for i in doc["issues"])
        review = sum(bool(i.get("review")) for i in doc["issues"])
        sys.stdout.write(f"{len(doc['issues'])} issues from {len(doc['runs'])} runs; {shared} found by more than "
                         f"one run; {review} matches to review (marked \"review\": true) in {args.out}\n")
        return 0
    stats = score_doc(json.loads(args.issues.read_text(encoding="utf-8")))
    if args.out:
        inv.write_json(args.out, stats)
    sys.stdout.write(render(stats) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
