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
    c.check(len(known) == 3 and "confirmed" in known[1] + known[2] and "refuted" in known[1] + known[2],
            "known.tsv lists open and refuted items, not duplicates")
    saved[0]["status"] = "fixed"
    (root / "out" / "2026-09-27" / "findings.json").write_text(json.dumps(saved), encoding="utf-8")
    run("save_run.py", "--refresh-known", root=root, config=config)
    known = (root / "out" / "known.tsv").read_text(encoding="utf-8").splitlines()
    c.check(len(known) == 2, "a fixed item leaves known.tsv, so a regression is reported again")


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
        for test in (test_partition, test_metrics, test_clones, test_history, test_save_run, test_intake):
            try:
                test(c, tmp)
            except (RuntimeError, OSError, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as exc:
                c.check(False, f"{test.__name__} raised {exc}")
    test_workflow(c)
    sys.stdout.write(f"\n{'PASS' if not c.failed else f'FAIL: {c.failed} checks'}\n")
    return 1 if c.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
