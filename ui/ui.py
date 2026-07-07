import reflex as rx

from ui.constants import CONTENT_WIDTH
from ui.empty_state import empty_state
from ui.header import header
from ui.input import input_area
from ui.messages import message_row
from ui.state import State


def index() -> rx.Component:
    return rx.vstack(
        header(),
        rx.cond(
            State.messages,
            rx.vstack(
                rx.foreach(State.messages, message_row),
                spacing="4",
                width="100%",
                max_width=CONTENT_WIDTH,
                margin="0 auto",
                padding="24px 16px",
                flex="1",
                overflow_y="auto",
            ),
            empty_state(),
        ),
        input_area(),
        width="100%",
        height="100vh",
        spacing="0",
    )


app = rx.App()
app.add_page(index, on_load=State.load_projects)
