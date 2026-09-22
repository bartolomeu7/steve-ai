"""Time-of-day and first-run aware greetings."""
from __future__ import annotations

from datetime import datetime


def time_of_day_greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        return "Bom dia"
    if 12 <= hour < 18:
        return "Boa tarde"
    return "Boa noite"


class GreetingService:
    def greet(self, user_name: str, is_first_run: bool, now: datetime | None = None) -> str:
        """Called once configuration is already loaded/known — is_first_run here means
        "this session just finished first-run setup", not "the wizard is about to run"
        (ConfigManager.run_first_run_wizard() prints its own welcome for that moment).
        Both branches are time-of-day aware and use the real user_name, same as the
        returning-user greeting — a first-run welcome shouldn't feel like a different,
        colder mechanism than every greeting after it."""
        greeting = time_of_day_greeting(now)
        if is_first_run:
            return f"{greeting}, {user_name}. É um prazer conhecê-lo."
        return f"{greeting}, {user_name}. Bem-vindo de volta."
