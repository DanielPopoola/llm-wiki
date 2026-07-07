import os
from pathlib import Path

import reflex as rx
from dotenv import load_dotenv

load_dotenv()


def _default_hot_reload_excludes() -> None:
    """Prevent Reflex dev reloads when the app writes wiki content."""
    existing = os.environ.get("REFLEX_HOT_RELOAD_EXCLUDE_PATHS")
    wikis_dir = Path(os.environ.get("WIKIS_DIR", "./wikis")).resolve()
    paths = [*existing.split(os.pathsep)] if existing else []
    if wikis_dir.exists() and str(wikis_dir) not in paths:
        paths.append(str(wikis_dir))
    os.environ["REFLEX_HOT_RELOAD_EXCLUDE_PATHS"] = os.pathsep.join(paths)


_default_hot_reload_excludes()

config = rx.Config(
    app_name="ui",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(),
    ],
)
