"""FSM-состояния"""
from aiogram.fsm.state import State, StatesGroup


class StudentRegistration(StatesGroup):
    full_name = State()
    group_number = State()
    password = State()


class StaffManualCode(StatesGroup):
    waiting_code = State()


class AdminStats(StatesGroup):
    choosing_period = State()
    custom_from = State()
    custom_to = State()

class AdminImport(StatesGroup):
    waiting_file = State()