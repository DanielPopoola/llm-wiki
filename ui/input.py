import reflex as rx

from ui.constants import CONTENT_WIDTH
from ui.state import State


def error_banner() -> rx.Component:
    return rx.cond(
        State.error != "",
        rx.callout.root(
            rx.callout.icon(rx.icon("triangle-alert", size=15)),
            rx.callout.text(State.error, size="2"),
            color="red",
            width="100%",
        ),
        rx.fragment(),
    )


def input_area() -> rx.Component:
    return rx.box(
        rx.vstack(
            error_banner(),
            rx.hstack(
                rx.input(
                    placeholder="Ask a question about your wiki...",
                    value=State.question,
                    on_change=State.set_question,
                    on_key_down=State.handle_key_down,
                    variant="soft",
                    size="3",
                    flex="1",
                    disabled=State.is_loading,
                    auto_focus=True,
                    border_radius="12px",
                ),
                rx.button(
                    rx.cond(
                        State.is_loading,
                        rx.spinner(size="2"),
                        rx.icon("arrow-up", size=16),
                    ),
                    on_click=State.ask,
                    disabled=State.is_loading,
                    size="3",
                    border_radius="10px",
                    width="42px",
                    height="42px",
                    padding="0",
                ),
                width="100%",
                spacing="2",
                align="center",
            ),
            width="100%",
            max_width=CONTENT_WIDTH,
            margin="0 auto",
            padding_x="8",
            spacing="3",
        ),
        width="100%",
        padding="16px 0",
        border_top=f"1px solid {rx.color('gray', 4)}",
        background=rx.color("gray", 1),
    )
