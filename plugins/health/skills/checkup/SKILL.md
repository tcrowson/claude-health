---
name: checkup
description: Runs a whole-codebase health checkup in any language and at any size. Scripts measure, partition and mine the git history; readers read every line of bounded units; Opus hunters trace lifecycle, failure-path, regression and security bugs; lens agents weigh duplication and performance opportunities; every finding is verified or evaluated; the main loop traces problem areas back to the decisions behind them. Use when the user asks for a checkup, a code health check, a full or comprehensive codebase review or audit, a review of a large branch before merging, or an uber-review. For one diff use /code-review; to act on the findings use /treatment.
---

# Checkup

## Principles

Each one guards against a way whole-codebase reviews fail. Keep them when changing the skill.

- **Every line is read once, in units a reader can finish.** Recall depends on depth per line, not on the number of reviewers: a reader with too much code skims. Units are capped by risk-weighted size, and skimmed files go to a follow-up reader.
- **Map state against events before hunting.** The costliest bugs sit between files: state that survives an event (a switch, a removal whose id is reused, a job finishing late, undo). No single file shows them, so a cartographer maps events against long-lived state and a hunter walks every cell that is not reset.
- **Compare old and new code after a refactor.** Moved code loses guards and changes order without anyone noticing. A regression hunter runs whenever there is a base commit.
- **Verify by trigger or repro, never by mechanism alone.** Asked only whether a mechanism exists, a verifier confirms nearly everything. Critical and high defects get a repro attempt; the rest must name the call chain from a real entry point.
- **Scripts before agents.** Counting, parsing, clone detection and history mining are faster, exact and repeatable as scripts, and they trend between runs; an agent doing them guesses.
- **Nothing is sampled.** Improvements carry their own payoff rating rather than a low slot on a bug scale, and every finding is verified or evaluated.
- **The budget is the user's.** The plan prints the agent count by model, and nothing launches without a yes.

## Talking to the user

The person running this may never have seen a checkup. Every message answers three things in this order: **where we are** (one line, "Stage 2 of 5 · Plan"), **what I need from you** (a question, or "nothing needed"), and only then **detail**, kept short or left in a file behind a link. Templates for every message and for the report are in [REFERENCE.md](REFERENCE.md) under Messages and Report.

The user sees five stages; the numbered Steps below are the machinery behind them:
1. **Look around:** scripts measure the code (no agents).
2. **Plan:** what will be reviewed, what it costs, any files to add. Needs one yes (on a first run, after a few quick questions).
3. **Review:** reviewer agents, or I, read the code.
4. **Double-check:** a second look confirms or rejects every finding.
5. **Report:** the results, then a short talk about the design questions.

- **Plain words.** Say "reviewer agents", "a second agent double-checks each finding", "a pass for bugs that appear when you switch, undo or close things". Never use tier, unit, lens, hunter, cartographer, verifier, inline, BRIEF, ledger or workflow args in a message; a term the user must see gets a few-word explanation the first time.
- **Short.** A progress message is at most three lines. An approval fits on one screen. When nothing is needed, say so.
- **No raw files in chat.** Never paste JSON, regexes or file contents. Summarize in a sentence and link the file.
- **Files, in layman's terms.** For each file you want to add or change, one line: what it is, where it goes, why it helps, and that it is safe to edit or delete.
- **Findings only when confirmed.** During stages 3 and 4 give counts, not lists ("14 possible issues found; checking each one"). Findings appear in the report after the double-check; anything still unconfirmed is labeled so.
- **Questions:** use AskUserQuestion for choices, and "Reply **yes** to go ahead, or tell me what to change" for a single approval.
- **Time:** give an estimate only from a previous run's recorded duration; otherwise say nothing about time.

## Files

- **Project** (checked in, created by intake): `.claude/checkup/config.json` and `seed.md`. Formats in [REFERENCE.md](REFERENCE.md).
- **Run folder** `<data_root>/<YYYY-MM-DD>/`: plan.json, checkup.workflow.js (the copy this run ran), metrics.json, clones.json, history.json, BRIEF.md, review.json, findings.json, trajectory.json, checkup_report.md.
- **Across runs** in `<data_root>/`: ledger.json (what was read in full, at which commit), known.tsv (items agents must not re-report; partition.py creates it, header only, before the first run).
- **Scripts** in `<skill dir>/scripts/`, standard library only (Python 3.10+, git optional): `intake.py`, `partition.py`, `metrics.py`, `clones.py`, `history.py`, `save_run.py`, `compare.py`, `checkup.workflow.js`, and `languages.json` (the per-language table). After editing any of them, run `python <skill dir>/scripts/selftest.py`.

## Tiers and modes

| Tier | Cap (weighted lines) | Reader limit | Hunters | Lenses | ~Agents at 75k lines |
|---|---|---|---|---|---|
| lean | 12000 | 6 | regression with a base, else failure | none | ~10 |
| standard (default) | 12000 | 15 | lifecycle, failure (+regression with a base, +security for a service) | duplication, performance | ~22 |
| deep | 7000 | 25 | + security | duplication, performance | ~35 |

The size picks the mode: **inline** (2 units or fewer: the main loop reads the code; one Opus verifier), **full** (every unit read), or **rolling** (over the reader limit: the units ranked highest by risk, churn and staleness are read; the rest go to later runs through the ledger and are reported as not covered). The cartographer and lifecycle hunter are skipped when there is little stateful code. **Delta run:** `--since <previous run's head>` limits readers to changed files and their importers; the cartographer and hunters still cover the whole app.

## Steps

Commands run from the repo root, with `S=<skill dir>/scripts`.

0. **Intake, first run in a repo** (no `.claude/checkup/config.json`). The user's part is one round of questions and one approval; everything else is drafted from evidence.
   1. `python $S/intake.py --out <scratchpad>/intake` drafts `config.json` and `seed.md` and lists the open questions in `intake.json`. It writes nothing to the repo.
   2. Fill what evidence cannot give from CLAUDE.md, the README and the docs: the lint command for the configured tools intake lists, run rules, hot paths, established facts. Cut candidate lists to what is real, and drop any question the docs already answer.
   3. Ask what is left in **one** AskUserQuestion call: at most four questions, each with the evidence-backed answer first, marked (Recommended), in this order:
      - **Run limits:** what agents must never run or touch (the app, the full suite, an exclusive resource such as a GPU, the network). Skip it when CLAUDE.md says.
      - **Layering rules** (multi-select, the top four candidates, worded as rules): which of today's one-way dependencies must stay that way.
      - **Profiles** (multi-select): only when the signals are mixed. Offer the detected ones first; the others stay reachable through Other.
      - **Exclusions** (multi-select): only when there are vendored candidates.
      Lifecycle events, write paths, framework names, risk markers, docs and commands are drafted, never asked.
      Word every question for someone who has not read the code ("Should the core stay free of UI code? It is today.").
   4. Fold the setup into the stage-2 approval (step 2): describe the two files and any `.gitignore` line in plain words with the template, never pasted, and link the drafts for anyone who wants to look. On a yes, write both files to `.claude/checkup/`.
1. **Prep (main loop, no agents).** Run `python $S/intake.py --check` first. Fix any error; list any drift (a new source folder, an unlisted framework callback, a new doc) in step 2, one line each, and apply what the user accepts. Run folder = `<data_root>/<today>/`, or `<today>-2`, `-3` and so on when that folder already exists. Start the project's test suite and linter in the background (config `commands`). Then:
   ```
   python $S/partition.py --tier standard [--base <sha>] [--since <sha>] --out <run>/plan.json
   python $S/metrics.py --out <run>/metrics.json [--compare <previous run>/metrics.json]
   python $S/clones.py --out <run>/clones.json
   python $S/history.py --out <run>/history.json
   ```
   partition.py also copies `checkup.workflow.js` into the run folder and creates the known-items file when it is missing. Write `<run>/BRIEF.md` from the template: point at CLAUDE.md instead of copying it (agents receive it), and take the rest from seed.md, the profiles and config. In plan.json's `workflow_args`, give each unit a name and a one-line focus naming the state it holds and the events that touch it (the marker summary is a starting point).
2. **Plan approval (stage 2).** One message from the Plan template: what will be reviewed and skipped, how (agents by model, from partition's output), files to add on a first run, and any drift from `--check` as plain one-liners. Get a yes.
3. **Run (stages 3 and 4).** Post the stage 3 progress line. Inline mode: read the units yourself against the reader checklist in `checkup.workflow.js` (`readerPrompt`), and verify every defect: critical and high with one Opus agent (repro or trigger), the rest yourself by repro or call chain; evaluate the improvements yourself. Otherwise: `Workflow({scriptPath: "<run>/checkup.workflow.js", args: <workflow_args>})`, the copy named by plan.json's `workflow_script` (the Workflow tool runs only scripts inside the working directory). Post the stage 4 line, with counts only, when checking starts.
4. **Save.** `python $S/save_run.py --run-id <wf_...> --run-dir <run>` writes review.json and findings.json, updates the ledger and known.tsv, and prints counts, yield by lens and cost. A killed run has its agent outputs salvaged; resume it with `resumeFromRunId`. A resume reuses an agent's result only when its prompt and options are unchanged and every agent launched before it was reused, so changing one agent re-runs it and every later one; the files agents read (BRIEF.md) are not part of that key, so a resume after editing them keeps results made under the old version. The record counts only the last pass: add the earlier passes' cost from their completion notices.
5. **Spot-check** three or four verdicts yourself: defects confirmed only by Sonnet, the top accepted improvements (open the clone sites; check a performance claim's input size), and improvements a lens agent both proposed and accepted. Then re-grade every critical and high yourself: its trigger frequency and consequence against the table in the BRIEF template ([REFERENCE.md](REFERENCE.md)), correcting findings.json where you disagree.
6. **Trajectory (main loop, never delegated).** Group the evidence into at most 5 problem areas: confirmed defects by root cause, `for-trajectory` items, history.json (hotspots, hidden coupling, footprint, fix recurrence), metrics.json (cycles, forbidden imports, pass-throughs, fan-in, untested risky files) and clones.json. Write one card per area ([REFERENCE.md](REFERENCE.md) has the schema):
   - **Symptoms**, cited. **Requirement**: what must be true, stated without the current code.
   - **Diagnosis**: the decision, when it was made (`history.py --origin "<string>"`), what it optimized for, and a verdict: mistake, expired tradeoff, or still right.
   - **Options**: at least two genuinely different designs, one of them "keep and contain". Test each with the deletion test (does it gather complexity, or only move it?) and the two-adapter rule (a seam needs two real implementations).
   - **Counterfactual test**: for each confirmed bug in the area, would it be impossible or unlikely under the proposal?
   - **Cost and path**: files and call sites touched, an incremental migration, the tests needed first, what it retires. **Strength**: strong, worth exploring or speculative. **Wins** in terms of locality and leverage.
   Give every `for-trajectory` item a status (accepted into a card, or rejected with a reason), write `<run>/trajectory.json`, and run `save_run.py --refresh-known`.
7. **Report (stage 5), every run.** Write `<run>/checkup_report.md` from the Report template on every run: full, inline, rolling or delta, a killed run (from what it salvaged, saying what did not finish) and a replica or experiment. A run is not finished until its report exists and the user has seen it. Present it with the Done message: the verdict, the fix-first titles and the link. If the session can publish pages, offer in one line to publish the report as a page. Then walk the trajectory cards with the user, one at a time. When a card is declined for a lasting reason, offer to record it in the project's decision doc (config `decision_docs`) so later runs do not propose it again; that edit needs the user's OK.
8. **Hand off.** `/treatment` works from findings.json and trajectory.json, and names items by the report's IDs.

## Measuring the skill

Two checkups of one commit should find mostly the same bugs; the gap between them is what a single run misses. To measure a change to this skill, run a **blind replica**: a new data root with the same plan.json and BRIEF.md and a header-only known.tsv, so no agent sees the other run's findings. Then `compare.py pair` groups both runs' findings into issues; check the pairing by hand (merge or split issues in issues.json, especially those marked for review); and `compare.py score` reports overlap, the estimated total and each run's estimated recall, how often each lens's findings recur, and severity and verdict agreement. An independent check of a sample (a fresh Opus agent verifying findings blind to which run made them) adds `truth` to issues, and an issues.json with truth values is a reference set that later versions are scored against with `pair --reference`. Details in [REFERENCE.md](REFERENCE.md) under Comparing runs.

## Rules

- No silent caps: whatever the budget leaves unread is reported as not covered.
- Agents never write repo files; run outputs go only to the run folder. New repo files (setup) only with the user's OK.
- Agents never read other runs' folders: BRIEF.md names the only data files they may open.
- Respect exclusive resources (config `exclusive_resources`): no two agents use one at once, and none while the suite runs.
- Every finding is verified or evaluated; a reviewer's grade is a claim, and only verified findings are reported as fact.
- No agent does what a script can. Design-pattern suggestions pass the two-adapter rule.
