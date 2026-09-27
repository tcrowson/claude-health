# claude-health

A Claude Code plugin marketplace with one plugin, **health**: the `checkup` and `treatment` skills
(see [plugins/health/README.md](plugins/health/README.md)).

## Install
In Claude Code:
```
/plugin marketplace add tcrowson/claude-health
/plugin install health@claude-health
```
The repo is private for now, so this needs access to it. Without the marketplace, unzip a release zip (below)
into `~/.claude/skills/`.

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
.github/workflows/check.yml         runs tools/check.py on every push and pull request
```

## Develop
- The skills live in `plugins/health/skills/`; this repo is their source of truth.
- Before committing: `python tools/check.py` (ends with PASS). GitHub runs it on every push too.
- Try changes in Claude Code: `/plugin marketplace add <path to this repo>`, then install as above.
- Zip for someone without the marketplace: `python tools/build_zip.py`.

## Release
`python tools/release.py patch` (or `minor`, `major`, or an exact `X.Y.Z`; add `--dry-run` to preview). It
checks everything, sets the version in `plugins/health/.claude-plugin/plugin.json`, builds the zip, tags, pushes, and
publishes a GitHub release with the zip attached. Plugin users get the new version through `/plugin` updates.
Before publishing: choose a license (add `LICENSE`), make the repo public, and test an install on a clean machine.
