"""Test the `bootstrap_updates` module."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import bootstrap_updates
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable


def _git(cwd: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return bootstrap_updates.run_cmd(["git", *args], cwd=cwd, capture_output=capture)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _commit_all(path: Path, message: str) -> None:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)


def _build_workspace(tmp_path: Path) -> Path:
    original = tmp_path / "original"
    _init_repo(original)
    (original / "foo.txt").write_text("line1\nline2\nline3\n")
    _commit_all(original, "init")
    return original


def _make_fake_run(
    original_dir: Path, *, conflict: bool = False, clean_status: bool = False
) -> Callable[..., subprocess.CompletedProcess[str]]:
    real_run = subprocess.run

    def fake_run(
        cmd: list[str], *, cwd: Path, check: bool, text: bool, capture_output: bool
    ) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[0] == "copier":
            target = Path(cwd) / "foo.txt"
            target.write_text(target.read_text().replace("line1", "line1-updated"))
            if conflict:
                foo = original_dir / "foo.txt"
                foo.write_text(foo.read_text().replace("line1", "line1-locally-changed"))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if clean_status and cmd == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, cwd=cwd, check=check, text=text, capture_output=capture_output)

    return fake_run


def test_sync_modified_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _init_repo(src)
    (src / "a.txt").write_text("old")
    _commit_all(src, "init")
    (src / "a.txt").write_text("new")

    dst.mkdir()
    (dst / "a.txt").write_text("old")

    bootstrap_updates.sync_uncommitted_changes(src, dst)

    assert (dst / "a.txt").read_text() == "new"


def test_sync_deleted_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _init_repo(src)
    (src / "b.txt").write_text("content")
    _commit_all(src, "init")
    (src / "b.txt").unlink()

    dst.mkdir()
    (dst / "b.txt").write_text("content")

    bootstrap_updates.sync_uncommitted_changes(src, dst)

    assert not (dst / "b.txt").exists()


def test_sync_deleted_file_dst_is_directory(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _init_repo(src)
    (src / "c").mkdir()
    (src / "c" / "file.txt").write_text("content")
    _commit_all(src, "init")
    (src / "c" / "file.txt").unlink()

    dst_path = dst / "c" / "file.txt"
    dst_path.mkdir(parents=True)
    (dst_path / "nested.txt").write_text("x")

    bootstrap_updates.sync_uncommitted_changes(src, dst)

    assert not dst_path.exists()


def test_sync_untracked_new_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _init_repo(src)
    (src / "README.md").write_text("readme")
    _commit_all(src, "init")

    (src / "newdir").mkdir()
    (src / "newdir" / "new.txt").write_text("hello")

    dst.mkdir()

    bootstrap_updates.sync_uncommitted_changes(src, dst)

    assert (dst / "newdir" / "new.txt").read_text() == "hello"


def test_sync_new_file_dst_is_directory(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _init_repo(src)
    (src / "README.md").write_text("readme")
    _commit_all(src, "init")

    (src / "d.txt").write_text("hi")

    dst.mkdir()
    dst_path = dst / "d.txt"
    dst_path.mkdir()
    (dst_path / "nested.txt").write_text("x")

    bootstrap_updates.sync_uncommitted_changes(src, dst)

    assert dst_path.is_file()
    assert dst_path.read_text() == "hi"


def test_set_copier_answers_commit_creates_file(tmp_path: Path) -> None:
    bootstrap_updates.set_copier_answers_commit(tmp_path, "abc123")
    assert (tmp_path / ".copier-answers.yml").read_text() == "_commit: abc123\n_src_path: .\n"


def test_set_copier_answers_commit_replaces_existing(tmp_path: Path) -> None:
    answers = tmp_path / ".copier-answers.yml"
    answers.write_text("_commit: old\nfoo: bar\n")

    bootstrap_updates.set_copier_answers_commit(tmp_path, "new123")

    content = answers.read_text()
    assert "_commit: new123\n" in content
    assert "foo: bar" in content


def test_set_copier_answers_commit_prepends_missing(tmp_path: Path) -> None:
    answers = tmp_path / ".copier-answers.yml"
    answers.write_text("_src_path: .\n")

    bootstrap_updates.set_copier_answers_commit(tmp_path, "new123")

    assert answers.read_text() == "_commit: new123\n_src_path: .\n"


def test_parse_overwrite_flag() -> None:
    assert bootstrap_updates.parse_overwrite(["--overwrite"]) is True
    assert bootstrap_updates.parse_overwrite([]) is False


def test_main_requires_git_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        bootstrap_updates.main([])

    assert exc_info.value.code == 1
    assert "must be a Git repository" in caplog.text


def test_main_no_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)

    real_run = subprocess.run

    def fake_run(
        cmd: list[str], *, cwd: Path, check: bool, text: bool, capture_output: bool
    ) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[0] == "copier":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, cwd=cwd, check=check, text=text, capture_output=capture_output)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        bootstrap_updates.main([])

    assert "No template changes to apply." in caplog.text


def test_main_declines_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)
    monkeypatch.setattr(subprocess, "run", _make_fake_run(original))
    monkeypatch.setattr("builtins.input", lambda _: "n")

    bootstrap_updates.main([])

    assert (original / "foo.txt").read_text() == "line1\nline2\nline3\n"
    assert "line1-updated" in capsys.readouterr().out


def test_main_applies_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)
    monkeypatch.setattr(subprocess, "run", _make_fake_run(original))
    monkeypatch.setattr("builtins.input", lambda _: "y")

    with caplog.at_level(logging.INFO):
        bootstrap_updates.main([])

    assert "Update applied cleanly." in caplog.text
    assert (original / "foo.txt").read_text() == "line1-updated\nline2\nline3\n"


def test_main_overwrite_flag_skips_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)
    monkeypatch.setattr(subprocess, "run", _make_fake_run(original))

    def _unexpected_input(_: str) -> str:
        raise AssertionError("input() should not be called with --overwrite")  # pragma: no cover

    monkeypatch.setattr("builtins.input", _unexpected_input)

    with caplog.at_level(logging.INFO):
        bootstrap_updates.main(["--overwrite"])

    assert "Update applied cleanly." in caplog.text
    assert (original / "foo.txt").read_text() == "line1-updated\nline2\nline3\n"


def test_main_apply_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)
    monkeypatch.setattr(subprocess, "run", _make_fake_run(original, conflict=True))
    monkeypatch.setattr("builtins.input", lambda _: "y")

    with caplog.at_level(logging.WARNING):
        bootstrap_updates.main([])

    assert "Update applied with conflict markers." in caplog.text


def test_main_status_clean_skips_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)
    fake_run = _make_fake_run(original, clean_status=True)

    calls: list[list[str]] = []
    real_fake = fake_run

    def tracking_run(
        cmd: list[str], *, cwd: Path, check: bool, text: bool, capture_output: bool
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return real_fake(cmd, cwd=cwd, check=check, text=text, capture_output=capture_output)

    monkeypatch.setattr(subprocess, "run", tracking_run)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    bootstrap_updates.main([])

    assert ["git", "add", "-A"] not in calls


def test_main_aborts_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = _build_workspace(tmp_path)
    monkeypatch.chdir(original)
    monkeypatch.setattr(subprocess, "run", _make_fake_run(original))

    def _raise_interrupt(_: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise_interrupt)

    with caplog.at_level(logging.ERROR):
        result = bootstrap_updates.main([])

    assert result == 1
    assert "Aborted." in caplog.text
