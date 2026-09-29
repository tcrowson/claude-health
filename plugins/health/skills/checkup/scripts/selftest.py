"""Self-test for the checkup scripts: run each one on the planted fixtures and check the known answers.
The fixtures hold, in Python, JavaScript, C++ and Go: renamed clones, long functions, swallowed errors,
import cycles, a pass-through, a forbidden import and an untested file. History runs on a throwaway git
repo built from them; save_run on a stub result. Everything is written to a temp folder. The workflow's
own harness (selftest_workflow.mjs) runs too when node is on PATH.

    python selftest.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
CONFIG = FIXTURES / "config.json"
GIT_ID = ["-c", "user.name=checkup-selftest", "-c", "user.email=selftest@example.invalid"]
NODE = "node"
WORKFLOW_HARNESS = HERE / "selftest_workflow.mjs"


class Checks:
    """Collects pass/fail lines."""

    def __init__(self) -> None:
        """Start with no failures."""
        self.failed = 0

    def check(self, ok: bool, what: str) -> None:
        """Record one check.
        Args:
            ok: Whether it passed.
            what: What was checked.
        """
        self.failed += not ok
        sys.stdout.write(f"  {'ok  ' if ok else 'FAIL'}  {what}\n")


def run(script: str, *args: str, root: Path = FIXTURES, config: Path = CONFIG) -> str:
    """Run one checkup script on a root.
    Args:
        script: The script file name.
        *args: Extra arguments.
        root: The repo root to analyze.
        config: The config to use.
    Returns:
        Its stdout.
    """
    cmd = [sys.executable, str(HERE / script), "--root", str(root), "--config", str(config), *args]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{script} failed ({done.returncode}):\n{done.stderr}")
    return done.stdout


def sites_cover(groups: list[dict], *files: str) -> bool:
    """Tell whether one clone group has a site in each of the files.
    Args:
        groups: Clone groups from clones.json.
        *files: Repo-relative paths.
    Returns:
        True when some group spans all the files.
    """
    return any(all(any(s.startswith(f + ":") for s in g["sites"]) for f in files) for g in groups)


def test_partition(c: Checks, tmp: Path) -> None:
    """Check sizing modes, the cap and import-affinity merging.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("partition\n")
    run("partition.py", "--out", str(tmp / "plan.json"))
    plan = json.loads((tmp / "plan.json").read_text(encoding="utf-8"))
    c.check(plan["totals"]["files"] == 12, f"12 source files in scope, tests excluded (got {plan['totals']['files']})")
    c.check(plan["mode"] == "inline", f"a tiny tree runs inline (got {plan['mode']})")
    run("partition.py", "--cap", "160", "--max-readers", "2", "--out", str(tmp / "plan2.json"))
    plan = json.loads((tmp / "plan2.json").read_text(encoding="utf-8"))
    units = plan["units"] + plan["uncovered"]
    c.check(plan["mode"] == "rolling" and len(plan["units"]) == 2 and plan["uncovered"],
            f"over the reader limit the run rolls and reports the rest (mode {plan['mode']}, {len(units)} units)")
    together = any({"src/core/helpers.py", "src/report/summary.py"} <= set(u["files"]) for u in units)
    c.check(together, "a small unit merges with the package it imports from")
    c.check(all(u["weight"] <= 160 for u in plan["units"]), "no unit exceeds the cap")
    args = plan["workflow_args"]
    c.check(args["units"] and all(len(u["files"]) == len(u["weights"]) for u in args["units"]),
            "workflow_args carry units with per-file weights")
    readers = [m for what, _, m in plan["agents"]["items"] if what.startswith(("readers", "follow-up"))]
    c.check(args["readerModel"] == "opus" and readers == ["opus", "opus"],
            "standard-tier readers run on Opus, and the plan counts them as Opus")
    run("partition.py", "--tier", "lean", "--cap", "160", "--out", str(tmp / "plan_lean.json"))
    lean = json.loads((tmp / "plan_lean.json").read_text(encoding="utf-8"))
    c.check(lean["workflow_args"]["readerModel"] == "sonnet", "lean-tier readers stay on Sonnet")
    c.check(not (FIXTURES / "out").exists(), "a plan written outside the data root creates nothing in the repo")
    repo = tmp / "plan_repo"
    shutil.copytree(FIXTURES, repo)
    run("partition.py", "--out", str(repo / "out" / "2026-09-27" / "plan.json"), root=repo, config=repo / "config.json")
    known = repo / "out" / "known.tsv"
    c.check(known.is_file() and known.read_bytes() == b"file\tline\tstatus\tkind\tid\ttitle\n",
            "the known-items file exists from the first run, header only, LF")
    script = repo / "out" / "2026-09-27" / "checkup.workflow.js"
    c.check(script.is_file() and b"\r" not in script.read_bytes(), "the workflow script is copied into the run folder, LF only")


def test_sites(c: Checks, tmp: Path) -> None:
    """Check that sites.py lists one site per function with its kinds, skips import lines and fills the plan.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("sites\n")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["risk_markers"] = {"python": {"async": r"\bWorkerThread\b"}}
    (tmp / "sites_config.json").write_text(json.dumps(config), encoding="utf-8")
    plan = tmp / "sites_plan.json"
    run("partition.py", "--out", str(plan), config=tmp / "sites_config.json")
    out = run("sites.py", "--plan", str(plan), config=tmp / "sites_config.json")
    units = json.loads(plan.read_text(encoding="utf-8"))["workflow_args"]["units"]
    sites = [s for u in units for s in u["sites"]]
    by = {(s["file"], s["function"]): s["kinds"] for s in sites}
    c.check(by.get(("src/core/scoring.py", "load")) == {"io": [31], "error": [32]},
            f"one site for load() with its file access and its handler ({by.get(('src/core/scoring.py', 'load'))})")
    c.check(by.get(("src/ui/card.py", "Card.refresh")) == {"async": [17]}, "a method is named Class.method")
    c.check(not any(s["file"] == "src/ui/card.py" and any(3 in lines for lines in s["kinds"].values()) for s in sites),
            "a marker on an import line is not a site")
    c.check(all([s["id"] for s in u["sites"]] == [f"S{n:03d}" for n in range(1, len(u["sites"]) + 1)] for u in units)
            and "sites (functions with" in out, "each unit's sites are numbered in order and counted")


def finding(fid: str, title: str, file: str, line: int, verdict: str, severity: str = "medium",
            lens: str = "readers") -> dict:
    """Build a defect finding for the compare test.
    Args:
        fid: Its id.
        title: Its title.
        file: Its file.
        line: Its line.
        verdict: real, not_real or uncertain.
        severity: Its severity.
        lens: The lens that found it.
    Returns:
        The finding.
    """
    status = {"real": "confirmed", "not_real": "refuted"}.get(verdict, "uncertain")
    return {"id": fid, "kind": "defect", "lens": lens, "title": title, "file": file, "line": line, "evidence": title,
            "severity": severity, "status": status, "verdict": {"verdict": verdict}}


def test_compare(c: Checks, tmp: Path) -> None:
    """Check pairing across runs, the agreement statistics and scoring against a reference.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("compare\n")
    runs = {
        "A": [finding("a1", "Stale cache survives the document switch", "src/a.py", 10, "real", "high"),
              finding("a2", "Timer is not stopped on close", "src/b.py", 5, "real"),
              finding("a3", "Unchecked write result loses the save", "src/c.py", 7, "real", lens="failure")],
        "B": [finding("b1", "Stale cache survives a switch of document", "src/a.py", 12, "real", "medium"),
              finding("b2", "Timer is not stopped on close", "src/b.py", 6, "not_real"),
              finding("b3", "Export path ignores the chosen folder", "src/d.py", 1, "real")],
        "C": [finding("c1", "Export path ignores the chosen folder", "src/d.py", 2, "real")],
    }
    for label, found in runs.items():
        (tmp / "cmp" / label).mkdir(parents=True)
        (tmp / "cmp" / label / "findings.json").write_text(json.dumps(found), encoding="utf-8")
    cmp = [sys.executable, str(HERE / "compare.py")]
    subprocess.run([*cmp, "pair", f"A={tmp / 'cmp' / 'A'}", f"B={tmp / 'cmp' / 'B'}", "--out", str(tmp / "issues.json")],
                   check=True, capture_output=True)
    doc = json.loads((tmp / "issues.json").read_text(encoding="utf-8"))
    joined = {tuple(sorted(i["members"].get(r, [""])[0] for r in ("A", "B"))) for i in doc["issues"]}
    c.check({("a1", "b1"), ("a2", "b2")} <= joined and len(doc["issues"]) == 4,
            f"matching findings pair across runs; the rest stand alone ({len(doc['issues'])} issues)")
    for i in doc["issues"]:
        i["truth"] = {"a1": "real", "a3": "not-real"}.get(i["members"].get("A", [""])[0])
    (tmp / "issues.json").write_text(json.dumps(doc), encoding="utf-8")
    out = subprocess.run([*cmp, "score", str(tmp / "issues.json"), "--out", str(tmp / "stats.json")],
                         check=True, capture_output=True, text=True).stdout
    stats = json.loads((tmp / "stats.json").read_text(encoding="utf-8"))
    p = stats["pairs"][0]
    c.check(p["both"] == 1 and p["estimated_total"] == 5.0, f"overlap and the Chapman estimate (both {p['both']}, "
            f"total {p['estimated_total']}; (3+1)(2+1)/(1+1)-1 = 5)")
    c.check(p["verdict_disagreement"] == {"real in one, refuted in the other": 1} and p["severity_agreement"] == {"one step": 1},
            "a defect real in one run and refuted in the other, and a one-step severity gap, are counted")
    ref = stats["reference"]["runs"]["A"]
    c.check(ref["recall"] == 1.0 and ref["precision"] == 0.5, f"recall and precision against the reference ({ref})")
    c.check("estimated recall" in out, "score prints a text summary")
    subprocess.run([*cmp, "pair", f"C={tmp / 'cmp' / 'C'}", "--reference", str(tmp / "issues.json"), "--out",
                    str(tmp / "issues2.json")], check=True, capture_output=True)
    doc2 = json.loads((tmp / "issues2.json").read_text(encoding="utf-8"))
    c.check(any(i["members"].get("C") == ["c1"] and i["members"].get("B") == ["b3"] for i in doc2["issues"])
            and len(doc2["issues"]) == 4, "a new run joins the reference's issues")
    subprocess.run([*cmp, "score", str(tmp / "issues2.json"), "--out", str(tmp / "stats2.json")], check=True,
                   capture_output=True)
    share = json.loads((tmp / "stats2.json").read_text(encoding="utf-8"))["share_of_others"]["C"]
    c.check(share == {"found": 1, "of": 4, "new": 0}, f"a new run's share of what the other runs found ({share})")


def test_metrics(c: Checks, tmp: Path) -> None:
    """Check the metrics against the planted defects.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("metrics\n")
    run("metrics.py", "--out", str(tmp / "metrics.json"))
    m = json.loads((tmp / "metrics.json").read_text(encoding="utf-8"))
    c.check(set(m["languages"]) == {"python", "javascript", "c", "go"}, f"four languages ({sorted(m['languages'])})")
    longest = {f["name"] for f in m["longest"] if f["lines"] > 60}
    c.check({"long_function", "longOne"} <= longest, f"long functions found in Python and JS ({sorted(longest)})")
    c.check(m["smells"].get("except_pass") == 1, "the Python except/pass is counted")
    c.check(m["smells"].get("empty_catch") == 3, f"empty catch in JS, C++ and Go (got {m['smells'].get('empty_catch')})")
    c.check(any("scoring.py" in p and p.endswith(" forward") for p in m["pass_through"]), "forward() is a pass-through")
    c.check(any(v["where"].startswith("src/core/scoring.py:") and v["import"] == "forbidden_ui"
                for v in m["forbidden_imports"]), "the forbidden import is reported with its line")
    cyc = m["import_cycles"]
    c.check(["src.core.helpers", "src.core.scoring"] in cyc, "Python module-level cycle found")
    c.check(["src/web/app.js", "src/web/util.js"] in cyc, "JS import cycle found")
    untested = {u["file"] for u in m["untested_risky"]}
    c.check("src/core/helpers.py" in untested and "src/core/scoring.py" not in untested,
            "helpers.py is untested, scoring.py is tested")
    cpp = [f for f in m["longest"] + m["most_complex"] if f["name"] == "moving_average"]
    c.check(bool(cpp) and cpp[0].get("approximate") and cpp[0]["cc"] >= 3, "C++ function found with branch complexity")


def test_clones(c: Checks, tmp: Path) -> None:
    """Check that every planted renamed clone is found and nothing periodic is.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("clones\n")
    run("clones.py", "--out", str(tmp / "clones.json"))
    groups = json.loads((tmp / "clones.json").read_text(encoding="utf-8"))["groups"]
    for a, b in (("src/core/scoring.py", "src/core/helpers.py"), ("src/web/app.js", "src/web/util.js"),
                 ("src/native/stats.cpp", "src/native/smooth.cpp"), ("src/svc/server.go", "src/svc/handler.go")):
        c.check(sites_cover(groups, a, b), f"clone {Path(a).name} ~ {Path(b).name}")
    c.check(all(g["kind"] == "renamed" for g in groups), "the copies are reported as renamed")
    c.check(not any(g["copies"] > 2 for g in groups), "the long repetitive functions are not reported as clones")


def git(repo: Path, *args: str) -> None:
    """Run git in the throwaway repo.
    Args:
        repo: The repo folder.
        *args: git arguments.
    """
    subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], check=True, capture_output=True)


def touch(repo: Path, *rels: str) -> None:
    """Append a comment line to files so a commit changes them.
    Args:
        repo: The repo folder.
        *rels: Files to change.
    """
    for rel in rels:
        path = repo / rel
        mark = "#" if rel.endswith(".py") else "//"
        path.write_text(path.read_text(encoding="utf-8") + f"{mark} edit\n", encoding="utf-8")


def test_visits(c: Checks, tmp: Path) -> None:
    """Check the visit types: fresh and second-opinion known lists, follow-up re-checks, routine ordering, budgets.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("visits\n")
    if shutil.which("git") is None:
        c.check(False, "git is on PATH")
        return
    repo = tmp / "visit_repo"
    shutil.copytree(FIXTURES, repo)
    cfg = repo / "config.json"
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    touch(repo, "src/core/helpers.py")
    git(repo, "commit", "-q", "-am", "treatment")

    def plan(name: str, *args: str) -> tuple[dict, str]:
        """Plan one visit in the throwaway repo.
        Args:
            name: The run folder name.
            *args: partition.py arguments.
        Returns:
            The plan and partition's output.
        """
        out = run("partition.py", *args, "--out", str(repo / "out" / name / "plan.json"), root=repo, config=cfg)
        return json.loads((repo / "out" / name / "plan.json").read_text(encoding="utf-8")), out

    fresh, _ = plan("v1", "--cap", "160", "--fresh")
    c.check(fresh["workflow_args"]["knownPath"].endswith("v1/known_empty.tsv")
            and (repo / "out" / "v1" / "known_empty.tsv").read_bytes() == b"file\tline\tstatus\tkind\tid\ttitle\n",
            "--fresh points agents at an empty known list in the run folder")
    second, _ = plan("v2", "--visit", "second-opinion", "--cap", "200")
    c.check(second["fresh"] and second["cap"] == 200 and second["visit"] == "second-opinion",
            "a second opinion is fresh (an explicit --cap wins over the default split change)")
    split, _ = plan("v2b", "--visit", "second-opinion")
    c.check(split["cap"] == round(12000 * 0.7), f"a second opinion splits the code at 70% of the tier's cap ({split['cap']})")
    chart = {"conditions": [
        {"id": "C0001", "kind": "defect", "status": "treated", "title": "Stale cache", "file": "src/core/helpers.py",
         "line": 3, "severity": "high", "details": {"evidence": "e", "failure_scenario": "s"},
         "treated": {"commit": "fix", "base": base}, "sightings": [], "notes": []},
        {"id": "C0002", "kind": "defect", "status": "open", "title": "Other", "file": "src/a.py", "line": 1,
         "severity": "medium", "sightings": [], "notes": []}],
        "visits": [{"run": "v0", "kind": "baseline", "head": base}]}
    (repo / "out" / "chart.json").write_text(json.dumps(chart), encoding="utf-8")
    follow, out = plan("v3", "--visit", "follow-up", "--cap", "160")
    fa = follow["workflow_args"]
    c.check([r["condition"] for r in fa["recheck"]] == ["C0001"] and fa["base"] == base
            and "regression" in fa["hunters"], "a follow-up re-checks the treated conditions against the treatment's base")
    c.check(follow["agents"]["total"] <= 6 and fa["readerModel"] == "sonnet" and not fa["lenses"]
            and all(f == "src/core/helpers.py" for u in fa["units"] for f in u["files"]),
            f"a follow-up fits its budget and reads only changed files ({follow['agents']['total']} agents)")
    routine, out = plan("v4", "--visit", "routine", "--cap", "160")
    ra = routine["workflow_args"]
    c.check(routine["agents"]["total"] <= 10 and ra["hunters"] == ["failure"] and not ra["cartographer"],
            f"a routine visit fits its budget of 10 ({routine['agents']['total']} agents)")
    c.check(routine["units"] and routine["units"][0]["changed"] and "(changed)" in out,
            "a routine visit reads the changed code first")
    tight, out = plan("v5", "--visit", "routine", "--cap", "160", "--budget", "3")
    c.check(tight["agents"]["total"] <= 3 and tight["uncovered"], "a smaller budget reads fewer units and says what it left")
    big, out = plan("v6", "--cap", "60", "--budget", "5")
    c.check(big["over_budget"] and "OVER BUDGET" in out, "a baseline over its budget is flagged, not trimmed")


def test_history(c: Checks, tmp: Path) -> None:
    """Check coupling, hidden coupling and fix recurrence on a throwaway git history.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("history\n")
    if shutil.which("git") is None:
        c.check(False, "git is on PATH")
        return
    repo = tmp / "repo"
    shutil.copytree(FIXTURES, repo)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    for i in range(5):
        touch(repo, "src/core/scoring.py", "src/core/helpers.py")
        git(repo, "commit", "-q", "-am", f"fix weighting edge case {i}")
    for i in range(4):
        touch(repo, "src/web/app.js", "src/native/stats.cpp")
        git(repo, "commit", "-q", "-am", f"tweak output {i}")
    run("history.py", "--origin", "def combine", "--out", str(tmp / "history.json"), root=repo, config=repo / "config.json")
    h = json.loads((tmp / "history.json").read_text(encoding="utf-8"))
    pairs = {(p["a"], p["b"]): p for p in h["coupling"]}
    core = pairs.get(("src/core/helpers.py", "src/core/scoring.py"))
    c.check(bool(core) and core["imports"] and core["shared"] == 6, "scoring/helpers change together and import each other")
    hidden = pairs.get(("src/native/stats.cpp", "src/web/app.js"))
    c.check(bool(hidden) and not hidden["imports"], "app.js/stats.cpp is hidden coupling")
    fixes = {f["file"]: f["fixes"] for f in h["fixes"]}
    c.check(fixes.get("src/core/scoring.py") == 5, f"five fix commits on scoring.py (got {fixes.get('src/core/scoring.py')})")
    c.check(h["origins"] and h["origins"][0]["found"] and h["origins"][0]["subject"] == "initial",
            "origin of 'def combine' is the initial commit")


def test_save_run(c: Checks, tmp: Path) -> None:
    """Check review/findings output, the ledger and known.tsv on a stub result.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("save_run\n")
    root = tmp / "saveroot"
    (root / "out" / "2026-09-27").mkdir(parents=True)
    config = tmp / "save_config.json"
    config.write_text(json.dumps({"data_root": "out"}), encoding="utf-8")
    finding = {"kind": "defect", "file": "src/a.py", "line": 3, "title": "Stale cache", "lens": "readers"}
    result = {"head": "abc123", "runDate": "2026-09-27", "yield": [], "not_covered": {},
              "readers": [{"id": "u01", "read_in_full": ["src/a.py", "src\\b.py"]}],
              "findings": [dict(finding, id="read:u01#d1", status="confirmed"),
                           dict(finding, id="read:u01#d2", status="refuted", title="Not a bug"),
                           dict(finding, id="read:u01#d3", status="duplicate", title="Twin")]}
    (tmp / "result.json").write_text(json.dumps(result), encoding="utf-8")
    run("save_run.py", "--result", str(tmp / "result.json"), "--run-dir", "out/2026-09-27", root=root, config=config)
    saved = json.loads((root / "out" / "2026-09-27" / "findings.json").read_text(encoding="utf-8"))
    c.check(len(saved) == 3 and all(f["run"] == "2026-09-27" for f in saved), "findings.json holds every finding")
    ledger = json.loads((root / "out" / "ledger.json").read_text(encoding="utf-8"))
    c.check(ledger.get("src/b.py", {}).get("commit") == "abc123", "the ledger records files read in full at HEAD")
    known = (root / "out" / "known.tsv").read_text(encoding="utf-8").splitlines()
    c.check(len(known) == 3 and "\topen\t" in known[1] + known[2] and "\trefuted\t" in known[1] + known[2],
            "known.tsv lists the charted open and refuted conditions, not duplicates")
    run("chart.py", "set", "C0001", "treated", "--commit", "fix1", "--base", "abc123", root=root, config=config)
    known = (root / "out" / "known.tsv").read_text(encoding="utf-8").splitlines()
    c.check(len(known) == 2, "a treated condition leaves known.tsv, so a regression is reported again")


def charted(fid: str, title: str, file: str, line: int, status: str = "confirmed", severity: str = "medium") -> dict:
    """Build a saved finding for the chart test.
    Args:
        fid: Its id.
        title: Its title (also its evidence).
        file: Its file.
        line: Its line.
        status: Its status in the run.
        severity: Its severity.
    Returns:
        The finding.
    """
    return {"id": fid, "kind": "defect", "lens": "readers", "title": title, "file": file, "line": line,
            "evidence": title, "severity": severity, "status": status}


def test_chart(c: Checks, tmp: Path) -> None:
    """Check the patient chart across visits: matching, statuses, re-checks, vitals, merge and detach.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("chart\n")
    root = tmp / "chartroot"
    config = tmp / "chart_config.json"
    config.write_text(json.dumps({"data_root": "out"}), encoding="utf-8")
    visits = {
        "2026-01-01": [charted("r#d1", "Stale cache survives the document switch", "src/a.py", 10, severity="high"),
                       charted("r#d2", "Typo in the save log line", "src/b.py", 5, severity="low"),
                       charted("r#d3", "Timer is not stopped on close", "src/c.py", 7, status="refuted"),
                       charted("r#d4", "Twin of d1", "src/a.py", 11, status="duplicate")],
        "2026-02-01": [charted("s#d1", "Stale cache survives a switch of document", "src/a.py", 12, severity="high"),
                       charted("s#d2", "Export ignores the chosen folder", "src/d.py", 3)],
        "2026-03-01": [charted("t#d1", "Stale cache still survives the document switch", "src/a.py", 14, severity="high")],
    }
    for run_name, found in visits.items():
        (root / "out" / run_name).mkdir(parents=True)
        (root / "out" / run_name / "findings.json").write_text(json.dumps(found), encoding="utf-8")
    chart_file = root / "out" / "chart.json"

    def load() -> dict:
        """Read the chart.
        Returns:
            The chart document.
        """
        return json.loads(chart_file.read_text(encoding="utf-8"))

    out = run("chart.py", "add", "--run", "out/2026-01-01", "--visit", "baseline", root=root, config=config)
    ch = load()
    status = {x["title"]: x["status"] for x in ch["conditions"]}
    c.check(len(ch["conditions"]) == 3 and status["Typo in the save log line"] == "watch"
            and status["Timer is not stopped on close"] == "refuted",
            f"a run opens one condition per charted finding; a low bug goes on the watch list ({status})")
    v = ch["visits"][-1]
    c.check(v["new"] == 3 and v["new_serious"] == 1 and v["open_serious"] == 1 and "1 serious" in out,
            f"the visit's vitals count new and open serious conditions ({v})")
    run("chart.py", "add", "--run", "out/2026-01-01", root=root, config=config)
    c.check(len(load()["conditions"]) == 3 and len(load()["conditions"][0]["sightings"]) == 1
            and load()["visits"][-1]["new"] == 3, "re-adding a run updates it instead of charting it twice")
    run("chart.py", "add", "--run", "out/2026-02-01", "--visit", "routine", root=root, config=config)
    ch = load()
    stale = next(x for x in ch["conditions"] if x["id"] == "C0001")
    c.check(len(ch["conditions"]) == 4 and len(stale["sightings"]) == 2 and stale["first_seen"] == "2026-01-01",
            "a later visit's report of the same bug is a second sighting, not a new condition")
    run("chart.py", "set", "C0001", "treated", "--commit", "fix1", "--base", "base1", root=root, config=config)
    known = (root / "out" / "known.tsv").read_text(encoding="utf-8")
    c.check("C0001" not in known and "C0002" in known, "a treated condition leaves the known list; open ones stay")
    run("chart.py", "add", "--run", "out/2026-03-01", "--visit", "routine", root=root, config=config)
    ch = load()
    stale = next(x for x in ch["conditions"] if x["id"] == "C0001")
    c.check(stale["status"] == "reopened" and ch["visits"][-1]["reopened"] == 1,
            "a treated condition seen again is reopened and counted in the vitals")
    run("chart.py", "set", "C0004", "treated", "--commit", "fix2", "--base", "base2", root=root, config=config)
    follow = root / "out" / "2026-04-01"
    follow.mkdir()
    (follow / "findings.json").write_text("[]", encoding="utf-8")
    (follow / "review.json").write_text(json.dumps({"head": "h4", "meta": {"agentCount": 3}, "rechecks": [
        {"condition": "C0004", "verdict": "cured", "reason": "guard added"}]}), encoding="utf-8")
    run("chart.py", "add", "--run", "out/2026-04-01", "--visit", "follow-up", root=root, config=config)
    ch = load()
    v = ch["visits"][-1]
    c.check(next(x for x in ch["conditions"] if x["id"] == "C0004")["status"] == "cured" and v["cured"] == 1
            and v["kind"] == "follow-up" and v["agents"] == 3, f"a follow-up re-check marks a treated condition cured ({v})")
    run("chart.py", "merge", "C0001", "C0002", root=root, config=config)
    ch = load()
    c.check(len(ch["conditions"]) == 3 and len(next(x for x in ch["conditions"] if x["id"] == "C0001")["sightings"]) == 4,
            "merge folds one condition's sightings into another")
    out = run("chart.py", "detach", "C0001", "2026-01-01/r#d2", root=root, config=config)
    ch = load()
    c.check("new condition C0005" in out and len(ch["conditions"]) == 4, "detach splits a wrong match off")
    found = json.loads((root / "out" / "2026-01-01" / "findings.json").read_text(encoding="utf-8"))
    found[0]["severity"], found[1]["severity"] = "medium", "medium"
    (root / "out" / "2026-01-01" / "findings.json").write_text(json.dumps(found), encoding="utf-8")
    run("save_run.py", "--refresh-known", "--run-dir", "out/2026-01-01", root=root, config=config)
    ch = load()
    typo = next(x for x in ch["conditions"] if x["id"] == "C0005")
    stale = next(x for x in ch["conditions"] if x["id"] == "C0001")
    c.check(typo["severity"] == "medium" and typo["status"] == "open",
            f"a re-grade in the run that opened a condition moves it off the watch list ({typo['status']})")
    c.check(stale["severity"] == "high", "re-adding an older run does not overwrite a newer sighting's details")
    status_out = run("chart.py", "status", root=root, config=config)
    c.check("open_serious" in status_out and "2026-04-01" in status_out, "status prints the vitals trend")
    later = {"2026-05-01": [charted("u#d1", "Cache entry kept after closing the document", "src/a.py", 60, severity="high")],
             "2026-06-01": [charted("w#d1", "Chosen folder is ignored when exporting twice", "src/d.py", 40)]}
    for run_name, found in later.items():
        (root / "out" / run_name).mkdir(parents=True)
        (root / "out" / run_name / "findings.json").write_text(json.dumps(found), encoding="utf-8")
    run("chart.py", "add", "--run", "out/2026-05-01", "--visit", "routine", root=root, config=config)
    ch = load()
    weak = next(x for x in ch["conditions"] if x["first_seen"] == "2026-05-01")
    stale = next(x for x in ch["conditions"] if x["id"] == "C0001")
    c.check(weak.get("maybe", {}).get("id") == "C0001" and all(s["run"] != "2026-05-01" for s in stale["sightings"]),
            "a weak match opens a new condition linked to its candidate instead of joining it")
    c.check("maybe C0001" in run("chart.py", "status", root=root, config=config), "status lists the possible matches")
    run("chart.py", "distinct", weak["id"], root=root, config=config)
    c.check("maybe" not in next(x for x in load()["conditions"] if x["id"] == weak["id"]),
            "distinct records that a possible match is a different problem")
    run("chart.py", "add", "--run", "out/2026-06-01", "--visit", "routine", root=root, config=config)
    twin = next(x for x in load()["conditions"] if x["first_seen"] == "2026-06-01")
    c.check(twin.get("maybe", {}).get("id") == "C0004", f"a weak match to a cured condition is linked, not reopened ({twin})")
    run("chart.py", "merge", "C0004", twin["id"], root=root, config=config)
    cured = next(x for x in load()["conditions"] if x["id"] == "C0004")
    c.check(cured["status"] == "reopened", "merging a later report into a cured condition reopens it")


def test_intake(c: Checks, tmp: Path) -> None:
    """Check that intake drafts the config from the planted evidence, and that --check validates it.
    Args:
        c: The check collector.
        tmp: A scratch folder.
    """
    sys.stdout.write("intake\n")
    repo = tmp / "intake_repo"
    shutil.copytree(FIXTURES, repo)
    (repo / "config.json").unlink()
    agent_dir = repo / ".claude" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "spec-drift.md").write_text(
        "---\nname: spec-drift\ndescription: Reports drift between the design docs and the code.\n---\n", encoding="utf-8")
    out = tmp / "intake"
    run("intake.py", "--out", str(out), root=repo, config=repo / "none.json")
    cfg = json.loads((out / "config.json").read_text(encoding="utf-8"))
    ev = json.loads((out / "intake.json").read_text(encoding="utf-8"))
    c.check(cfg["src"] == ["src"] and cfg["tests"] == ["tests"], f"src and tests found ({cfg['src']}, {cfg['tests']})")
    c.check("drawEvent" in cfg["framework_names"] and "refresh" not in cfg["framework_names"],
            "the camelCase override is a framework callback; the unreferenced snake_case method is only possible")
    markers = cfg["risk_markers"].get("python", {})
    c.check("WorkerThread" in markers.get("async", "") and "changed_signal" in markers.get("events", ""),
            f"external worker and signal names become risk markers ({markers})")
    confined = {x["import"]: x["used_in"] for x in ev["layering"]["confined_externals"]}
    one_way = {(x["from"], x["import"]) for x in ev["layering"]["one_way_internal"]}
    c.check(confined.get("toolkit") == ["src/ui"], "the toolkit is confined to src/ui (layering candidate)")
    c.check(("src/core", "src.ui") in one_way, "src/core never imports src/ui (layering candidate)")
    c.check([v["file"] for v in ev["vendored"]] == ["src/core/model_zoo.py"], "the licensed third-party file is a vendored candidate")
    cmds = ev["commands"]
    c.check("npm run test" in cmds.get("test", []) and "npm run lint" in cmds.get("lint", [])
            and "make bench" in cmds.get("bench", []), f"commands from package.json and the Makefile ({cmds})")
    c.check("docs/BACKLOG.md" in cfg["known_docs"] and "docs/adr/" in cfg["decision_docs"], "backlog and ADR docs found")
    c.check(cfg["extras"] and cfg["extras"][0]["agentType"] == "spec-drift" and "docs/ARCHITECTURE.md" in cfg["extras"][0]["brief"],
            "the project's drift agent becomes an extra, aimed at the architecture doc")
    asked = [q["id"] for q in ev["questions"]]
    c.check(cfg["data_root"] == ".checkup" and {"run_limits", "layering", "exclude"} <= set(asked),
            f"default data_root and the questions to ask ({asked})")
    target = repo / ".claude" / "checkup"
    target.mkdir(parents=True)
    shutil.copy(out / "config.json", target / "config.json")
    report = run("intake.py", "--check", root=repo, config=target / "config.json")
    c.check("ERROR" not in report and "drift" in report, "--check passes a fresh config and reports drift (not ignored)")
    bad = dict(cfg, src=["src", "gone"])
    (target / "config.json").write_text(json.dumps(bad), encoding="utf-8")
    done = subprocess.run([sys.executable, str(HERE / "intake.py"), "--check", "--root", str(repo)],
                          capture_output=True, text=True, encoding="utf-8", check=False)
    c.check(done.returncode == 1 and "src path missing: gone" in done.stdout, "--check fails on a missing src path")


def test_workflow(c: Checks) -> None:
    """Run the workflow harness when node is available.
    Args:
        c: The check collector.
    """
    sys.stdout.write("workflow\n")
    if shutil.which(NODE) is None:
        sys.stdout.write("  skip  node is not on PATH\n")
        return
    done = subprocess.run([NODE, str(WORKFLOW_HARNESS)], capture_output=True, text=True, encoding="utf-8",
                          check=False)
    for line in done.stdout.strip().splitlines():
        sys.stdout.write(f"        {line}\n")
    c.check(done.returncode == 0, "workflow assembly on stub agents")


def main() -> int:
    """Run every check.
    Returns:
        0 when all pass, 1 otherwise.
    """
    c = Checks()
    with tempfile.TemporaryDirectory(prefix="checkup-selftest-") as t:
        tmp = Path(t)
        for test in (test_partition, test_visits, test_metrics, test_clones, test_history, test_save_run, test_chart,
                     test_intake, test_sites, test_compare):
            try:
                test(c, tmp)
            except (RuntimeError, OSError, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as exc:
                c.check(False, f"{test.__name__} raised {exc}")
    test_workflow(c)
    sys.stdout.write(f"\n{'PASS' if not c.failed else f'FAIL: {c.failed} checks'}\n")
    return 1 if c.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
