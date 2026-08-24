#!/usr/bin/env python3
"""Bootstraps template updates into the `copier-template` repo."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _typeshed import StrPath

logger = logging.getLogger()


def run_cmd(
    cmd: Sequence[StrPath],
    cwd: Path,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Runs a command in the provided directory."""
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def sync_uncommitted_changes(src_dir: Path, dst_dir: Path) -> None:
    """Syncs modified, deleted, and untracked files while respecting `.gitignore` rules."""
    # -m: modified, -d: deleted, -o: untracked, --exclude-standard: use .gitignore & excludes
    res = run_cmd(
        ["git", "ls-files", "-m", "-d", "-o", "--exclude-standard", "-z"],
        cwd=src_dir,
        capture_output=True,
    )
    changed_rel_paths = [Path(p) for p in res.stdout.split("\0") if p]

    for rel_path in changed_rel_paths:
        src = src_dir / rel_path
        dst = dst_dir / rel_path

        if not src.exists() and not src.is_symlink():
            # file was deleted in workspace
            if dst.is_file() or dst.is_symlink():
                dst.unlink()
            elif dst.is_dir():
                shutil.rmtree(dst)
        else:
            # file was modified or newly created
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_dir():
                shutil.rmtree(dst)
            shutil.copy2(src, dst, follow_symlinks=False)


def set_copier_answers_commit(repo_dir: Path, commit_hash: str) -> None:
    """Ensures `.copier-answers.yml` exists and contains the current `_commit` hash."""
    answers_file = repo_dir / ".copier-answers.yml"
    if not answers_file.exists():
        answers_file.write_text(f"_commit: {commit_hash}\n_src_path: .\n")
        return

    content = answers_file.read_text()
    if re.search(r"^_commit:.*$", content, flags=re.MULTILINE):
        new_content = re.sub(
            r"^_commit:.*$", f"_commit: {commit_hash}", content, flags=re.MULTILINE
        )
    else:
        new_content = f"_commit: {commit_hash}\n" + content

    answers_file.write_text(new_content)


def parse_overwrite(argv: Sequence[str] | None = None) -> bool:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Apply the merged update to the workspace without asking for confirmation.",
    )
    return parser.parse_args(argv).overwrite  # type: ignore[no-any-return]


def main(argv: Sequence[str] | None = None) -> int:
    """Bootstraps template updates into the `copier-template` repo."""
    overwrite = parse_overwrite(argv)
    original_dir = Path.cwd().resolve()

    if not (original_dir / ".git").exists():
        logger.error("The current workspace must be a Git repository.")
        raise SystemExit(1)

    head_hash = run_cmd(
        ["git", "rev-parse", "HEAD"],
        cwd=original_dir,
        capture_output=True,
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="copier_update_") as tmp:
        tmp_dir = Path(tmp)

        logger.info("Cloning repository into temp workspace: %s", tmp_dir)
        run_cmd(["git", "clone", "--local", original_dir, tmp_dir], cwd=original_dir)

        # `git clone` does not copy the source repo's local user.name/user.email, and the
        # environment may have no global git identity configured (e.g. CI runners). Set a
        # throwaway identity since this clone's commits are never pushed anywhere.
        run_cmd(["git", "config", "user.email", "copier-bootstrap@local"], cwd=tmp_dir)
        run_cmd(["git", "config", "user.name", "Copier Bootstrap"], cwd=tmp_dir)

        logger.info("Syncing uncommitted changes (respecting .gitignore)...")
        sync_uncommitted_changes(original_dir, tmp_dir)

        logger.info("Writing commit hash (%s) to .copier-answers.yml...", head_hash[:7])
        set_copier_answers_commit(tmp_dir, head_hash)

        # commit uncommitted changes in temp so working tree is clean for copier update
        status = run_cmd(["git", "status", "--porcelain"], cwd=tmp_dir, capture_output=True)
        if status.stdout.strip():
            run_cmd(["git", "add", "-A"], cwd=tmp_dir)
            run_cmd(["git", "commit", "-qm", "Snapshot: pre-update working tree"], cwd=tmp_dir)

        logger.info("Running native copier update with 3-way merge...")
        run_cmd(
            [
                "copier",
                "update",
                "--skip-answered",
                "--defaults",
                "--trust",
                "--vcs-ref",
                "HEAD",
            ],
            cwd=tmp_dir,
        )

        logger.info("Updating commit hash in .copier-answers.yml to leave it out of diff...")
        set_copier_answers_commit(tmp_dir, head_hash)

        logger.info("Diff of update result:")
        diff_res = run_cmd(["git", "diff"], cwd=tmp_dir, capture_output=True)
        diff_text = diff_res.stdout

        if not diff_text.strip():
            logger.info("No template changes to apply.")
            return 0

        print("\n" + "=" * 60)  # noqa: T201
        print(diff_text)  # noqa: T201
        print("=" * 60 + "\n")  # noqa: T201

        if overwrite:
            choice = "y"
        else:
            try:
                choice = (
                    input("Apply merged result to your original workspace? [y/N]: ").strip().lower()
                )
            except (KeyboardInterrupt, EOFError):
                logger.error("Aborted.")  # noqa: TRY400
                return 1

        if choice in {"y", "yes"}:
            patch_file = tmp_dir / "copier_update.patch"
            patch_file.write_text(diff_text)

            logger.info("Applying 3-way merged patch back to original workspace...")
            res = run_cmd(
                ["git", "apply", "--3way", patch_file],
                cwd=original_dir,
                check=False,
            )
            if res.returncode == 0:
                logger.info("Update applied cleanly.")
            else:
                logger.warning("Update applied with conflict markers. Check your workspace files.")

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
