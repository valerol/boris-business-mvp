from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel

from boris_mvp.boris import BORISConstraintsReport
from boris_mvp.sima import SIMAOutput


class LLMResponse(BaseModel):
    action_type: str
    content: str
    confidence: float
    provider: str = "local_stub"
    raw_response: str = ""


def propose_action(
    prompt: str,
    snapshot: SIMAOutput,
    constraints: BORISConstraintsReport,
    context_package: dict,
) -> LLMResponse:
    if not constraints.action_allowed:
        return LLMResponse(
            action_type="blocked",
            content="No LLM action proposed because BORIS constraints blocked execution.",
            confidence=1.0,
        )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        response = _call_openai(prompt, context_package, api_key)
        if response is not None:
            return response

    files = snapshot.active_context.get("files", [])
    file_preview = ", ".join(files[:8]) if isinstance(files, list) and files else "no project files found"
    lower = prompt.lower()
    if any(word in lower for word in ("explain", "describe", "объясни", "расскажи")):
        action_type = "explain"
    elif any(word in lower for word in ("add", "create", "implement", "добавь", "создай")):
        action_type = "suggest_change"
    elif any(word in lower for word in ("fix", "bug", "error", "ошибка", "почини")):
        action_type = "debug"
    else:
        action_type = "respond"

    return LLMResponse(
        action_type=action_type,
        content=(
            "BORIS Business MVP accepted the task.\n\n"
            f"Prompt: {prompt}\n"
            f"Detected action: {action_type}\n"
            f"Workspace files: {file_preview}\n\n"
            "This MVP returns a validated local response and persists the full cognitive trace."
        ),
        confidence=0.8,
    )


def _call_openai(prompt: str, context_package: dict, api_key: str) -> LLMResponse | None:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a proposal generator inside BORIS Business MVP. "
                    "You must not decide execution flow, bypass BOIS, bypass BORIS, "
                    "or claim to have executed actions. Return a concise task result proposal."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"prompt": prompt, "context_package": context_package},
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None

    content = (
        payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        return None
    return LLMResponse(
        action_type=_detect_action_type(prompt),
        content=content,
        confidence=0.75,
        provider="openai",
        raw_response=json.dumps(payload, ensure_ascii=False),
    )


def _detect_action_type(prompt: str) -> str:
    lower = prompt.lower()
    if any(word in lower for word in ("explain", "describe", "объясни", "расскажи")):
        return "explain"
    if any(word in lower for word in ("add", "create", "implement", "добавь", "создай")):
        return "suggest_change"
    if any(word in lower for word in ("fix", "bug", "error", "ошибка", "почини")):
        return "debug"
    return "respond"


def model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
