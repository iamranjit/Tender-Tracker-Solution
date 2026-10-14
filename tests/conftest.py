import pytest


@pytest.fixture(autouse=True)
def no_real_anthropic_credentials(monkeypatch):
    """Tests must never reach the real Anthropic API, even when the developer's .env holds a key."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_WORKSPACE_ID"):
        monkeypatch.setenv(name, "")  # set (empty) so load_dotenv(override=False) can't fill it from .env
    monkeypatch.setattr("tender_tracker.ai.credentials_present", lambda: False)
