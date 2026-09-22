from __future__ import annotations

from datetime import datetime

from core.greeting import GreetingService, time_of_day_greeting


def test_time_of_day_greeting_morning():
    assert time_of_day_greeting(datetime(2026, 1, 1, 8, 0)) == "Bom dia"


def test_time_of_day_greeting_afternoon():
    assert time_of_day_greeting(datetime(2026, 1, 1, 14, 0)) == "Boa tarde"


def test_time_of_day_greeting_night():
    assert time_of_day_greeting(datetime(2026, 1, 1, 21, 0)) == "Boa noite"


def test_greeting_service_first_run():
    """The first-run greeting is shown AFTER setup just finished, not before it (the
    wizard prints its own separate welcome at that point) — it must be time-aware and
    use the real name, same as every greeting after it, not a generic wizard-intro
    message repeated out of context."""
    message = GreetingService().greet("Junior", is_first_run=True, now=datetime(2026, 1, 1, 9, 0))
    assert message == "Bom dia, Junior. É um prazer conhecê-lo."


def test_greeting_service_returning_user():
    message = GreetingService().greet("Junior", is_first_run=False, now=datetime(2026, 1, 1, 9, 0))
    assert message == "Bom dia, Junior. Bem-vindo de volta."


def test_greeting_service_first_run_and_returning_user_differ():
    now = datetime(2026, 1, 1, 9, 0)
    first_run = GreetingService().greet("Junior", is_first_run=True, now=now)
    returning = GreetingService().greet("Junior", is_first_run=False, now=now)
    assert first_run != returning
