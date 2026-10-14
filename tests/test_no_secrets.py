import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_has_no_real_secrets():
    """.env.example is committed to Git - real keys belong only in .env (which Git ignores)."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"sk-ant-[a-z0-9]+-\w{8,}", text), "An Anthropic key was pasted into .env.example - move it to .env"
    for line in text.splitlines():
        if re.match(r"(ANTHROPIC_API_KEY|SMTP_PASSWORD|TELEGRAM_BOT_TOKEN|TWILIO_AUTH_TOKEN|APP_PASSWORD)=", line):
            assert line.split("=", 1)[1].strip() == "", f"{line.split('=')[0]} must stay empty in .env.example"
