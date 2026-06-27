"""
LLM Wiki CLI.

Thin entrypoint that calls wiki functions and prints results.
No business logic lives here.

Usage:
    llm-wiki new <name>
    llm-wiki list
    llm-wiki inspect <name>
"""

from pathlib import Path

import typer

from config import settings
from wiki.schema import create_wiki, inspect_wiki, list_wikis

app = typer.Typer(help="LLM Wiki — personal knowledge base agent.")


def _wikis_dir() -> Path:
    return Path(settings.wikis_dir)


@app.command()
def new(name: str = typer.Argument(..., help="Wiki project name.")):
    """Scaffold a new wiki project."""
    try:
        wiki = create_wiki(name, wikis_dir=_wikis_dir())
        typer.echo(f"✅ Created wiki '{wiki.name}' at {wiki.path}")
    except FileExistsError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command(name="list")
def list_command():
    """List all wiki projects."""
    wikis = list_wikis(_wikis_dir())

    if not wikis:
        typer.echo("No wikis found.")
        return

    for wiki in wikis:
        total = sum(wiki.page_counts.values())
        typer.echo(f"  {wiki.name}  ({total} pages)")


@app.command()
def inspect(name: str = typer.Argument(..., help="Wiki project name.")):
    """Show structure and page counts for a wiki."""
    try:
        wiki = inspect_wiki(name, wikis_dir=_wikis_dir())
    except FileNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"\nWiki: {wiki.name}")
    typer.echo(f"Path: {wiki.path}\n")

    typer.echo("Pages:")
    for directory, count in wiki.page_counts.items():
        typer.echo(f"  {directory}/  —  {count} pages")

    schema_excerpt = (wiki.path / "SCHEMA.md").read_text()[:300]
    typer.echo(f"\nSchema (first 300 chars):\n{schema_excerpt}")


if __name__ == "__main__":
    app()
