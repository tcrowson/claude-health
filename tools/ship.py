"""Ship dev: merge it into main with a merge commit, push, cut a release with tools/release.py, then fast-forward
dev to the release commit. Every merge to main is released, so this is the one command for it.

    python tools/ship.py patch | minor | major | X.Y.Z [--message "Merge dev: ..."] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

import release as rel

DEFAULT_TITLE = "Merge dev: {subject}"


def preflight() -> list[str]:
    """Check that dev is ready to ship: on dev, clean, in step with the remote, and ahead of main.
    Returns:
        The problems found.
    """
    problems = []
    if rel.run("git", "rev-parse", "--abbrev-ref", "HEAD") != rel.DEV_BRANCH:
        problems.append(f"not on {rel.DEV_BRANCH}")
    if rel.run("git", "status", "--porcelain"):
        problems.append("uncommitted changes")
    rel.run("git", "fetch", "--tags", rel.REMOTE)
    for branch in (rel.DEV_BRANCH, rel.BRANCH):
        if rel.run("git", "rev-parse", branch) != rel.run("git", "rev-parse", f"{rel.REMOTE}/{branch}"):
            problems.append(f"{branch} differs from {rel.REMOTE}/{branch}: pull or push first")
    if not rel.run("git", "rev-list", f"{rel.BRANCH}..{rel.DEV_BRANCH}"):
        problems.append(f"nothing to ship: {rel.DEV_BRANCH} has no commits that {rel.BRANCH} lacks")
    return problems


def main() -> int:
    """Parse arguments and ship.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="X.Y.Z, or major / minor / patch (passed to release.py)")
    ap.add_argument("--message", help="the merge commit message (default: 'Merge dev: <last dev commit subject>')")
    ap.add_argument("--dry-run", action="store_true", help="check and show the steps without changing anything")
    args = ap.parse_args()
    if not (rel.SEMVER_RX.match(args.version) or args.version in rel.BUMPS):
        ap.error("version must be X.Y.Z, major, minor or patch")
    problems = preflight()
    if problems:
        sys.stdout.write("Not ready to ship:\n" + "".join(f"- {p}\n" for p in problems))
        return 1
    subject = rel.run("git", "log", "-1", "--no-merges", "--format=%s", rel.DEV_BRANCH)
    message = args.message or DEFAULT_TITLE.format(subject=subject)
    steps = [f"merge {rel.DEV_BRANCH} into {rel.BRANCH} ({message.splitlines()[0]!r}) and push {rel.BRANCH}",
             f"release: tools/release.py {args.version}",
             f"fast-forward {rel.DEV_BRANCH} to {rel.BRANCH}, push it, and switch back to {rel.DEV_BRANCH}"]
    sys.stdout.write("Ship:\n" + "".join(f"  {i}. {s}\n" for i, s in enumerate(steps, 1)))
    if args.dry_run:
        return 0
    rel.run("git", "switch", rel.BRANCH)
    rel.run("git", "merge", "--no-ff", rel.DEV_BRANCH, "-m", message)
    rel.run("git", "push", rel.REMOTE, rel.BRANCH)
    try:
        sys.stdout.write(rel.run(sys.executable, str(rel.ROOT / "tools" / "release.py"), args.version) + "\n")
    except SystemExit:
        sys.stdout.write(f"{rel.BRANCH} is merged and pushed but not released. Fix the problem above, then run "
                         f"tools/release.py {args.version} on {rel.BRANCH} and fast-forward {rel.DEV_BRANCH}.\n")
        raise
    rel.run("git", "switch", rel.DEV_BRANCH)
    rel.run("git", "merge", "--ff-only", rel.BRANCH)
    rel.run("git", "push", rel.REMOTE, rel.DEV_BRANCH)
    sys.stdout.write(f"Shipped: {rel.BRANCH} and {rel.DEV_BRANCH} are at {rel.run('git', 'rev-parse', '--short', 'HEAD')}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
