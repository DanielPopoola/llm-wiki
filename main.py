"""
LLM Wiki CLI.

Thin entrypoint — calls wiki functions and workflows, prints results.
No business logic lives here.

Commands:
    llm-wiki new <name>         — scaffold a new wiki and select it
    llm-wiki select <name>      — select an existing wiki as active
    llm-wiki list               — list all wikis with metadata
    llm-wiki inspect            — show structure of selected wiki
    llm-wiki ingest <source>    — ingest a source document
    llm-wiki query <question>   — ask a question
"""

from pathlib import Path
from typing import Optional

import typer
from langchain_groq import ChatGroq
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from config import settings
from infrastructure.db import DatabaseConnection
from wiki import storage
from wiki.project_config import (
    get_selected_project,
    set_selected_project,
)
from wiki.schema import create_wiki, inspect_wiki, list_wikis
from workflows.ingestion import run_ingestion
from workflows.query import run_query

app = typer.Typer(help="LLM Wiki — personal knowledge base agent.")
console = Console()


# ---------------------------------------------------------------------------
# Shared dependencies
# ---------------------------------------------------------------------------


def _wikis_dir() -> Path:
    return Path(settings.wikis_dir)


def _make_llm() -> ChatGroq:
    return ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
    )


def _make_db() -> DatabaseConnection:
    return DatabaseConnection.from_settings(settings)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def new(name: str = typer.Argument(..., help="Wiki project name.")):
    """Scaffold a new wiki project and select it as active."""
    db = _make_db()
    try:
        wiki = create_wiki(name, wikis_dir=_wikis_dir())
        storage.register_project(db, name=name, wiki_path=wiki.path)
        set_selected_project(name)
        console.print(f"✅ Created and selected wiki [bold]{name}[/bold] at {wiki.path}")
    except FileExistsError as e:
        console.print(f"❌ {e}", style="red")
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def select(name: str = typer.Argument(..., help="Wiki project name to select.")):
    """Select an existing wiki as the active project."""
    db = _make_db()
    try:
        storage.select_project(db, name)  # validates it exists
        set_selected_project(name)
        console.print(f"✅ Selected wiki [bold]{name}[/bold]")
    except ValueError as e:
        console.print(f"❌ {e}", style="red")
        raise typer.Exit(1)
    finally:
        db.close()


@app.command(name="list")
def list_command():
    """List all wiki projects with metadata."""
    db = _make_db()
    try:
        projects = storage.list_projects(db)
    finally:
        db.close()

    # Fall back to disk scan if Oracle has no projects yet
    if not projects:
        wikis = list_wikis(_wikis_dir())
        if not wikis:
            console.print("No wikis found.")
            return
        for wiki in wikis:
            total = sum(wiki.page_counts.values())
            console.print(f"  {wiki.name}  ({total} pages)")
        return

    try:
        selected = get_selected_project()
    except SystemExit:
        selected = None

    table = Table(show_header=True, header_style="bold")
    table.add_column("")  # selected marker
    table.add_column("Name")
    table.add_column("Pages", justify="right")
    table.add_column("Sources", justify="right")
    table.add_column("Last Ingested")

    for p in projects:
        marker = "✓" if p.name == selected else ""
        last = p.last_ingested[:10] if p.last_ingested else "never"
        table.add_row(marker, p.name, str(p.page_count), str(p.source_count), last)

    console.print(table)


@app.command()
def inspect():
    """Show structure and page counts for the selected wiki."""
    name = get_selected_project()
    try:
        wiki = inspect_wiki(name, wikis_dir=_wikis_dir())
    except FileNotFoundError as e:
        console.print(f"❌ {e}", style="red")
        raise typer.Exit(1)

    console.print(f"\nWiki: [bold]{wiki.name}[/bold]")
    console.print(f"Path: {wiki.path}\n")
    console.print("Pages:")
    for directory, count in wiki.page_counts.items():
        console.print(f"  {directory}/  —  {count} pages")

    schema_excerpt = (wiki.path / "SCHEMA.md").read_text()[:300]
    console.print(f"\nSchema (first 300 chars):\n{schema_excerpt}")


@app.command()
def ingest(
    source: str = typer.Argument(..., help="Path to source document."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Override selected project."),
):
    """Ingest a source document into the selected wiki."""
    name = project or get_selected_project()
    source_path = Path(source)

    if not source_path.exists():
        console.print(f"❌ Source file not found: {source_path}", style="red")
        raise typer.Exit(1)

    db = _make_db()
    try:
        proj = storage.select_project(db, name)
        wiki_path = Path(proj.wiki_path)
    except ValueError as e:
        console.print(f"❌ {e}", style="red")
        db.close()
        raise typer.Exit(1)

    console.print(f"⚙️  Ingesting [bold]{source_path.name}[/bold] into [bold]{name}[/bold]...")

    try:
        llm = _make_llm()
        state = run_ingestion(
            wiki_path=wiki_path,
            source_path=source_path,
            project=name,
            llm=llm,
            db=db,
        )
        pages = len(state.pages_written)
        storage.update_project_stats(db, name=name, page_count=pages)
        console.print(f"✅ Done — {pages} pages written.")
    except Exception as e:
        console.print(f"❌ Ingestion failed: {e}", style="red")
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def query(
    question: str = typer.Argument(..., help="Question to ask the wiki."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Override selected project."),
    save: bool = typer.Option(False, "--save", help="Save answer as a wiki page."),
):
    """Ask a question and get an answer from the selected wiki."""
    name = project or get_selected_project()
    db = _make_db()

    try:
        proj = storage.select_project(db, name)
        wiki_path = Path(proj.wiki_path)
    except ValueError as e:
        console.print(f"❌ {e}", style="red")
        db.close()
        raise typer.Exit(1)

    confirm_fn = None
    if save:
        confirm_fn = lambda q: typer.confirm(q)

    try:
        llm = _make_llm()
        state = run_query(
            wiki_path=wiki_path,
            project=name,
            question=question,
            llm=llm,
            db=db,
            confirm_fn=confirm_fn,
        )

        console.print()
        console.print(Markdown(state.answer))

        if state.citations:
            console.print("\n[dim]Citations: " + ", ".join(state.citations) + "[/dim]")

        if state.has_gap:
            console.print("\n[yellow]⚠️  The wiki doesn't fully cover this topic.[/yellow]")

    except Exception as e:
        console.print(f"❌ Query failed: {e}", style="red")
        raise typer.Exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    app()
