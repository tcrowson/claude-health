# Checkup reference

## config.json (`.claude/checkup/config.json`)

Read by the scripts (src, exclude, tests, data_root, extensions, framework names, forbidden imports,
history_months, extras, budget) and by the main loop (the rest). Every key is optional.

```json
{
  "src": ["app"],
  "exclude": ["app/vendored/model.py"],
  "tests": ["tests"],
  "data_root": "WORK/checkup",
  "profiles": ["interactive"],
  "extensions": {".inl": "c"},
  "framework_names": ["on_custom_callback", "paintEvent"],
  "risk_markers": {"python": {"async": "\\bJobQueue\\b", "events": "\\.on_changed\\("}},
  "forbidden_imports": [{"from": "src/core", "import": "src.ui"}],
  "commands": {"test": "...", "test_quick": "...", "lint": "...", "bench": "..."},
  "exclusive_resources": ["one GPU: never two GPU jobs at once"],
  "known_docs": ["docs/BACKLOG.md"],
  "decision_docs": ["docs/DECISIONS.md"],
  "extras": [{"id": "spec", "name": "Spec drift", "brief": "Report drift between docs/ARCHITECTURE.md and the code.", "agentType": "optional-custom-agent"}],
  "history_months": 12,
  "budget": {"baseline": 30, "second-opinion": 30, "follow-up": 6, "routine": 10}
}
```

- `budget`: the most agents each visit may start. Follow-up and routine visits fit their readers to it. When a
  plan is still over (a follow-up's re-checks alone can be), partition.py prints the least the visit needs and
  the user chooses: run it at the plan's count, save that count here, or read fewer units (a baseline: a smaller
  tier).

- `framework_names`: names the project's frameworks call by themselves (overridden event handlers, registered
  callbacks), so they are never reported as dead code. The language table itself lists only `main`.
- `risk_markers`: per language, extra regexes by category (events, async, state, ids, io, db, errors) for the
  project's own frameworks and libraries. They extend the table's language-level markers, which set unit sizes
  and pick the cartographer's files.
- `extras` run as Sonnet readers with their own brief (a spec-drift check, a test-coverage map of recent changes).

## Intake output (`intake.py --out <folder>`)

Drafts `config.json` and `seed.md` in the folder, and records the evidence in `intake.json`:

- `layout`: `src`, `tests`, other code folders (tooling, scripts, dot folders) and lines per folder.
- `commands`: the interpreter; test, lint and bench candidates from package scripts, Makefile targets, the
  language toolchain and runner or bench scripts; and `tools`, the tools configured in the repo, by file or section.
- `framework_callbacks`: public methods on classes with an external base that nothing references. `likely` when
  the name's style differs from the project's (a camelCase override in snake_case code); only likely ones enter the draft.
- `risk_markers`: external names whose words say thread, signal, session, cache and the like, used at least three times.
- `layering`: `confined_externals` (a package used by only a few internal packages) and `one_way_internal`
  (packages imported one way only). Candidates for `forbidden_imports`; never drafted without the user.
- `vendored`: files with a license or copyright header unlike the project's own. Candidates for `exclude`.
- `profiles` (signals, suggestion, whether it is clear), `data_root` (path, ignored by git), `docs`, and
  `lifecycle_candidates` / `write_candidates` for seed.md.
- `questions`: what to ask the user (run limits, layering, profiles when unclear, exclusions when any).

`intake.py --check` validates an existing config (paths exist, regexes compile, `data_root` ignored) and reports
drift: new source folders, likely framework callbacks not listed, docs with backlog or decision content not
listed. Doc entries may use globs (`SPEC/*.md (Decisions sections)`).

## seed.md (`.claude/checkup/seed.md`)

What an agent needs that CLAUDE.md does not already say. Keep it short.

```md
# Checkup seed: <project>
## Profiles
<from PROFILES.md, plus what is specific here>
## Run rules
<how an agent runs a snippet (interpreter, env vars for headless / CPU-only), how it builds temp data,
which single test it may run, and what it must never run>
## Lifecycle events
<every way the "current" thing changes or ends, with the functions that do it>
## Write paths
<every operation that writes user data, caches or external state>
## Hot paths and measurement
<where time is spent, input sizes that matter, the bench / profile commands>
## Established facts and decisions (not findings)
<tuned values and deliberate choices a reviewer would otherwise flag, when CLAUDE.md does not list them>
```

## BRIEF.md (`<run>/BRIEF.md`, written each run)

```md
# Checkup brief: <project>, <date>

## Scope
Repo <path>; branch <name> at <sha>; base <sha or none>. Source: <src>. Excluded: <exclude>. Visit <visit>; tier
<tier>, mode <mode>. Tests <n/n passing, or running>; lint <clean?>.

## Run rules (every agent)
- Read-only: create, edit or delete no repo file; no git command that changes state.
- Of the checkup's data folder <data_root>/, open only this brief, clones.json, metrics.json and the known-items
  file named below; never search it, open other runs' folders or open chart.json.
- Point every Grep and Glob at the source, test and docs folders, never at the repo root or <data_root>/, so the
  records of other visits never enter your results.
- <from seed.md: how to run snippets, temp data, the one test you may run>
- Never: <the full suite, the app, exclusive resources, network, ...>.

## Project rules
CLAUDE.md (already in your context) holds the invariants and coding rules. Also: <seed.md facts not in CLAUDE.md>.

## Profiles, lifecycle events, write paths, hot paths
<from seed.md and PROFILES.md>

## Known items (do not report)
- Before reporting on a file, grep <knownPath> for its path (columns: file, line, status, kind, id, title; it may
  hold only its header). Listed items are conditions already on the chart: open, on the watch list, deferred or
  judged not worth it. Report one only with new evidence. Treated conditions are not listed: a treated bug you
  find again is a finding.
- Everything in <known_docs>, and the decisions in <decision_docs>.
- An item is known only when a known.tsv line or a doc passage names it: the same file or symbol and the same
  failure. A doc that describes the class of problem or the area is not enough; report the specific defect.

## Defect severity
Grade two things, then read the severity from the table.
- Trigger: **common** (routine use hits it), **occasional** (a specific but plausible sequence of actions or
  inputs), **rare** (a narrow race window, unusual configuration or hardware, a disk or network failure).
- Consequence: **severe** (the user's work or data lost or corrupted; wrong output saved, exported or delivered;
  persistent records wrong), **moderate** (a crash or hang, wrong or stale state shown, a job failing, a stuck
  control, a leak that grows with use), **minor** (cosmetic, a misleading message or log, a missing log line, a
  bounded leak, a doc or comment error).

| | common | occasional | rare |
|---|---|---|---|
| severe | critical | high | medium |
| moderate | high | medium | low |
| minor | medium | low | low |

## Improvement ratings
impact: quantified (lines removed, sites unified, ms or MB saved, a bug class retired). effort, as the work of a
coding agent (it writes the code and tests; slow suites and reviews add wall-clock): S (a few edits and one test
run), M (a focused session across several files), L (several sessions, or a staged migration). risk: of the change
breaking something. strength: strong, worth_exploring, speculative.
```

## Workflow args

`partition.py` writes them to `plan.json` → `workflow_args`; edit unit names and focus lines, then pass the
object as `args`, with `plan.json` → `workflow_script` (the run folder's copy) as `scriptPath`. Keys: `dataDir`,
`visit`, `head`, `base`, `runDate`, `profile`, `cap`, `units` (`id`, `name`, `files`, `weights`, `focus`, optional
`model`; may be empty on a follow-up), `extras`, `cartographer`, `cartoFiles`, `hunters`, `lenses`, `followups`,
`knownPath`, `readerModel` (the tier's: opus, or sonnet in lean, follow-up and routine visits), `recheck` (a
follow-up's treated conditions: `condition`, `kind`, `title`, `file`, `line`, `severity`, `evidence`,
`failure_scenario`, `fix`, `commit`: defects only); optional `opusBatch` (10), `sonnetBatch` (20). The workflow cannot read
files: agents read BRIEF.md, clones.json and metrics.json from `dataDir` themselves. An agent that answers fewer
items than it was given (a verifier, an evaluator or a re-checker) is retried once on the missing ones in half-size
batches.

The result (review.json) also holds `rechecks`: one `{condition, verdict: cured | still_present | uncertain,
trigger, repro, reason}` per treated defect; `save_run.py` applies them to the chart.

## Finding schema (findings.json, shared with /treatment)

Common: `id`, `kind` (`defect` | `improvement`), `lens`, `source` (the agent), `title`, `file`, `line`,
`other_sites`, `evidence`, `status`, `verdict`, `also_reported_by`, `run`.

- **defect**: `category`, `severity` (after verification: looked up from the verifier's `trigger_frequency` and
  `consequence`), `reported_severity`, `regrade_check` (true when the two differ by two or more steps: the main
  loop re-grades it), `regrade` (the main loop's reason, when it corrected the grade), `trigger_frequency`,
  `consequence`, `failure_scenario`, `confidence`, `fix`. Status: `confirmed`, `refuted`, `uncertain`, `known`
  (only with the naming line quoted in the verdict's `known_where`), `duplicate`, `unverified`.
- **improvement**: `lens` (`duplication`, `simplification`, `performance`, `structure`, `tests`, `docs`),
  `impact`, `effort`, `risk`, `strength`, `measurement`, `change`, `evaluated_by`. Status: `accepted`,
  `rejected`, `merged`, `uncertain`, `known`, `for-trajectory` (structure items, for the main loop).
- A finding's status records what its run decided. What happens to the problem afterwards (treated, cured,
  deferred) is recorded on its chart condition, not in findings.json.

## Chart (`<data_root>/chart.json`, written only by `chart.py` and `save_run.py`)

```json
{
  "version": 1, "next_id": 43,
  "conditions": [{
    "id": "C0042", "kind": "defect | improvement | trajectory", "title": "...", "file": "src/a.py", "line": 10,
    "severity": "high", "severity_by": "manual (only after set --severity)",
    "status": "open", "status_by": "run | manual | recheck",
    "first_seen": "<run folder>", "last_seen": "<run folder>",
    "sightings": [{"run": "<run folder>", "id": "<finding id>", "status": "confirmed", "severity": "high",
                   "score": 1.9, "review": false}],
    "details": {"evidence": "...", "failure_scenario": "...", "fix": "..."},
    "treated": {"commit": "<sha>", "base": "<sha before the treatment>", "date": "..."},
    "notes": ["<run>: seen again after treatment: reopened"]
  }],
  "visits": [{"run": "<run folder>", "kind": "baseline", "head": "<sha>", "date": "...", "new": 12,
              "new_serious": 2, "reopened": 0, "cured": 0, "cured_total": 0, "open_serious": 5,
              "open_defects": 40, "watch": 30,
              "open_improvements": 60, "awaiting_follow_up": 0, "agents": 22, "tokens": 5000000,
              "minutes": 40, "metrics": {"functions_over_100": 25, "import_cycles": 0, "clone_groups": 40}}]
}
```

Statuses: `open` (treatment's work), `watch` (a low-severity defect: fixed only when cheap or when a nearby fix
touches the same code; not counted as open), `documented` (a project doc already names it), `uncertain`,
`proposed` (a design card or structure item awaiting the talk), `treated` (fixed in `treated.commit`, awaiting a
follow-up), `cured` (a follow-up's re-check says the failure can no longer happen), `reopened` (a treated
condition seen again, or a re-check found it still present: open again), `deferred`, `wontfix`, `refuted`,
`rejected`.

How statuses move: a run's findings open conditions (`confirmed` becomes `open`, or `watch` when low; `known`
becomes `documented`; `fixed`, a treatment recorded the older way with its `commit`, becomes `treated`, also when a
later run marks a sighting of an open condition fixed); a later sighting of a `treated` or `cured` condition
reopens it; a sighting confirming a `refuted` or `uncertain` one opens it; a decision (`deferred`, `wontfix`) and
a manual status stand until changed.

Matching uses compare.py's score, best first, a tie going to the older condition. A match below its sure score
opens a new condition linked to its candidate (`maybe`), and so does a finding that looks like another finding of
the same run (the run's de-duplication kept both, so the pair is the main loop's call); `status` lists the links,
settled with `merge` (the kept condition takes a live status over a refuted or uncertain one, and links to the
dropped one move to it) or `distinct`, and a wrong sure match is split off with `detach`.

Severity: the worst among a condition's sightings from the latest reviewed commit (the runs sharing the latest
run's head; older commits' grades are history, and a refuted sighting grades nothing). `set --severity` re-grades
by hand and stands against later sightings (`severity_by: manual`). A visit's `cured` counts its own re-checks;
`cured_total` is the chart's.

Commands: `chart.py add --run <run> [--visit <kind>]` (idempotent: re-adding a run refreshes its sightings),
`init` (every run folder, named `<date>` or `<date>-<n>`, oldest first; other folders with findings, such as a
combined analysis, are listed and skipped), `new --title --file [--line --severity --kind --evidence]` (a condition
found outside a checkup, during a treatment say; linked to a similar condition when there is one), `set <id>
<status> [--commit --base --note --severity]`, `merge <keep> <drop>`, `distinct <id>`, `detach <id> <run>/<finding
id>`, `status`, `known`; read-only: `show <id>` (one condition in full: sightings, details, notes, treatment,
possible match) and `list [--status ...] [--kind <kind>] [--severity ...] [--file <substring>]` (one line per
condition, oldest first; with no filter, the open and reopened defects).

## Trajectory card (trajectory.json: a list)

```json
{
  "id": "T1", "kind": "trajectory", "status": "proposed", "title": "Per-document state has no owner",
  "files": ["src/ui/window.py"], "symptoms": ["<finding id or evidence, cited>"],
  "requirement": "...", "decision": "...", "introduced": "<sha date subject>", "optimized_for": "...",
  "verdict": "mistake | expired tradeoff | still right",
  "options": [{"name": "...", "summary": "...", "deletion_test": "...", "seams": "..."}],
  "counterfactual": [{"finding": "<id>", "prevented": "yes | likely | no", "why": "..."}],
  "cost": "...", "path": ["step 1", "..."], "tests_first": ["..."], "retires": "...",
  "wins": ["locality: ...", "leverage: ..."], "strength": "strong | worth_exploring | speculative"
}
```

Status: `proposed`, then `accepted` or `declined` after the talk with the user (re-add the run so the chart
follows). /treatment records what it does on the card's chart condition.

## Messages

Fill the angle brackets; drop any line that does not apply. Plain words only (see "Talking to the user" in
SKILL.md). Each example is complete: nothing more goes in the message.

**Start (stage 1)**
```md
**Checkup of <project>**: <"a first full exam of the whole codebase" | "a follow-up: checking that the last
fixes worked" | "a routine visit: the code changed since last time, plus a slice not read recently">.
It runs in five stages. I'll need you twice: to approve the plan, and to talk through the results.

**Stage 1 of 5 · Look around:** <open serious problems on record: <n>> · measuring the code with scripts (no agents yet).
```

**Plan (stage 2)**: one message, one yes on the count. On a first run, the AskUserQuestion round comes just before it.
```md
**Stage 2 of 5 · Plan:** I need one yes before the review starts.

**Visit:** <baseline | second opinion | follow-up | routine> (<why, in a few words>).
**What I'll review:** <folders and files, in words>, about <N> lines. <On a follow-up: "and re-check the <n>
problems the last treatment fixed.">. **Skipping:** <item> (<why, in a few words>).
**How:** <"I'll read it myself; one extra agent double-checks the serious findings." |
"<n> reviewer agents read the code side by side, then others double-check every finding: <N> agents in all
(<s> Sonnet, <o> Opus), within this visit's budget of <B>." | "... <N> agents, over this visit's budget of <B>."
followed by the budget question (AskUserQuestion): "Run it at <N> agents", "Make <N> this project's budget for
<visit> visits (saved in `.claude/checkup/config.json`)", and, when fewer readers would do, "Read fewer parts of
the code (at least <M> agents)"; for a baseline, also "A <lean> exam (<M> agents)".>
**Files I'll add to your repo** (first checkup only; safe to edit or delete):
- `.claude/checkup/config.json`: which folders to review or skip, so the next checkup doesn't ask again.
- `.claude/checkup/seed.md`: a page of notes on how the app works (what "saving" or "switching" means here), for the reviewers.
- One line in `.gitignore` for `<data_root>/`: the checkup keeps its notes and reports there, and this keeps them out of git.
**Changed since last time:** <one plain line per drift item, or drop this line>

Reply **yes** to go ahead, or tell me what to change. (Drafts: <links>)
```

**Progress (stages 3 and 4)**: at most three lines, counts only.
```md
**Stage 3 of 5 · Review:** <n> reviewer agents are reading the code. Nothing needed from you.
```
```md
**Stage 4 of 5 · Double-check:** the review found <N> possible issues; each is now being confirmed or rejected.
```

**Done (stage 5)**: sent on every visit, once checkup_report.md is written; a few lines and a link.
```md
**Checkup finished:** <one-sentence verdict in plain words, led by the vitals: "3 serious problems open, down from 7">.
- <n> new serious problems: <C0042 plain title>; <C0043 plain title>
- <follow-up: "<n> of <m> fixes confirmed; <k> came back: <C-ids>">
- <n> other new problems · <n> on the watch list · <n> improvements · <n> design questions
Report: <link to checkup_report.md>

Next: <baseline: "a second opinion (another independent exam, about <N> agents) would find more; want one?" |
"I can walk you through the design questions, or you can run /treatment to start fixing." | follow-up: "the
<k> that came back are open again for /treatment.">
```

## Report (`<run>/checkup_report.md`)

Written on every visit, including killed runs (saying what did not finish) and replicas. Written for a person
first and an agent second: plain titles that describe what a user would see, one idea per line, tables for lists,
and the agent-only details at the end. Conditions are named by their chart ids (C0042), which stay the same in every
later report and are what the user and /treatment refer to.

```md
# Checkup: <project> · <date> · <visit>

<Two or three plain sentences: how healthy the code is, whether it is getting healthier, and the one thing to do first.>

## Vitals
| | This visit | Last visit | Trend |
|---|---|---|---|
| Open serious problems | <n> | <n> | <better / worse / same> |
| New serious problems found | <n> | <n> | |
| Fixes that came back | <n> | <n> | |
| Fixes confirmed | <n> | <n> | |
| Open problems (all) | <n> | <n> | |
| On the watch list (minor) | <n> | <n> | |
| Cost | <agents, tokens, minutes> | <...> | |
<First visit: "These numbers are the baseline.">

## Fix first
<New and reopened critical and high conditions, most serious first.>

### C0042. <What the user sees, e.g. "Mirror with nothing selected crashes">
- **When it happens:** <the steps that trigger it>
- **Why:** <the cause in one sentence> (`<file:line>`)
- **Fix:** <one sentence>
- **Confirmed by:** <reproducing it | tracing the call path> · severity <critical | high> · <new | seen before on <date> | came back after the fix in <sha>>

## Follow-up (follow-up visits)
| # | Problem | Fixed in | Result |
|---|---|---|---|
| C0031 | <plain title> | `<sha>` | <confirmed fixed | came back: <why> | unclear> |

## Other new problems
| # | What happens | Where | Severity |
|---|---|---|---|
| C0050 | <plain description> | `<file:line>` | medium |

<n> new minor problems went on the watch list (chart status `watch`).

## Improvements
| # | Change | Payoff | Effort | Risk |
|---|---|---|---|---|
| C0060 | <e.g. "Merge the two copies of the parser"> | <e.g. "40 fewer lines; one place to fix"> | <S, M or L> | <low, medium or high> |

## Design questions
### C0070. <Plain title>
<The problem in one sentence.> <The suggestion in one sentence.>
**Why it matters:** <what gets easier, or which problems stop happening> · **Confidence:** <strong | worth exploring | speculative>

## What was covered
- **Reviewed line by line:** <folders and files>
- **Checked by script only:** <items and why>
- **Not reviewed this visit:** <items and why; the next routine visit starts with them>
- **Likely missed:** one visit reports only a share of the real problems in what it reads; later visits find
  more, mostly minor ones, while serious ones recur more often. A file with no findings was read, not proven clean.

## Code measurements
<Three to five metrics with the change since the last visit.>

## For agents
Run folder `<path>`; chart `<data_root>/chart.json`. Conditions new this visit: <C-ids>; weak matches checked: <n>.
Cost: <agents by model>, <tokens>, <minutes>.
```

## Comparing runs (`compare.py`)

**Blind replica.** A second run of the same commit that must not see the first: a new data root (its own config
copy with `data_root` changed), a header-only known.tsv there before anything launches, the first run's plan.json
`workflow_args` with only `dataDir` and `knownPath` changed, and the same BRIEF.md with its paths changed. After
the run, search the agents' transcripts for the other data root's path; a hit means that agent saw the other run.
Redo a leaked replica as a fresh run, not a resume: a resume reuses every agent launched before the changed one,
so the two passes share those results and are not independent samples.

**issues.json**, written by `compare.py pair` and adjudicated by hand:
```json
{
  "runs": {"A": "<data_root>/2026-09-27", "B": "<replica root>/2026-09-27"},
  "issues": [{"id": "X001", "kind": "defect", "title": "...", "file": "...", "line": 10,
              "members": {"A": ["hunt:lifecycle#d1"], "B": ["read:u04#d3"]}, "scores": {"B": 1.9},
              "review": false, "truth": "real | not-real | uncertain (optional)"}]
}
```
- Pairing matches findings of the same kind that share a file, by title and evidence words; a match under 1.6 is
  marked `review`. Adjudicate by moving ids between issues: two findings of one run can share an issue.
- `truth` comes from an independent check, never from the runs being compared.
- `score` counts a defect as found when the run's verifier called it real (confirmed, or known with a real
  verdict), and reports per pair of runs: both / only, overlap, the Chapman estimate of the total and each run's
  estimated recall, per-lens recurrence ("readers 5/21": 5 of the 21 real defects readers found in A were found
  in B), severity agreement on shared defects, and defects real in one run but refuted or uncertain in the
  other. With `truth`, it adds each run's recall and precision against the reference.

**Site worklists (`sites.py`, measurement only).** `sites.py --plan <run>/plan.json` writes `sites` into each unit
of `workflow_args`: one site per function (or class body, or module-level code) holding risk markers, with its
kinds (`io`: file and database access; `async`: threads, workers, timers, plus signal lines in the same function;
`state`: long-lived containers and caches; `error`: exception handlers) and lines; import lines are skipped. A
unit with sites gets the worklist reader prompt, and its reader answers every site `ok`, `defect` or `unsure`;
`review.json` then holds each reader's coverage (`sites`: listed, answered, defect, unsure). Compare two reviews
site by site from their answers. One experiment (three units, two samples each) found worklist reviewers no more
repeatable than ordinary ones, at about 20% more tokens, so normal runs leave it off.
