"""Cut a release: check everything, set the plugin version, build the zip, commit, tag, push, and publish a GitHub
release with the zip attached. Releasing the version already in plugin.json skips the version commit.

    python tools/release.py 0.2.0 | patch | minor | major [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "plugins" / "health" / ".claude-plugin" / "plugin.json"
DIST = ROOT / "dist"
ZIP_NAME = "health-skills-{version}.zip"
BRANCH = "main"
DEV_BRANCH = "dev"
REMOTE = "origin"
GH_CANDIDATES = ("gh", r"C:\Program Files\GitHub CLI\gh.exe")
BUMPS = ("major", "minor", "patch")
SEMVER_RX = re.compile(r"^\d+\.\d+\.\d+$")


def run(*cmd: str) -> str:
    """Run a command in the repo, failing loudly.
    Args:
        *cmd: The command and its arguments.
    Returns:
        Its stdout, stripped.
    """
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{done.stdout}{done.stderr}")
    return done.stdout.strip()


def next_version(current: str, target: str) -> str:
    """Work out the release version.
    Args:
        current: The version in plugin.json.
        target: An explicit version, or major / minor / patch.
    Returns:
        The version to release.
    """
    if SEMVER_RX.match(target):
        return target
    parts = [int(p) for p in current.split(".")]
    i = BUMPS.index(target)
    parts[i] += 1
    parts[i + 1:] = [0] * (2 - i)
    return ".".join(map(str, parts))


def find_gh() -> str | None:
    """Find the GitHub CLI.
    Returns:
        Its path, or None.
    """
    for candidate in GH_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def preflight(version: str) -> list[str]:
    """Check the repo is ready: on main, clean, in step with the remote, and the tag is new.
    Args:
        version: The version to release.
    Returns:
        The problems found.
    """
    problems = []
    if run("git", "rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        problems.append(f"not on {BRANCH}")
    if run("git", "status", "--porcelain"):
        problems.append("uncommitted changes")
    run("git", "fetch", "--tags", REMOTE)
    if run("git", "rev-parse", "HEAD") != run("git", "rev-parse", f"{REMOTE}/{BRANCH}"):
        problems.append(f"{BRANCH} differs from {REMOTE}/{BRANCH}: pull or push first")
    if run("git", "tag", "--list", f"v{version}"):
        problems.append(f"tag v{version} already exists")
    return problems


def main() -> int:
    """Parse arguments and cut the release.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="X.Y.Z, or major / minor / patch")
    ap.add_argument("--dry-run", action="store_true", help="check and show the steps without changing anything")
    args = ap.parse_args()
    if not (SEMVER_RX.match(args.version) or args.version in BUMPS):
        ap.error("version must be X.Y.Z, major, minor or patch")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current = manifest["version"]
    version = next_version(current, args.version)
    problems = preflight(version)
    if problems:
        sys.stdout.write("Not ready to release:\n" + "".join(f"- {p}\n" for p in problems))
        return 1
    gh = find_gh()
    zip_path = DIST / ZIP_NAME.format(version=version)
    steps = ["run tools/check.py",
             f"set plugin.json version {current} -> {version} and commit" if version != current
             else f"release the current version {version} (no version commit)",
             f"build {zip_path.relative_to(ROOT).as_posix()}",
             f"tag v{version} and push {BRANCH} with the tag",
             f"publish GitHub release v{version} with the zip" if gh else "print the GitHub release command (gh not found)"]
    sys.stdout.write(f"Release v{version}:\n" + "".join(f"  {i}. {s}\n" for i, s in enumerate(steps, 1)))
    if args.dry_run:
        return 0
    run(sys.executable, str(ROOT / "tools" / "check.py"))
    if version != current:
        manifest["version"] = version
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        run("git", "commit", "-am", f"Release v{version}")
    run(sys.executable, str(ROOT / "tools" / "build_zip.py"))
    run("git", "tag", "-a", f"v{version}", "-m", f"health {version}")
    run("git", "push", REMOTE, BRANCH, "--follow-tags")
    release = ["release", "create", f"v{version}", str(zip_path), "--title", f"health {version}",
               "--generate-notes", "--verify-tag"]
    if gh:
        sys.stdout.write(run(gh, *release) + "\n")
    else:
        sys.stdout.write("Publish it with: gh " + " ".join(release) + "\n")
    if version != current:
        sys.stdout.write(f"Next, bring the version commit into {DEV_BRANCH}: "
                         f"git switch {DEV_BRANCH} && git merge {BRANCH} && git push\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
