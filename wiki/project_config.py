"""
Local project selection config.

Persists the currently selected wiki project between CLI commands.
Stored at ~/.config/llm-wiki/config.json — one file, one responsibility.

Usage:
    from wiki.project_config import get_selected_project, set_selected_project

    set_selected_project("my-stocks-wiki")
    name = get_selected_project()   # "my-stocks-wiki"
"""

import json
from pathlib import Path

_CONFIG_DIR = Path.home() / ".config" / "llm-wiki"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


def get_selected_project() -> str:
    if not _CONFIG_FILE.exists():
        _fail_no_project()

    data = json.loads(_CONFIG_FILE.read_text())
    name = data.get("selected_project")

    if not name:
        _fail_no_project()

    return name


def set_selected_project(name: str) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    existing = {}
    if _CONFIG_FILE.exists():
        existing = json.loads(_CONFIG_FILE.read_text())

    existing["selected_project"] = name
    _CONFIG_FILE.write_text(json.dumps(existing, indent=2))


def clear_selected_project() -> None:
    if not _CONFIG_FILE.exists():
        return

    data = json.loads(_CONFIG_FILE.read_text())
    data.pop("selected_project", None)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))


def _fail_no_project() -> None:
    import typer

    raise typer.BadParameter(
        "No project selected.\nRun: llm-wiki select <project-name>\nOr create a new wiki: llm-wiki new <project-name>"
    )
