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
tools/build_zip.py                  builds dist/health-skills-<version>.zip for installing without the marketplace
```

## Develop
- The skills live in `plugins/health/skills/`; this repo is their source of truth.
- After changing a script: `python plugins/health/skills/checkup/scripts/selftest.py` (ends with PASS).
- Try changes in Claude Code: `/plugin marketplace add <path to this repo>`, then install as above.
- Zip for someone without the marketplace: `python tools/build_zip.py`.

## Release
Bump `version` in `plugins/health/.claude-plugin/plugin.json`, commit, tag `v<version>`, push.
Before publishing: choose a license (add `LICENSE`), make the repo public, and test an install on a clean machine.
