#!/usr/bin/env python3
"""Bumps pinned `rev:` values in `template/.pre-commit-config.yaml.jinja` via `prek update`."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger()

TEMPLATE = Path("template/.pre-commit-config.yaml.jinja")

REPO_RE = re.compile(r"^(\s*)- repo:\s*(\S+)\s*$")
REV_RE = re.compile(r"^(\s*)rev:\s*(\S+)\s*$")
HOOK_ID_RE = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$")


def extract_repo_blocks(lines: list[str]) -> dict[str, list[str]]:
    """Maps repo URL -> hook ids, skipping local blocks."""
    blocks: dict[str, list[str]] = {}
    current_repo: str | None = None
    for line in lines:
        repo_match = REPO_RE.match(line)
        if repo_match:
            current_repo = repo_match.group(2)
            if current_repo == "local":
                current_repo = None
            else:
                blocks.setdefault(current_repo, [])
            continue
        if current_repo is None:
            continue
        hook_match = HOOK_ID_RE.match(line)
        if hook_match:
            blocks[current_repo].append(hook_match.group(1))
    return blocks


def build_synthetic_config(blocks: dict[str, list[str]], revs: dict[str, str]) -> str:
    """Mocks a `.pre-commit-config.yaml` with the `blocks`."""
    out = ["repos:"]
    for repo, hook_ids in blocks.items():
        out.append(f"  - repo: {repo}")
        out.append(f"    rev: {revs[repo]}")
        out.append("    hooks:")
        out.extend(f"      - id: {hook_id}" for hook_id in hook_ids)
    return "\n".join(out) + "\n"


def extract_first_revs(lines: list[str], repos: set[str]) -> dict[str, str]:
    """Extracts the current revisions from the template lines."""
    revs: dict[str, str] = {}
    pending_repo: str | None = None
    for line in lines:
        repo_match = REPO_RE.match(line)
        if repo_match:
            pending_repo = repo_match.group(2) if repo_match.group(2) in repos else None
            continue
        if pending_repo is None:
            continue
        rev_match = REV_RE.match(line)
        if rev_match:
            revs.setdefault(pending_repo, rev_match.group(2))
            pending_repo = None
    return revs


def apply_new_revs(lines: list[str], new_revs: dict[str, str]) -> list[str]:
    """Applies the updated revisions to the template lines."""
    out = list(lines)
    pending_repo: str | None = None
    for i, line in enumerate(out):
        repo_match = REPO_RE.match(line)
        if repo_match:
            pending_repo = repo_match.group(2)
            continue
        if pending_repo is None:
            continue
        rev_match = REV_RE.match(line)
        if rev_match:
            repo = pending_repo
            pending_repo = None
            if repo in new_revs:
                out[i] = f"{rev_match.group(1)}rev: {new_revs[repo]}\n"
    return out


def main() -> None:
    """Runs `prek update` and updates the Jinja template."""
    lines = TEMPLATE.read_text().splitlines(keepends=True)
    blocks = extract_repo_blocks(lines)
    old_revs = extract_first_revs(lines, set(blocks))

    with tempfile.TemporaryDirectory() as tmp:
        # prek requires its config to live inside a git repo.
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)  # noqa: S607
        synthetic = Path(tmp) / ".pre-commit-config.yaml"
        synthetic.write_text(build_synthetic_config(blocks, old_revs))
        subprocess.run(["prek", "update"], cwd=tmp, check=True)  # noqa: S607
        new_lines = synthetic.read_text().splitlines(keepends=True)

    new_revs = extract_first_revs(new_lines, set(blocks))
    changed = {repo: rev for repo, rev in new_revs.items() if rev != old_revs[repo]}

    if not changed:
        logger.info("All pinned pre-commit revs are already up to date.")
        return

    for repo, rev in changed.items():
        logger.info("%s: %s -> %s", repo, old_revs[repo], rev)

    TEMPLATE.write_text("".join(apply_new_revs(lines, changed)))


if __name__ == "__main__":
    main()
