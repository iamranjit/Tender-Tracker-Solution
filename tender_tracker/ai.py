"""Claude-powered drafting for proposal workspaces.

Every call returns schema-validated JSON (structured outputs), streams so long
tenders and long drafts don't hit HTTP timeouts, and puts the tender text in a
cached prefix so the three drafting steps read it once at full price.
Server-side ``fallbacks: "default"`` re-runs a request on Anthropic's
recommended model if it is declined by a safety classifier.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AIConfig

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIError(RuntimeError):
    """The AI step could not produce a usable result."""


def credentials_present() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()  # `ant auth login` profile


def ai_status(cfg: AIConfig) -> dict[str, Any]:
    try:
        import anthropic  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    ready = cfg.enabled and installed and credentials_present()
    if not cfg.enabled:
        reason = "AI drafting is turned off (ai.enabled in config.yaml)."
    elif not installed:
        reason = "The 'anthropic' package isn't installed (pip install -r requirements.txt)."
    elif not credentials_present():
        reason = "Add ANTHROPIC_API_KEY to .env and restart the app."
    else:
        reason = ""
    return {"ready": ready, "model": cfg.model, "reason": reason}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    models: list[str] = field(default_factory=list)

    def add(self, usage: Any, model: str) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        if model and model not in self.models:
            self.models.append(model)


class Drafter:
    """Thin wrapper around the Anthropic SDK for schema-constrained drafting calls."""

    def __init__(self, cfg: AIConfig, client: Any = None) -> None:
        self.cfg = cfg
        if client is None:
            import anthropic

            # Keys that aren't scoped to a workspace must name one on every request.
            workspace = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
            client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)
        self.client = client
        self.usage = Usage()

    def structured(
        self,
        *,
        system: str,
        context: list[str],
        instruction: str,
        schema: dict[str, Any],
        max_tokens: int = 64000,
    ) -> dict[str, Any]:
        """One drafting call. ``context`` blocks form a stable, cached prefix shared by all calls."""
        content: list[dict[str, Any]] = [{"type": "text", "text": block} for block in context]
        if content:
            content[-1]["cache_control"] = {"type": "ephemeral"}
        content.append({"type": "text", "text": instruction})

        import anthropic

        try:
            with self.client.beta.messages.stream(
                model=self.cfg.model,
                max_tokens=max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                output_config={"effort": self.cfg.effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": content}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise AIError("The Anthropic API key was rejected - check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(f"The API key doesn't have access to {self.cfg.model}.") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Anthropic API rate limit reached - try again in a minute.") from exc
        except anthropic.BadRequestError as exc:
            raise AIError(f"The request was rejected: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Anthropic API error ({exc.status_code}) - try again later.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Couldn't reach the Anthropic API - check the internet connection.") from exc

        self.usage.add(message.usage, getattr(message, "model", self.cfg.model))
        if message.stop_reason == "refusal":
            raise AIError("The model declined this request.")
        if message.stop_reason == "max_tokens":
            raise AIError("The draft was cut off (output limit reached).")
        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError("The model returned malformed output.") from exc
