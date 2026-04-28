"""Minimal JSON-mode LLM helper for website VC layer."""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
_TELEMETRY: list[dict[str, Any]] = []


def _cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    inp = float(os.getenv("OPENAI_PRICE_INPUT_PER_1M", "5.0"))
    out = float(os.getenv("OPENAI_PRICE_OUTPUT_PER_1M", "15.0"))
    return round((prompt_tokens * inp + completion_tokens * out) / 1_000_000, 6)


def reset_vc_llm_telemetry() -> None:
    _TELEMETRY.clear()


def get_vc_llm_telemetry() -> list[dict[str, Any]]:
    return list(_TELEMETRY)


def json_llm(
    client: OpenAI,
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    response_model: Type[T],
    temperature: float = 0.2,
    telemetry_label: str | None = None,
) -> T:
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    usage = getattr(resp, "usage", None)
    pt = int(getattr(usage, "prompt_tokens", 0) or 0)
    ct = int(getattr(usage, "completion_tokens", 0) or 0)
    _TELEMETRY.append(
        {
            "name": telemetry_label or response_model.__name__,
            "input_tokens": pt,
            "output_tokens": ct,
            "total_tokens": pt + ct,
            "cost_usd": _cost_usd(pt, ct),
        }
    )
    return response_model.model_validate(data)


def json_llm_optional(
    client: OpenAI,
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    response_model: Type[T],
    temperature: float = 0.2,
    telemetry_label: str | None = None,
) -> Optional[T]:
    try:
        return json_llm(
            client,
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            response_model=response_model,
            temperature=temperature,
            telemetry_label=telemetry_label,
        )
    except Exception:
        return None
