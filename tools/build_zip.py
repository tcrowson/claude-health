"""Build the zip for installing the health skills without the marketplace.
Puts both skill folders side by side with install notes, skips caches, and names the zip after the version in
the plugin manifest: dist/health-skills-<version>.zip.

    python tools/build_zip.py
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "health"
SKILLS = PLUGIN / "skills"
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
DIST = ROOT / "dist"
SKIP_PARTS = {"__pycache__", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc"}
INSTALL_NAME = "INSTALL.txt"
INSTALL_TEXT = """Health skills {version}: /checkup and /treatment for Claude Code

INSTALL
1. Unzip into your Claude Code skills folder, so that these two folders land there side by side:
     Windows:        %USERPROFILE%\\.claude\\skills\\checkup   and  ...\\treatment
     macOS / Linux:  ~/.claude/skills/checkup             and  ~/.claude/skills/treatment
   (This INSTALL.txt can be deleted afterwards.)
2. Start a new Claude Code session.

USE
- In any repo, type /checkup (or ask for a "checkup"). The first time it asks a few quick questions and, with
  your OK, adds two small settings files in .claude/checkup/ so later checkups don't ask again.
- To fix what it finds, type /treatment.

NEEDS
- Python 3.10 or newer and git on PATH. Node.js is optional (the self-test uses it).
- On bigger repos, multi-agent runs use Claude Code's Workflow tool; small repos run without it.

CHECK THE INSTALL (optional)
  python ~/.claude/skills/checkup/scripts/selftest.py      (ends with PASS)
"""


def skill_files(folder: Path) -> list[Path]:
    """List the files of one skill folder, without caches.
    Args:
        folder: The skill folder.
    Returns:
        The files to pack, sorted.
    """
    return sorted(p for p in folder.rglob("*") if p.is_file() and not SKIP_PARTS & set(p.parts)
                  and p.suffix not in SKIP_SUFFIXES)


def main() -> int:
    """Write the zip and print what went in.
    Returns:
        The process exit code.
    """
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    DIST.mkdir(exist_ok=True)
    out = DIST / f"health-skills-{version}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(INSTALL_NAME, INSTALL_TEXT.format(version=version))
        for skill in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
            for f in skill_files(skill):
                z.write(f, f.relative_to(SKILLS).as_posix())
                count += 1
    sys.stdout.write(f"{out} ({count} files + {INSTALL_NAME}, {out.stat().st_size // 1024} KB)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
