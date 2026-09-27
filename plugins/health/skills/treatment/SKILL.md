---
name: treatment
description: Acts on a /checkup's results. Builds a triage table from the confirmed defects, accepted improvements and trajectory cards, gets the user's approval batch by batch, including every file to be created or deleted, then fixes each item with a test, measures performance changes, re-reviews the fix diff, and writes the outcome back to the checkup's findings. Use when the user asks to treat, fix or act on checkup findings, or to work through a checkup report.
---

# Treatment

Works from the latest checkup run in `<data_root>/<date>/` (config `.claude/checkup/config.json`):
`findings.json` and `trajectory.json`, whose schema is in the checkup skill's `REFERENCE.md`. The checkup skill
folder sits next to this one (`<this skill dir>/../checkup/`), wherever the pair is installed. Follow
the project's CLAUDE.md for tests, lint, commits and pace; commit only when the user asks.

## Talking to the user

Same rules as the checkup skill ("Talking to the user" in `<this skill dir>/../checkup/SKILL.md`): say where we
are and what you need first, in plain words; no raw files in chat; counts while working, details when done.
Refer to items by their report IDs and titles ("B1, Mirror with nothing selected crashes"). For every file to
add or remove, one line on what it is and why, e.g. "`tests/test_mirror.py`: a test that fails today and passes
after the fix, so the bug can't quietly come back." Each batch ends with a short done message: what changed, the
test result in one line, and a link to the full output.

## Steps

1. **Load** the open items: defects with status `confirmed`, improvements `accepted`, and trajectory cards
   `accepted`, with their report IDs (the report's "For agents" section maps them). Re-check each item's file:line at HEAD; drop or re-anchor what moved, and say so.
2. **Triage table.** Group the items into batches by root cause and directory. Per batch, one row with:
   ask, feasibility, effort, consequences, **new files** (every test, script, fixture or module it would create,
   by path) and **deleted files** (including tests made redundant). Put data-threatening defects first, and
   trajectory cards in batches of their own.
3. **Approval.** The user approves, reorders or drops batches. The approval covers exactly the listed files:
   if a fix turns out to need a file that is not on the list, stop and ask before creating it, even a test.
4. **Fix each item.**
   - A defect gets a test that fails before the fix and passes after, where feasible (the tdd skill works for
     this). Test through the module's interface, the same one callers use.
   - A performance item gets before and after numbers from the measurement plan, on the same input.
     A change that does not move the number is reverted and reported as such.
   - A structural change picks its test strategy by dependency: in-process code is tested through the new
     interface; a dependency with a local stand-in (in-memory database, temp folder) uses the stand-in; a seam
     with two real implementations gets a port with a test adapter; true externals get a mock.
   - A trajectory card: before writing code, lay out at least two genuinely different designs (smallest
     interface, easiest common case, ports and adapters) and compare them on depth, locality and seam
     placement, then recommend one and get the user's pick. Migrate in steps that each leave the tests green.
   - Old tests made redundant by a test at the new interface are deleted only when that deletion was approved.
5. **Verify.** Run the batch's tests and the linter after each fix, and the full suite at the project's
   cadence. Show the output. Then run `/code-review` on the batch's diff and fix what it confirms.
6. **Record.** Set each item's `status` in findings.json or trajectory.json (`fixed` with `commit`, `wontfix`
   with a reason, or `deferred`), then run
   `python <this skill dir>/../checkup/scripts/save_run.py --refresh-known` so the next checkup skips what is
   closed and re-reports a fixed bug that comes back.
7. **Report** per batch: what changed, the files created and deleted, test and measurement output, and
   anything skipped and why.

## Agents

Default to fixing in the main loop. Use agents only for a batch that splits cleanly by directory, one agent per
directory, each in its own worktree (`isolation: "worktree"`). State the count and model first. Each brief
lists the items, the only files the agent may create or delete, the project's invariants, how to run the
tests, and requires verbatim test output. Spot-check agent code on real data, not only on its own tests.
