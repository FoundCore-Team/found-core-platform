from pydantic import BaseModel


class OpenedContactSchema(BaseModel):
    # `last_name` of the owner is deliberately absent: the full name is seen in Telegram after the chat is opened.
    username: str
    is_new_open: bool
    remaining_today: int
