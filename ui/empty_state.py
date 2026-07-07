import reflex as rx

from ui.state import State


def _prompt_card(icon: str, label: str, prompt: str) -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon(icon, size=14, color=rx.color("accent", 9)),
                rx.text(
                    label,
                    size="1",
                    weight="bold",
                    color=rx.color("accent", 9),
                    letter_spacing="0.08em",
                ),
                spacing="2",
                align="center",
            ),
            rx.text(
                prompt,
                size="3",
                color=rx.color("gray", 11),
                line_height="1.6",
            ),
            spacing="3",
            align="start",
        ),
        padding="28px 32px",
        border_radius="12px",
        border=f"1px solid {rx.color('gray', 5)}",
        background=rx.color("gray", 1),
        cursor="pointer",
        transition="border-color 0.15s ease, background 0.15s ease",
        _hover={
            "border_color": rx.color("accent", 7),
            "background": rx.color("accent", 2),
        },
        on_click=State.set_question(prompt),
        flex="1",
        min_width="220px",
        max_width="300px",
    )


def empty_state() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            rx.heading(
                "Ask your wiki anything",
                size="7",
                weight="bold",
                text_align="center",
                color=rx.color("gray", 12),
            ),
            rx.text(
                "Answers are synthesised from the pages you've built, with citations.",
                size="4",
                color=rx.color("gray", 10),
                text_align="center",
            ),
            spacing="3",
            align="center",
            max_width="480px",
        ),
        rx.flex(
            _prompt_card("network", "OVERVIEW", "What are the key entities and how do they connect?"),
            _prompt_card("git-compare", "COMPARE", "What's the difference between the top two topics here?"),
            _prompt_card("triangle-alert", "GAPS", "What contradictions or gaps has this wiki flagged?"),
            spacing="4",
            flex_wrap="wrap",
            justify="center",
            width="100%",
        ),
        spacing="9",
        align="center",
        justify="center",
        flex="1",
        width="100%",
        padding_y="9",
    )
