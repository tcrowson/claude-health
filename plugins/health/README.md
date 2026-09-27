# health

Two Claude Code skills that work as a pair:

- **checkup** reviews a whole codebase for bugs and for ways to make it simpler or faster. It measures the code
  with scripts, has reviewer agents read every line, double-checks every finding, and writes a plain-language
  report. On a large repo it uses several agents; on a small one it reads the code itself.
- **treatment** works through a checkup's findings: a triage table, your approval batch by batch, a test for
  each fix, and the outcome written back so the next checkup knows.

Installed as a plugin they are `/health:checkup` and `/health:treatment`; installed as plain skills they are
`/checkup` and `/treatment`. Keep the two folders side by side: treatment uses files from checkup.

## What it needs
- Python 3.10 or newer and git on PATH. Node.js is optional (only the self-test uses it).
- Multi-agent runs on bigger repos use Claude Code's Workflow tool. Small repos run without it.

## What it adds to a repo
On the first checkup, and only with your OK: two small settings files in `.claude/checkup/` (what to review, and
notes on how the app works) and a working folder for run results, which it offers to add to `.gitignore`.

## Check an install
`python <skills folder>/checkup/scripts/selftest.py` runs every script on built-in test code and ends with PASS.

## License
MIT: see [LICENSE](LICENSE).
