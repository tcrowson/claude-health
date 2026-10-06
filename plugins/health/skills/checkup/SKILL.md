---
name: checkup
description: Runs a codebase health checkup as a series of visits recorded on a patient chart. A baseline visit reads every line in bounded units with Opus readers and hunters, verifies every finding and traces problem areas to the decisions behind them; a follow-up visit confirms a treatment cured what it claimed; a routine visit re-reads changed and least-recently-read code within a small agent budget. Every condition keeps a stable id across visits, and every visit reports vitals that trend. Use when the user asks for a checkup, a code health check, a full or comprehensive codebase review or audit, a follow-up after fixes, a periodic review, or a review of a large branch before merging. For one diff use /code-review; to act on the findings use /treatment.
---

# Checkup

## Principles

Each one guards against a way whole-codebase reviews fail. Keep them when changing the skill.

- **The chart is the record, not the run.** Every finding is merged into one chart of conditions with stable ids,
  so nothing confirmed can drop out between visits, and treatment works from the chart. A run's own folder is
  evidence, not the list of open work.
- **Judge health by vitals, not by finding counts.** One visit finds a share of what is there, so raw counts stay
  flat or rise for several visits even as the code improves. Open serious conditions, new serious ones per visit,
  treated conditions that come back, and cost are the numbers that must trend down.
- **A treatment is not a cure until a follow-up says so.** Treatment marks conditions treated; only a follow-up
  visit's re-check (the failure can no longer happen, siblings included) marks them cured.
- **Every line is read once, in units a reader can finish.** Recall depends on depth per line, not on the number
  of reviewers. Units are capped by risk-weighted size, and skimmed files go to a follow-up reader.
- **Map state against events before hunting.** The costliest bugs sit between files: state that survives an event
  (a switch, a removal, a job finishing late, undo). A cartographer maps events against long-lived state and a
  hunter walks every cell that is not reset.
- **Compare old and new code after a change.** Moved code loses guards; a regression hunter runs whenever there is
  a base commit, and always on a follow-up visit.
- **Verify by trigger or repro, never by mechanism alone.** Critical and high defects get a repro attempt; the rest
  must name the call chain from a real entry point. A verifier grade two or more steps from the reader's is
  re-graded by the main loop.
- **Scripts before agents.** Counting, parsing, clone detection, history mining and chart bookkeeping are scripts.
- **The budget is the user's.** Every visit has an agent budget (config `budget`); the plan prints the count by
  model against it, and nothing launches without a yes on that count.

## Talking to the user

The person running this may never have seen a checkup. Every message answers three things in this order: **where we
are** (one line, "Stage 2 of 5 · Plan"), **what I need from you** (a question, or "nothing needed"), and only then
**detail**, kept short or left in a file behind a link. Templates for every message and for the report are in
[REFERENCE.md](REFERENCE.md) under Messages and Report.

The user sees five stages; the numbered Steps below are the machinery behind them:
1. **Look around:** the chart's vitals and scripts that measure the code (no agents).
2. **Plan:** which visit, what will be reviewed, what it costs against the budget. Needs one yes.
3. **Review:** reviewer agents, or I, read the code; on a follow-up, treated conditions are re-checked.
4. **Double-check:** a second look confirms or rejects every finding.
5. **Report:** vitals first, then the results, then a short talk about the design questions.

- **Plain words.** Say "reviewer agents", "a second agent double-checks each finding", "a pass for bugs that appear
  when you switch, undo or close things", "the chart (the record of every problem found so far)". Never use tier,
  unit, lens, hunter, cartographer, verifier, inline, BRIEF, ledger or workflow args in a message.
- **Short.** A progress message is at most three lines. An approval fits on one screen.
- **No raw files in chat.** Summarize in a sentence and link the file.
- **Files, in layman's terms.** For each file you want to add or change, one line: what it is, where it goes, why
  it helps, and that it is safe to edit or delete.
- **Findings only when confirmed.** During stages 3 and 4 give counts, not lists.
- **Questions:** AskUserQuestion for choices; "Reply **yes** to go ahead, or tell me what to change" for one approval.
- **Time:** estimate only from a previous visit's recorded duration; otherwise say nothing about time.

## Files

- **Project** (checked in, created by intake): `.claude/checkup/config.json` and `seed.md`. Formats in
  [REFERENCE.md](REFERENCE.md).
- **Run folder** `<data_root>/<YYYY-MM-DD>/` (one per visit): plan.json, checkup.workflow.js (the copy this visit
  ran), metrics.json, clones.json, history.json, BRIEF.md, review.json, findings.json, trajectory.json,
  checkup_report.md.
- **Across visits** in `<data_root>/`: chart.json (the patient chart: conditions and the vitals of every visit),
  known.tsv (built from the chart: every condition except treated and cured ones, for agents to grep),
  ledger.json (what was read in full, at which commit).
- **Scripts** in `<skill dir>/scripts/`, standard library only (Python 3.10+, git optional): `intake.py`,
  `partition.py`, `metrics.py`, `clones.py`, `history.py`, `save_run.py`, `chart.py`, `compare.py`, `sites.py`
  (measurement only), `checkup.workflow.js`, and `languages.json`. After editing any of them, run
  `python <skill dir>/scripts/selftest.py`.

## Visits

| Visit | When | What runs | Default budget |
|---|---|---|---|
| baseline | first checkup, or when the user asks for a full exam | every unit read (tier below), cartographer, hunters, lenses, spec extras | 30 |
| second-opinion | offered after a baseline, on a yes | an independent full exam: different unit splits (70% of the cap), an empty known list; merged into the chart | 30 |
| follow-up | after a treatment (the chart holds treated conditions) | a re-check of every treated defect (cured or still present), the regression hunter over the treatment's changes, Sonnet readers on the changed files as the budget allows | 6 |
| routine | periodically | Sonnet readers on the code changed since the last visit first, then the code read longest ago, one failure-path hunter; as many units as the budget allows | 10 |

Baseline tiers (the unit cap and what runs):

| Tier | Cap (weighted lines) | Reader limit | Readers | Hunters | Lenses | ~Agents at 75k lines |
|---|---|---|---|---|---|---|
| lean | 12000 | 6 | Sonnet | regression with a base, else failure | none | ~10 |
| standard (default) | 12000 | 15 | Opus | lifecycle, failure (+regression with a base, +security for a service) | duplication, performance | ~22 |
| deep | 7000 | 25 | Opus | + security | duplication, performance | ~35 |

The size picks a baseline's mode: **inline** (2 units or fewer: the main loop reads the code; one Opus verifier),
**full**, or **rolling** (over the reader limit: the rest go to later visits through the ledger). Follow-up and
routine visits fit themselves to their budget and report what they left for the next visit.

## Steps

Commands run from the repo root, with `S=<skill dir>/scripts`.

0. **Intake, first run in a repo** (no `.claude/checkup/config.json`). The user's part is one round of questions and
   one approval; everything else is drafted from evidence.
   1. `python $S/intake.py --out <scratchpad>/intake` drafts `config.json` and `seed.md` and lists the open
      questions in `intake.json`. It writes nothing to the repo.
   2. Fill what evidence cannot give from CLAUDE.md, the README and the docs: the lint command, run rules, hot
      paths, established facts. Cut candidate lists to what is real, and drop any question the docs already answer.
   3. Ask what is left in **one** AskUserQuestion call: at most four questions, each with the evidence-backed answer
      first, marked (Recommended): run limits; layering rules (multi-select); profiles, only when the signals are
      mixed; exclusions, only when there are vendored candidates. Word every question for someone who has not read
      the code.
   4. Fold the setup into the stage-2 approval: describe the two files and any `.gitignore` line in plain words,
      never pasted, and link the drafts. On a yes, write both files to `.claude/checkup/`.
1. **Prep (main loop, no agents).** Run `python $S/intake.py --check`; list any drift in step 2, one line each.
   - **Pick the visit.** No chart: a baseline (when earlier run folders exist, `python $S/chart.py init` builds the
     chart from them first). Treated conditions on the chart: a follow-up. Otherwise a routine visit, unless the
     user asks for a baseline. `python $S/chart.py status` prints the vitals and the open serious conditions.
   - Run folder = `<data_root>/<today>/`, or `<today>-2` and so on. Start the test suite and linter in the
     background (config `commands`). Then:
     ```
     python $S/partition.py --visit <visit> [--tier standard] [--base <sha>] [--fresh] --out <run>/plan.json
     python $S/metrics.py --out <run>/metrics.json [--compare <previous run>/metrics.json]
     python $S/clones.py --out <run>/clones.json
     python $S/history.py --out <run>/history.json
     ```
     `--fresh` gives agents an empty known list (a second opinion always has one). partition.py copies
     `checkup.workflow.js` into the run folder and prints the agent count against the visit's budget.
   - Write `<run>/BRIEF.md` from the template: point at CLAUDE.md instead of copying it, take the rest from seed.md,
     the profiles and config, and keep agents' searches out of `<data_root>/`. Give each unit in plan.json's
     `workflow_args` a name and a one-line focus naming the state it holds and the events that touch it.
2. **Plan approval (stage 2).** One message from the Plan template: the visit and why, what will be reviewed and
   skipped, the agent count by model against the budget, files to add on a first run, drift. Get a yes on the
   count. A plan over budget is the user's call: ask (AskUserQuestion) whether to run it at the plan's count, save
   that count as the project's budget for this kind of visit (config `budget`, so later visits don't ask), or
   read fewer units (partition.py prints the least the visit needs; a follow-up's re-checks always run). A
   baseline over budget also offers a smaller tier.
3. **Run (stages 3 and 4).** Inline mode: read the units yourself against the reader checklist in
   `checkup.workflow.js` (`readerPrompt`), verify every defect (critical and high with one Opus agent), and
   evaluate the improvements yourself. Otherwise: `Workflow({scriptPath: "<run>/checkup.workflow.js", args:
   <workflow_args>})`. An agent that answers fewer items than it was given is retried once, automatically.
4. **Save.** `python $S/save_run.py --run-id <wf_...> --run-dir <run>` writes review.json and findings.json, merges
   the run into the chart (new conditions, second sightings, reopened ones, a follow-up's cured ones), records the
   visit's vitals, rebuilds known.tsv and updates the ledger. When it reports weak matches, check each with
   `chart.py status` and fix a wrong pairing with `chart.py merge` or `chart.py detach`. A killed run has its
   outputs salvaged; resume it with `resumeFromRunId` (a resume reuses an agent only when its prompt and options
   are unchanged and every agent before it was reused; files agents read are not part of that key). So a resume
   re-runs every agent after the first changed one: say how many will re-run before resuming.
5. **Spot-check and re-grade.** Check three or four verdicts yourself: defects confirmed only by Sonnet, the top
   accepted improvements, improvements a lens both proposed and accepted. Re-grade every critical and high, and
   every finding marked `regrade_check`, against the severity table in the BRIEF template; write corrections into
   the run's findings.json (`severity`, and a `regrade` reason) and run `save_run.py --refresh-known --run-dir <run>`
   so the chart follows.
6. **Trajectory (main loop, never delegated; baseline and second-opinion visits, or any visit with structure
   items).** Group the evidence into at most 5 problem areas: confirmed defects by root cause, `for-trajectory`
   items, history.json, metrics.json and clones.json. Write one card per area ([REFERENCE.md](REFERENCE.md)):
   symptoms (cited), requirement, diagnosis (`history.py --origin "<string>"`: when, what it optimized for,
   mistake / expired tradeoff / still right), at least two options including "keep and contain" (deletion test,
   two-adapter rule), a counterfactual test per bug, cost and path, strength, wins. Give every `for-trajectory` item
   a status, write `<run>/trajectory.json`, and run `save_run.py --refresh-known --run-dir <run>`.
7. **Report (stage 5), every visit.** Write `<run>/checkup_report.md` from the Report template: vitals and their
   trend first (`chart.py status`), then new serious conditions, reopened ones, cured ones, the other new
   conditions, the watch list's size, design questions, coverage. Conditions are named by their chart ids. Present
   it with the Done message. After a baseline, offer a second opinion in one line with its agent count. Then walk
   the design questions with the user; a card declined for a lasting reason may be recorded in the project's
   decision doc (config `decision_docs`) with the user's OK.
8. **Hand off.** `/treatment` works from the chart. After a treatment, the next visit is a follow-up.

## Measuring the skill

Two checkups of one commit report mostly different minor bugs, because each finds only a share of what is there;
their overlap estimates how much there is and how much one run finds. To measure a change to this skill, run a
**blind replica**: a new data root with the same plan.json and BRIEF.md and a header-only known.tsv. Then
`compare.py pair` groups both runs' findings into issues; check the pairing by hand; and `compare.py score` reports
overlap, the estimated total and each run's estimated recall, per-lens recurrence, and severity and verdict
agreement. Details in [REFERENCE.md](REFERENCE.md) under Comparing runs. `sites.py` gives units a worklist of risk
sites for site-by-site comparisons; normal visits leave it off.

## Rules

- No silent caps: whatever the budget leaves unread is reported as not covered, and the ledger carries it forward.
- Never exceed a visit's budget without an explicit yes on the new count. A request for more effort or
  thoroughness is not that yes: the tier, and any side agents, change only with a yes on their stated count.
- Agents never write repo files; run outputs go only to the run folder, and the chart only through the scripts.
  New repo files (setup) only with the user's OK.
- Agents never read other runs' folders or the chart: BRIEF.md names the only data files they may open.
- Respect exclusive resources (config `exclusive_resources`): no two agents use one at once, and none while the
  suite runs.
- Every finding is verified or evaluated; a reviewer's grade is a claim, and only verified findings are reported.
- No agent does what a script can. Design-pattern suggestions pass the two-adapter rule.
