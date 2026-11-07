import pytest

import bot


@pytest.fixture(autouse=True)
def _no_real_last_run(monkeypatch):
    """Тести не читають позначку прогону зі справжнього state.json."""
    monkeypatch.setattr(bot, "load_last_run", lambda: 0.0)
