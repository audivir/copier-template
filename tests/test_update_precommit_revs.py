"""Test the `update_precommit_revs` module."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import update_precommit_revs

if TYPE_CHECKING:
    import pytest

SAMPLE_TEMPLATE = """\
repos:
  - repo: https://example.com/repo-a
    rev: v1.0.0
    hooks:
      - id: hook-a
  - repo: local
    hooks:
      - id: local-only
  - repo: https://example.com/repo-b
    rev: v2.0.0
    hooks:
      - id: hook-b
      - id: hook-b2
"""


def test_extract_repo_blocks() -> None:
    lines = SAMPLE_TEMPLATE.splitlines(keepends=True)
    blocks = update_precommit_revs.extract_repo_blocks(lines)
    assert blocks == {
        "https://example.com/repo-a": ["hook-a"],
        "https://example.com/repo-b": ["hook-b", "hook-b2"],
    }


def test_build_synthetic_config() -> None:
    blocks = {"https://example.com/repo-a": ["hook-a"]}
    revs = {"https://example.com/repo-a": "v1.0.0"}
    assert update_precommit_revs.build_synthetic_config(blocks, revs) == (
        "repos:\n"
        "  - repo: https://example.com/repo-a\n"
        "    rev: v1.0.0\n"
        "    hooks:\n"
        "      - id: hook-a\n"
    )


def test_extract_first_revs() -> None:
    lines = SAMPLE_TEMPLATE.splitlines(keepends=True)
    revs = update_precommit_revs.extract_first_revs(
        lines, {"https://example.com/repo-a", "https://example.com/repo-b"}
    )
    assert revs == {
        "https://example.com/repo-a": "v1.0.0",
        "https://example.com/repo-b": "v2.0.0",
    }


def test_extract_first_revs_skips_non_rev_lines() -> None:
    lines = [
        "  - repo: https://example.com/repo-a\n",
        "    # comment between repo and rev\n",
        "    rev: v1.0.0\n",
    ]
    revs = update_precommit_revs.extract_first_revs(lines, {"https://example.com/repo-a"})
    assert revs == {"https://example.com/repo-a": "v1.0.0"}


def test_apply_new_revs() -> None:
    lines = SAMPLE_TEMPLATE.splitlines(keepends=True)
    new_lines = update_precommit_revs.apply_new_revs(
        lines, {"https://example.com/repo-a": "v1.1.0"}
    )
    assert "    rev: v1.1.0\n" in new_lines
    assert "    rev: v2.0.0\n" in new_lines


def _write_template(tmp_path: Path) -> Path:
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    template_path = template_dir / ".pre-commit-config.yaml.jinja"
    template_path.write_text(SAMPLE_TEMPLATE)
    return template_path


def test_main_no_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    template_path = _write_template(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(update_precommit_revs, "TEMPLATE", template_path)

    real_run = subprocess.run

    def fake_run(cmd: list[str], *, cwd: str, check: bool) -> None:
        if cmd == ["prek", "update"]:
            return
        real_run(cmd, cwd=cwd, check=check)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        update_precommit_revs.main()

    assert "already up to date" in caplog.text
    assert template_path.read_text() == SAMPLE_TEMPLATE


def test_main_applies_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    template_path = _write_template(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(update_precommit_revs, "TEMPLATE", template_path)

    real_run = subprocess.run

    def fake_run(cmd: list[str], *, cwd: str, check: bool) -> None:
        if cmd == ["prek", "update"]:
            synthetic = Path(cwd) / ".pre-commit-config.yaml"
            synthetic.write_text(synthetic.read_text().replace("v1.0.0", "v1.1.0"))
            return
        real_run(cmd, cwd=cwd, check=check)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        update_precommit_revs.main()

    assert "https://example.com/repo-a: v1.0.0 -> v1.1.0" in caplog.text
    updated = template_path.read_text()
    assert "    rev: v1.1.0\n" in updated
    assert "    rev: v2.0.0\n" in updated
