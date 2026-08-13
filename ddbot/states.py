from aiogram.fsm.state import State, StatesGroup


class DraftFlow(StatesGroup):
    content = State()
    button_text = State()
    button_url = State()
    target = State()
    preview = State()
    modify_content = State()
    modify_button_text = State()
    modify_button_url = State()


class ManageFlow(StatesGroup):
    text = State()
    photo = State()
    button_text = State()
    button_url = State()
