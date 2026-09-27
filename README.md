# claude-health

A Claude Code plugin marketplace with one plugin, **health**: the `checkup` and `treatment` skills
(see [plugins/health/README.md](plugins/health/README.md)).

## Install
In Claude Code:
```
/plugin marketplace add tcrowson/claude-health
/plugin install health@claude-health
```
Without the marketplace: download the zip from the [latest release](https://github.com/tcrowson/claude-health/releases/latest)
and unzip it into `~/.claude/skills/` (its `INSTALL.txt` explains).

## Layout
```
.claude-plugin/marketplace.json     the catalog Claude Code reads
plugins/health/
  .claude-plugin/plugin.json        name and version
  skills/checkup/                   the review skill: SKILL.md, REFERENCE.md, PROFILES.md, scripts/
  skills/treatment/                 the fixing skill
tools/check.py                      manifests, skill front matter, claude plugin validate, self-tests
tools/release.py                    check, version, zip, tag, push, GitHub release
tools/build_zip.py                  builds dist/health-skills-<version>.zip for installing without the marketplace
.github/workflows/check.yml         runs tools/check.py on pushes to main and dev, and on pull requests
```

## Develop
- **Branches:** work on `dev`; merge into `main` when a change is ready to ship. `main` is what users install and
  where releases are cut from. GitHub blocks deleting either branch.
- The skills live in `plugins/health/skills/`; this repo is their source of truth.
- Before committing: `python tools/check.py` (ends with PASS). GitHub runs it on pushes to `dev` and `main` too.
- Try changes in Claude Code: `/plugin marketplace add <path to this repo>`, then install as above.
- Zip for someone without the marketplace: `python tools/build_zip.py`.

## Release
1. Merge `dev` into `main` and switch to `main`.
2. `python tools/release.py patch` (or `minor`, `major`, or an exact `X.Y.Z`; add `--dry-run` to preview). It checks
   everything, sets the version in `plugins/health/.claude-plugin/plugin.json`, builds the zip, tags, pushes, and
   publishes a GitHub release with the zip attached. It runs only on `main`.
3. Merge `main` back into `dev`, so `dev` has the version commit, and switch back to `dev`.

Plugin users get the new version through `/plugin` updates.
