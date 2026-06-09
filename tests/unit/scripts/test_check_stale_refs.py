"""Unit tests for ``scripts/check_stale_refs.py``.

The script reads its manifest + scan roots from ``REPO_ROOT``. We
monkeypatch those module-level constants onto a ``tmp_path`` tree
and call ``main()`` directly so the test has no dependency on the
live repo state.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

# Load the script as a module — it lives outside ``src/`` so the
# regular ``src.*`` import path will not resolve it.
_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_stale_refs.py"
_spec = importlib.util.spec_from_file_location("check_stale_refs", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_stale_refs = importlib.util.module_from_spec(_spec)
sys.modules["check_stale_refs"] = check_stale_refs
_spec.loader.exec_module(check_stale_refs)  # type: ignore[arg-type]


@pytest.fixture
def fake_repo(monkeypatch, tmp_path: Path) -> Path:
    """Build a minimal repo tree under ``tmp_path`` and rebind constants."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "core").mkdir(parents=True)

    monkeypatch.setattr(check_stale_refs, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        check_stale_refs, "MANIFEST", tmp_path / "scripts" / "stale_refs.yaml"
    )
    return tmp_path


def _write_manifest(repo: Path, body: str) -> None:
    (repo / "scripts" / "stale_refs.yaml").write_text(body)


def test_clean_tree_returns_zero(fake_repo: Path, capsys) -> None:
    _write_manifest(
        fake_repo,
        "patterns:\n  - pattern: '\\bbanned\\b'\n    replacement: 'use foo'\n",
    )
    (fake_repo / "docs" / "ok.md").write_text("# Title\nNo bad words here.\n")

    assert check_stale_refs.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""


def test_hit_returns_one_and_prints_hint(fake_repo: Path, capsys) -> None:
    _write_manifest(
        fake_repo,
        "patterns:\n  - pattern: '\\bbanned\\b'\n    replacement: 'use foo'\n",
    )
    (fake_repo / "docs" / "bad.md").write_text("This line is banned.\n")

    assert check_stale_refs.main() == 1
    err = capsys.readouterr().err
    assert "docs/bad.md:1:" in err
    assert "use foo" in err
    assert "1 stale reference(s) detected" in err


def test_allow_marker_skips_line(fake_repo: Path) -> None:
    _write_manifest(
        fake_repo,
        "patterns:\n  - pattern: '\\bbanned\\b'\n    replacement: 'use foo'\n",
    )
    (fake_repo / "docs" / "exempt.md").write_text(
        "This line is banned.  # stale-refs: allow\n"
    )

    assert check_stale_refs.main() == 0


def test_alembic_versions_dir_is_excluded(fake_repo: Path) -> None:
    _write_manifest(
        fake_repo,
        "patterns:\n  - pattern: '\\bbanned\\b'\n    replacement: 'use foo'\n",
    )
    versions = fake_repo / "alembic" / "versions"
    versions.mkdir(parents=True)
    (versions / "0001_x.py").write_text("# banned word inside a migration\n")

    # Migrations are an EXCLUDE_SUBSTRINGS match — the hit must NOT
    # surface because Alembic rename migrations are expected to
    # mention the old name.
    assert check_stale_refs.main() == 0


def test_changelog_is_excluded(fake_repo: Path) -> None:
    _write_manifest(
        fake_repo,
        "patterns:\n  - pattern: '\\bbanned\\b'\n    replacement: 'use foo'\n",
    )
    (fake_repo / "CHANGELOG.md").write_text("- renamed `banned` to `foo`\n")

    assert check_stale_refs.main() == 0


def test_missing_manifest_returns_two(fake_repo: Path, capsys) -> None:
    # Do not write the manifest. main() should exit 2 via SystemExit.
    with pytest.raises(SystemExit) as ei:
        check_stale_refs.main()
    assert ei.value.code == 2
    assert "manifest not found" in capsys.readouterr().err


def test_empty_manifest_returns_zero(fake_repo: Path) -> None:
    _write_manifest(fake_repo, "patterns: []\n")
    (fake_repo / "docs" / "anything.md").write_text("banned banned\n")
    assert check_stale_refs.main() == 0


def test_compiled_patterns_are_regex(fake_repo: Path) -> None:
    """The loader returns compiled patterns, not raw strings."""
    _write_manifest(
        fake_repo,
        "patterns:\n  - pattern: '\\bbanned\\b'\n    replacement: 'x'\n",
    )
    patterns = check_stale_refs.load_manifest()
    assert len(patterns) == 1
    compiled, hint = patterns[0]
    assert isinstance(compiled, re.Pattern)
    assert hint == "x"
