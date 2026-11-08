import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest


@pytest.fixture(autouse=True)
def isolate_real_provider_keys(monkeypatch):
    # Unit tests must never consume a developer's real paid API credential.
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
