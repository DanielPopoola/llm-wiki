import reflex as rx

from ui.constants import CONTENT_WIDTH
from ui.state import State


def _project_picker() -> rx.Component:
    return rx.select.root(
        rx.select.trigger(placeholder="Select a wiki...", variant="ghost", size="1"),
        rx.select.content(
            rx.foreach(
                State.projects,
                lambda name: rx.select.item(name, value=name),
            ),
        ),
        value=State.selected_project,
        on_change=State.select_project,
    )


def _ingest_popover() -> rx.Component:
    return rx.popover.root(
        rx.popover.trigger(
            rx.tooltip(
                rx.icon_button(
                    rx.icon("upload", size=14),
                    variant="ghost",
                    size="2",
                    color_scheme="gray",
                ),
                content="Add a source",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                rx.text("Add a source", size="2", weight="bold", color=rx.color("gray", 12)),
                rx.upload.root(
                    rx.vstack(
                        rx.icon("file-up", size=18, color=rx.color("gray", 9)),
                        rx.text(".md or .txt — drop or click", size="2", color=rx.color("gray", 9)),
                        spacing="2",
                        align="center",
                    ),
                    id="source_upload",
                    accept={"text/markdown": [".md"], "text/plain": [".txt"]},
                    max_files=1,
                    border=f"1px dashed {rx.color('gray', 6)}",
                    border_radius="10px",
                    padding="24px",
                    width="260px",
                    on_drop=State.handle_upload(rx.upload_files(upload_id="source_upload")),
                ),
                rx.cond(
                    State.ingest_status != "",
                    rx.hstack(
                        rx.cond(State.is_ingesting, rx.spinner(size="1")),
                        rx.text(State.ingest_status, size="1", color=rx.color("gray", 10)),
                        spacing="2",
                        align="center",
                    ),
                ),
                spacing="3",
                align="start",
            ),
            size="2",
        ),
    )


def _lint_popover() -> rx.Component:
    return rx.popover.root(
        rx.popover.trigger(
            rx.tooltip(
                rx.icon_button(
                    rx.icon("shield-check", size=14),
                    variant="ghost",
                    size="2",
                    color_scheme="gray",
                ),
                content="Health-check this wiki",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                rx.text("Wiki health", size="2", weight="bold", color=rx.color("gray", 12)),
                rx.button(
                    rx.cond(State.is_linting, rx.spinner(size="2"), rx.icon("play", size=14)),
                    rx.cond(State.is_linting, "Checking...", "Run lint"),
                    on_click=State.run_lint,
                    disabled=State.is_linting,
                    variant="soft",
                    size="2",
                ),
                rx.cond(
                    State.lint_summary != "",
                    rx.text(State.lint_summary, size="1", color=rx.color("gray", 10)),
                ),
                spacing="3",
                align="start",
                width="220px",
            ),
            size="2",
        ),
    )


def header() -> rx.Component:
    return rx.box(
        rx.hstack(
            # Brand
            rx.hstack(
                rx.icon("book-open-text", size=22, color=rx.color("accent", 9)),
                rx.vstack(
                    rx.heading("LLM Wiki", size="4", weight="bold"),
                    rx.text(
                        "Personal Knowledge Base",
                        size="1",
                        color=rx.color("gray", 9),
                        weight="medium",
                        letter_spacing="0.04em",
                    ),
                    spacing="1",
                    align="start",
                ),
                spacing="3",
                align="center",
                on_click=State.clear_chat,
                cursor="pointer",
            ),
            rx.spacer(),
            # Controls
            rx.hstack(
                _project_picker(),
                rx.separator(orientation="vertical", size="1"),
                _ingest_popover(),
                _lint_popover(),
                rx.cond(
                    State.messages,
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("eraser", size=14),
                            variant="ghost",
                            size="2",
                            color_scheme="gray",
                            on_click=State.clear_chat,
                        ),
                        content="Clear chat",
                    ),
                ),
                rx.separator(orientation="vertical", size="1"),
                rx.color_mode.button(variant="ghost", size="2"),
                spacing="3",
                align="center",
            ),
            width="100%",
            max_width=CONTENT_WIDTH,
            margin="0 auto",
            padding_x="8",
            align="center",
        ),
        width="100%",
        padding="16px 0",
        border_bottom=f"1px solid {rx.color('gray', 4)}",
        background=rx.color("gray", 1),
    )
