# claude-health

Two skills for [Claude Code](https://claude.com/claude-code) that look after a codebase's health, the way a
doctor looks after a patient:

- `/checkup` examines your codebase for bugs and for ways to make the code simpler or faster, double-checks
  everything it finds, records it on a **chart** that carries every problem from visit to visit, and writes a
  plain-language report that leads with the code's **vitals**.
- `/treatment` fixes what is on the chart, one batch at a time, only with your approval, and adds a test for
  each fix. The next checkup confirms the fixes held.

## Install

In Claude Code:
```
/plugin marketplace add tcrowson/claude-health
/plugin install health@claude-health
```
The commands are then `/health:checkup` and `/health:treatment`.

Without the marketplace: download the zip from the [latest release](https://github.com/tcrowson/claude-health/releases/latest)
and unzip it into `~/.claude/skills/` (the `INSTALL.txt` inside explains). The commands are then `/checkup` and
`/treatment`.

You need Python 3.10 or newer and git. Nothing else is installed.

## Using it

### The cycle
1. **Baseline:** the first `/health:checkup` is a full exam of the whole codebase. Afterwards it offers a
   **second opinion** (another independent exam that splits the code differently), which finds more; it runs only
   if you say yes.
2. **Treatment:** `/health:treatment` fixes what the exam put on the chart, most serious first.
3. **Follow-up:** the next `/health:checkup` sees the treated problems and checks that each fix held (and that it
   covered the same problem elsewhere), plus the code the treatment changed. It is small: about 6 agents.
4. **Routine visits:** after that, run `/health:checkup` whenever you like (weekly, or after a big change). It reads
   the code changed since the last visit first, then the code read longest ago, within a budget of about 10
   agents, so the whole codebase gets re-read over a few visits.

Health shows in the vitals at the top of every report: open serious problems, new serious ones found, fixes that
came back, fixes confirmed, and the cost of the visit. Those are the numbers that should fall. The total count of
findings is not: each visit finds only a share of what is there, so it keeps finding minor things for a while.

### 1. Run a checkup
Open your project in Claude Code and type `/health:checkup`. It works in five stages and tells you which one it
is in:

1. **Look around.** It reads the chart's vitals, and scripts measure the code: size, complexity, copied code,
   change history. No agents yet.
2. **Plan.** It tells you which visit this is, what it will read, what it will skip, and how many agents it will
   use against the visit's budget, and waits for your yes. On the first checkup in a project it asks up to four
   quick questions first (for example, "Should the core stay free of UI code?") and proposes two small settings
   files.
3. **Review.** Reviewer agents read the code in bounded pieces, and extra passes hunt for the bugs that sit
   between files: state that survives a switch or an undo, work that finishes after its item is gone, failures
   halfway through a save. On a follow-up, each fixed problem is re-checked.
4. **Double-check.** A second agent checks every finding, by reproducing it when it can. Findings that do not hold
   up are left out of the report.
5. **Report.** You get `checkup_report.md`, then a short conversation about any design questions it raised.

The report leads with the vitals, then lists in plain words: the serious problems to fix first, what happened to
the last fixes, the other new problems, improvements worth making (with payoff and effort), design questions,
what was covered, and what was likely missed. Every problem has a chart id (C0042) that stays the same in every
later report. Minor bugs go on a watch list instead of the fix list.

### 2. Fix what it found
Type `/health:treatment`. It groups the open problems into batches (design questions first, then serious bugs),
shows you each batch (including every file it would add or delete), and fixes only what you approve. Each fix
comes with a test that fails before and passes after, and a check for the same problem in similar code. Fixed
problems are marked "treated" on the chart; the next checkup confirms them.

## What to expect

- **It does not change your code during a checkup.** The review is read-only. Only treatment edits code, and only
  batch by batch after you approve.
- **You approve the spending, within a budget.** Every visit has an agent budget (baseline 30, follow-up 6,
  routine 10 by default; change them under `budget` in `.claude/checkup/config.json`). The plan shows the number
  of agents, by model, against it before anything starts, and nothing goes over it without your yes. A small
  project is reviewed by your Claude session itself with one helper agent. A baseline of a 75,000-line app uses
  about 20 agents and several million tokens and takes half an hour or more; a routine visit a fraction of that.
- **Findings are checked, not guessed.** Every finding is verified by a second agent before it reaches the report,
  and serious ones are reproduced when possible. Severity comes from how often a bug is triggered and how bad the
  result is; it is still a judgment call.
- **One checkup finds a share of the bugs, not all of them.** A second checkup of the same code will find more,
  mostly minor bugs; serious ones tend to show up again. A file with no findings was read, not proven clean.
- **Nothing found is lost.** Every confirmed problem stays on the chart until it is fixed and confirmed, or you
  decide to leave it. Reviewers skip what is already on the chart, so a visit spends its budget on new ground, and
  a fixed problem that comes back is reported again and reopened.

## Opus or Sonnet

A checkup uses two Claude models: Opus, the stronger one, which costs more per token, and Sonnet, the cheaper one.
- The reviewers who read every line run on **Opus** by default. Given the same code and instructions, Opus
  reviewers found about twice as many confirmed bugs as Sonnet reviewers, for about a fifth more tokens.
- The bug hunts, the improvement passes and the double-check of serious findings always use Opus; mapping the
  code and the other checks use Sonnet.

Follow-up and routine visits use Sonnet reviewers and Opus only to double-check serious findings.

The plan shows how many agents of each model will run. To spend less, reply to the plan with **"use Sonnet for the
reviewers"** (this visit only), ask for the **lean** baseline (Sonnet reviewers and fewer extra passes), or lower
the budgets in the config.

## What it adds to your project

Only with your OK, on the first checkup:
- `.claude/checkup/config.json`: which folders to review or skip.
- `.claude/checkup/seed.md`: a page of notes on how your app works, for the reviewers.
- A folder for results (`.checkup/` by default), which it offers to add to `.gitignore`. It holds one folder per
  visit and the chart (`chart.json`).

All three are safe to edit or delete.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT: see [LICENSE](LICENSE).
