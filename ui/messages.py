import reflex as rx

from ui.state import Message


def _divider() -> rx.Component:
    return rx.box(height="1px", width="100%", background=rx.color("gray", 4))


def user_message(msg: Message) -> rx.Component:
    return rx.hstack(
        rx.spacer(),
        rx.box(
            rx.text(msg.content, size="3", line_height="1.7", weight="medium"),
            background=rx.color("accent", 9),
            color="white",
            padding="14px 20px",
            border_radius="18px 18px 4px 18px",
            max_width="60%",
        ),
        width="100%",
    )


def assistant_message(msg: Message) -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon("book-open-text", size=16, color=rx.color("accent", 9)),
                rx.text(
                    "LLM Wiki",
                    size="2",
                    weight="bold",
                    color=rx.color("gray", 10),
                    letter_spacing="0.04em",
                ),
                spacing="2",
                align="center",
            ),
            _divider(),
            rx.box(rx.markdown(msg.content), width="100%"),
            rx.cond(
                msg.citations,
                rx.hstack(
                    rx.icon("link-2", size=12, color=rx.color("gray", 8)),
                    rx.foreach(
                        msg.citations,
                        lambda c: rx.badge(c, variant="soft", color_scheme="gray"),
                    ),
                    spacing="2",
                    wrap="wrap",
                    align="center",
                ),
            ),
            rx.cond(
                msg.has_gap,
                rx.callout.root(
                    rx.callout.icon(rx.icon("info", size=14)),
                    rx.callout.text("The wiki doesn't fully cover this topic.", size="2"),
                    color="amber",
                    size="1",
                ),
            ),
            spacing="4",
            align="start",
            width="100%",
        ),
        border=f"1px solid {rx.color('gray', 5)}",
        border_top=f"2px solid {rx.color('accent', 9)}",
        border_radius="2px 12px 12px 12px",
        padding="28px 32px",
        background=rx.color("gray", 1),
        width="100%",
        box_shadow="0 1px 4px rgba(0,0,0,0.04)",
    )


def message_row(msg: Message) -> rx.Component:
    return rx.cond(
        msg.role == "user",
        user_message(msg),
        assistant_message(msg),
    )
