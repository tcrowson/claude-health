# claude-health

Two skills for [Claude Code](https://claude.com/claude-code) that look after a codebase's health:

- **checkup** reviews your whole codebase for bugs and for ways to make the code simpler or faster, double-checks
  everything it finds, and writes a plain-language report.
- **treatment** fixes what a checkup found, one batch at a time, only with your approval, and adds a test for
  each fix.

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

### 1. Run a checkup
Open your project in Claude Code and type `/health:checkup`. It works in five stages and tells you which one it
is in:

1. **Look around.** Scripts measure the code: size, complexity, copied code, change history. No agents yet.
2. **Plan.** It tells you what it will read, what it will skip, and how many agents it will use, and waits for
   your yes. On the first checkup in a project it asks up to four quick questions first (for example, "Should the
   core stay free of UI code?") and proposes two small settings files.
3. **Review.** Reviewer agents read every line of the code in bounded pieces, and extra passes hunt for the bugs
   that sit between files: state that survives a switch or an undo, work that finishes after its item is gone,
   failures halfway through a save.
4. **Double-check.** A second agent checks every finding, by reproducing it when it can. Findings that do not hold
   up are left out of the report.
5. **Report.** You get `checkup_report.md`, then a short conversation about any design questions it raised.

The report lists, in plain words: the bugs to fix first, the other bugs, improvements worth making (with the
payoff and effort of each), design questions, what was covered, and what was likely missed.

### 2. Fix what it found
Type `/health:treatment`. It groups the findings into batches, shows you each batch (including every file it
would add or delete), and fixes only what you approve. Each fix comes with a test that fails before and passes
after, and the outcome is recorded so the next checkup knows what was fixed.

## What to expect

- **It does not change your code during a checkup.** The review is read-only. Only treatment edits code, and only
  batch by batch after you approve.
- **You approve the spending.** The plan shows the number of agents, by model, before anything starts. A small
  project is reviewed by your Claude session itself with one helper agent. A large one uses many agents: for a
  75,000-line app, about 20 agents and several million tokens, taking half an hour or more.
- **Findings are checked, not guessed.** Every finding is verified by a second agent before it reaches the report,
  and serious ones are reproduced when possible. Severity comes from how often a bug is triggered and how bad the
  result is; it is still a judgment call.
- **One checkup finds a share of the bugs, not all of them.** A second checkup of the same code will find more,
  mostly minor bugs; serious ones tend to show up again. A file with no findings was read, not proven clean.
- **It gets better at your project over time.** Each run remembers what was already reported, fixed or declined,
  so it does not repeat itself, and it tracks how the code's measurements change between checkups.

## Opus or Sonnet

A checkup uses two Claude models: Opus, the stronger one, which costs more per token, and Sonnet, the cheaper one.
- The reviewers who read every line run on **Opus** by default. Given the same code and instructions, Opus
  reviewers found about twice as many confirmed bugs as Sonnet reviewers, for about a fifth more tokens.
- The bug hunts, the improvement passes and the double-check of serious findings always use Opus; mapping the
  code and the other checks use Sonnet.

The plan shows how many agents of each model will run. To spend less, reply to the plan with **"use Sonnet for the
reviewers"** (this run only), or ask for the **lean** checkup (Sonnet reviewers and fewer extra passes).

## What it adds to your project

Only with your OK, on the first checkup:
- `.claude/checkup/config.json`: which folders to review or skip.
- `.claude/checkup/seed.md`: a page of notes on how your app works, for the reviewers.
- A folder for results (`.checkup/` by default), which it offers to add to `.gitignore`.

All three are safe to edit or delete.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT: see [LICENSE](LICENSE).
