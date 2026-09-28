# Contributing to claude-health

## Layout
```
.claude-plugin/marketplace.json     the catalog Claude Code reads
plugins/health/
  .claude-plugin/plugin.json        name and version
  skills/checkup/                   the review skill: SKILL.md, REFERENCE.md, PROFILES.md, scripts/
  skills/treatment/                 the fixing skill
tools/check.py                      manifests, skill front matter, claude plugin validate, self-tests
tools/ship.py                       merge dev into main, release, fast-forward dev: the one command to ship
tools/release.py                    check, version, zip, tag, push, GitHub release (ship.py runs it)
tools/build_zip.py                  builds dist/health-skills-<version>.zip for installing without the marketplace
.github/workflows/check.yml         runs tools/check.py on pushes to main and dev, and on pull requests
```
The skills live in `plugins/health/skills/`; this repo is their source of truth. The checkup's scripts use only the
Python standard library, and `scripts/selftest.py` checks them on built-in fixtures.

## Develop
- **Branches:** work on `dev`; merge into `main` when a change is ready to ship. `main` is what users install and
  where releases are cut. GitHub blocks deleting either branch.
- **Before committing:** `python tools/check.py` must end with PASS. GitHub runs it on every push to `dev` and `main`.
- **Keep the skills project-neutral:** no project names, paths or domain terms in skill files, scripts or tests.
- **Try a change in Claude Code:** `/plugin marketplace add <path to this repo>`, then install as in the README.
- **Measure changes to the checkup's reviewers** before adopting them: SKILL.md's "Measuring the skill" section
  describes blind replicas and `scripts/compare.py`.

## Ship: every merge to main is released
From a clean `dev` that is pushed: `python tools/ship.py patch` (or `minor`, `major`, or an exact `X.Y.Z`;
`--message` sets the merge commit message; `--dry-run` previews). It:
1. merges `dev` into `main` with a merge commit (not a squash) and pushes `main`;
2. runs `tools/release.py`, which checks everything, sets the version in `plugin.json`, builds the zip, tags,
   pushes, and publishes a GitHub release with the zip attached;
3. fast-forwards `dev` to the release commit, pushes it, and switches back to `dev`.

If the release step fails, `main` is merged but not released: fix the problem, run `tools/release.py` on `main`,
then fast-forward `dev`.

Use `patch` for fixes, `minor` for new features or changed behavior, `major` for changes that break existing
setups. Plugin users get the new version through `/plugin` updates; others download the release zip.
