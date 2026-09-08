#!/usr/bin/env python3
"""Push a branch to both published copies of this repository, and prove it.

The project is published twice:

    origin      github.com/mohammadkhair7/khair-arabic-models
    qurancomp   github.com/qurancomp/khair-arabic-models

They must never drift. A single `git push` cannot reach both, because each
repository is writable by a different GitHub account and git only ever
presents one credential per host. So this script pushes each remote with its
own account active, using `gh auth switch`, and then re-reads both remote
heads to confirm they actually match — a push that "succeeded" while the
other one failed is exactly the failure mode worth catching.

    python scripts/sync_repos.py                 # push the current branch
    python scripts/sync_repos.py --check         # verify only, push nothing
    python scripts/sync_repos.py --branch main

Requires `gh auth login` for both accounts (`gh auth status` should list
them). The originally active account is restored on the way out, including
when a push fails.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# remote name -> the GitHub account that can write to it
TARGETS = {
    "origin": "mohammadkhair7",
    "qurancomp": "qurancomp",
}


def run(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, check=False,
                          capture_output=capture)


def gh_active() -> str | None:
    result = run("gh", "api", "user", "--jq", ".login")
    return result.stdout.strip() or None


def gh_switch(account: str) -> None:
    result = run("gh", "auth", "switch", "--user", account)
    if result.returncode != 0:
        raise SystemExit(
            f"could not switch to '{account}'.\n"
            f"  {result.stderr.strip()}\n"
            f"  Run `gh auth login` for that account first.")


def current_branch() -> str:
    return run("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def local_head(branch: str) -> str:
    return run("git", "rev-parse", branch).stdout.strip()


def remote_head(remote: str, branch: str) -> str | None:
    result = run("git", "ls-remote", remote, branch)
    line = result.stdout.strip()
    return line.split()[0] if line else None


def push(remote: str, branch: str) -> bool:
    print(f"  pushing to {remote} ...", flush=True)
    # Not captured: git's progress and LFS upload meter should be visible.
    result = run("git", "push", remote, branch, capture=False)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--check", action="store_true",
                        help="report drift without pushing")
    args = parser.parse_args()

    branch = args.branch or current_branch()
    local = local_head(branch)
    print(f"branch {branch} at {local[:10]}\n")

    restore = gh_active()
    failures: list[str] = []

    if not args.check:
        try:
            for remote, account in TARGETS.items():
                if remote_head(remote, branch) == local:
                    print(f"  {remote}: already up to date")
                    continue
                gh_switch(account)
                if not push(remote, branch):
                    failures.append(remote)
        finally:
            if restore:
                gh_switch(restore)
                print(f"\nrestored active account: {restore}")

    # Verify from the remotes themselves rather than trusting the push output.
    print("\nverifying:")
    heads = {}
    for remote in TARGETS:
        head = remote_head(remote, branch)
        heads[remote] = head
        mark = "ok " if head == local else "OUT OF SYNC"
        print(f"  {mark:<12} {remote:<10} {(head or '(missing)')[:10]}")

    if failures:
        print(f"\nFAILED to push: {', '.join(failures)}", file=sys.stderr)
    if any(head != local for head in heads.values()):
        print("\nThe two published repositories do not match. Re-run this "
              "script, or push the lagging remote by hand after "
              "`gh auth switch --user <account>`.", file=sys.stderr)
        return 1

    print("\nboth repositories are in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
