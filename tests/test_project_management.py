"""
Tests for WIKI-008 — project management.

Covers:
  - list_projects / get_project / select_project in storage.py
  - project_config read/write
  - project isolation: queries filter by project column
"""

import json
from unittest.mock import MagicMock

import pytest

from wiki.project_config import (
    clear_selected_project,
    get_selected_project,
    set_selected_project,
)
from wiki.storage import (
    get_project,
    list_projects,
    select_project,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(fetchone_return=None, fetchall_return=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_return
    cursor.fetchall.return_value = fetchall_return or []
    db = MagicMock()
    db.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    db.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return db, cursor


def _project_row(name="ngx", path="/wikis/ngx", page_count=10, source_count=3):
    return (name, path, "2024-11-15 09:00:00", "2024-11-20 10:00:00", page_count, source_count)


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


def test_get_project_returns_project_info():
    db, _ = _make_db(fetchone_return=_project_row())

    result = get_project(db, "ngx")

    assert result is not None
    assert result.name == "ngx"
    assert result.page_count == 10
    assert result.source_count == 3


def test_get_project_returns_none_when_not_found():
    db, _ = _make_db(fetchone_return=None)

    result = get_project(db, "nonexistent")

    assert result is None


def test_get_project_filters_by_name():
    db, cursor = _make_db(fetchone_return=_project_row())

    get_project(db, "ngx")

    sql = cursor.execute.call_args.args[0]
    assert "name" in sql.lower()


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


def test_list_projects_returns_all():
    rows = [_project_row("ngx"), _project_row("books", path="/wikis/books")]
    db, _ = _make_db(fetchall_return=rows)

    results = list_projects(db)

    assert len(results) == 2
    assert results[0].name == "ngx"
    assert results[1].name == "books"


def test_list_projects_returns_empty_when_none():
    db, _ = _make_db(fetchall_return=[])

    results = list_projects(db)

    assert results == []


def test_list_projects_handles_null_last_ingested():
    """last_ingested can be NULL for a newly created project."""
    row = ("ngx", "/wikis/ngx", "2024-11-15", None, 0, 0)
    db, _ = _make_db(fetchall_return=[row])

    results = list_projects(db)

    assert results[0].last_ingested is None


# ---------------------------------------------------------------------------
# select_project
# ---------------------------------------------------------------------------


def test_select_project_returns_project_when_found():
    db, _ = _make_db(fetchone_return=_project_row())

    result = select_project(db, "ngx")

    assert result.name == "ngx"


def test_select_project_raises_when_not_found():
    db, _ = _make_db(fetchone_return=None)

    with pytest.raises(ValueError, match="ngx"):
        select_project(db, "ngx")


# ---------------------------------------------------------------------------
# project isolation — queries filter by project
# ---------------------------------------------------------------------------


def test_get_project_sql_includes_project_filter():
    db, cursor = _make_db(fetchone_return=None)

    get_project(db, "ngx")

    sql = cursor.execute.call_args.args[0]
    assert "WHERE" in sql.upper()
    assert "name" in sql.lower()


# ---------------------------------------------------------------------------
# project_config — read/write local selection
# ---------------------------------------------------------------------------


def test_set_and_get_selected_project(tmp_path, monkeypatch):
    """set_selected_project writes, get_selected_project reads."""
    config_dir = tmp_path / ".config" / "llm-wiki"
    monkeypatch.setattr("wiki.project_config._CONFIG_DIR", config_dir)
    monkeypatch.setattr("wiki.project_config._CONFIG_FILE", config_dir / "config.json")

    set_selected_project("ngx")
    result = get_selected_project()

    assert result == "ngx"


def test_set_selected_project_overwrites_previous(tmp_path, monkeypatch):
    config_dir = tmp_path / ".config" / "llm-wiki"
    monkeypatch.setattr("wiki.project_config._CONFIG_DIR", config_dir)
    monkeypatch.setattr("wiki.project_config._CONFIG_FILE", config_dir / "config.json")

    set_selected_project("ngx")
    set_selected_project("books")

    assert get_selected_project() == "books"


def test_get_selected_project_fails_fast_when_none_set(tmp_path, monkeypatch):
    """No project selected → clear error, not a silent None."""
    config_dir = tmp_path / ".config" / "llm-wiki"
    monkeypatch.setattr("wiki.project_config._CONFIG_DIR", config_dir)
    monkeypatch.setattr("wiki.project_config._CONFIG_FILE", config_dir / "config.json")

    import typer

    with pytest.raises(typer.BadParameter, match="No project selected"):
        get_selected_project()


def test_clear_selected_project(tmp_path, monkeypatch):
    config_dir = tmp_path / ".config" / "llm-wiki"
    monkeypatch.setattr("wiki.project_config._CONFIG_DIR", config_dir)
    monkeypatch.setattr("wiki.project_config._CONFIG_FILE", config_dir / "config.json")

    set_selected_project("ngx")
    clear_selected_project()

    import typer

    with pytest.raises(typer.BadParameter):
        get_selected_project()


def test_set_selected_project_preserves_other_config_keys(tmp_path, monkeypatch):
    """Writing a new selection doesn't wipe unrelated config keys."""
    config_dir = tmp_path / ".config" / "llm-wiki"
    config_file = config_dir / "config.json"
    monkeypatch.setattr("wiki.project_config._CONFIG_DIR", config_dir)
    monkeypatch.setattr("wiki.project_config._CONFIG_FILE", config_file)

    config_dir.mkdir(parents=True)
    config_file.write_text(json.dumps({"other_key": "other_value"}))

    set_selected_project("ngx")

    data = json.loads(config_file.read_text())
    assert data["other_key"] == "other_value"
    assert data["selected_project"] == "ngx"
