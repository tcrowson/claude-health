"""Check the marketplace before a commit or release: the manifests, each skill's front matter, Claude Code's own
validator (when the claude CLI is installed), and each skill's self-test. GitHub runs this on every push.

    python tools/check.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
MANIFEST = Path(".claude-plugin") / "plugin.json"
SEMVER_RX = re.compile(r"^\d+\.\d+\.\d+$")
FRONT_MATTER_RX = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
FIELD_RX = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
SELFTEST = Path("scripts") / "selftest.py"
LICENSE = "LICENSE"
CLAUDE = "claude"


def read_json(path: Path, errors: list[str]) -> dict:
    """Read a JSON file, recording a parse failure.
    Args:
        path: The file.
        errors: Collects problems.
    Returns:
        The parsed object, or {} when it is missing or broken.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        return {}


def check_skill(folder: Path, errors: list[str]) -> None:
    """Check that a skill folder has a SKILL.md whose front matter names the folder and describes the skill.
    Args:
        folder: The skill folder.
        errors: Collects problems.
    """
    skill = folder / "SKILL.md"
    if not skill.is_file():
        errors.append(f"{folder.relative_to(ROOT)}: no SKILL.md")
        return
    match = FRONT_MATTER_RX.match(skill.read_text(encoding="utf-8").replace("\r\n", "\n"))
    fields = dict(FIELD_RX.findall(match.group(1))) if match else {}
    if fields.get("name") != folder.name:
        errors.append(f"{skill.relative_to(ROOT)}: front matter name must be '{folder.name}'")
    if not fields.get("description"):
        errors.append(f"{skill.relative_to(ROOT)}: front matter needs a description")


def check_manifests(errors: list[str]) -> list[Path]:
    """Check the marketplace catalog and every plugin it lists.
    Args:
        errors: Collects problems.
    Returns:
        The plugin folders.
    """
    market = read_json(MARKETPLACE, errors)
    for key in ("name", "plugins"):
        if not market.get(key):
            errors.append(f"marketplace.json: missing '{key}'")
    if not (market.get("owner") or {}).get("name"):
        errors.append("marketplace.json: missing owner.name")
    folders = []
    for entry in market.get("plugins", []):
        source = entry.get("source", "")
        folder = ROOT / source
        if not source.startswith("./") or not folder.is_dir():
            errors.append(f"marketplace.json: plugin '{entry.get('name')}' source '{source}' is not a folder here")
            continue
        manifest = read_json(folder / MANIFEST, errors)
        if manifest.get("name") != entry.get("name"):
            errors.append(f"{source}: plugin.json name '{manifest.get('name')}' differs from the catalog's")
        if not SEMVER_RX.match(str(manifest.get("version", ""))):
            errors.append(f"{source}: plugin.json version must look like 1.2.3")
        skills = sorted(p for p in (folder / "skills").iterdir() if p.is_dir()) if (folder / "skills").is_dir() else []
        if not skills:
            errors.append(f"{source}: no skills")
        for skill in skills:
            check_skill(skill, errors)
        root_license, plugin_license = ROOT / LICENSE, folder / LICENSE
        if not plugin_license.is_file() or (root_license.is_file()
                                            and plugin_license.read_bytes() != root_license.read_bytes()):
            errors.append(f"{source}: needs a {LICENSE} identical to the repo's (installs copy only the plugin folder)")
        folders.append(folder)
    return folders


def run(cmd: list[str], label: str, errors: list[str]) -> None:
    """Run a command, recording a failure with the tail of its output.
    Args:
        cmd: The command.
        label: What it checks.
        errors: Collects problems.
    """
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    status = "ok  " if done.returncode == 0 else "FAIL"
    sys.stdout.write(f"  {status}  {label}\n")
    if done.returncode != 0:
        tail = (done.stdout + done.stderr).strip().splitlines()[-15:]
        errors.append(f"{label} failed:\n    " + "\n    ".join(tail))


def main() -> int:
    """Run every check and report.
    Returns:
        0 when everything passes, 1 otherwise.
    """
    errors: list[str] = []
    sys.stdout.write("manifests and skills\n")
    folders = check_manifests(errors)
    sys.stdout.write(f"  {'ok  ' if not errors else 'FAIL'}  catalog, {len(folders)} plugin(s), skill front matter\n")
    sys.stdout.write("claude plugin validate\n")
    claude = shutil.which(CLAUDE)
    if claude:
        run([claude, "plugin", "validate", str(ROOT)], "marketplace", errors)
        for folder in folders:
            run([claude, "plugin", "validate", str(folder)], f"plugin {folder.name}", errors)
    else:
        sys.stdout.write("  skip  the claude CLI is not installed here\n")
    sys.stdout.write("self-tests\n")
    for folder in folders:
        for test in sorted((folder / "skills").glob(f"*/{SELFTEST.as_posix()}")):
            run([sys.executable, str(test)], f"{test.parent.parent.name} self-test", errors)
    if errors:
        sys.stdout.write("\nFAIL\n" + "\n".join(f"- {e}" for e in errors) + "\n")
        return 1
    sys.stdout.write("\nPASS\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
