from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic
from pydantic import BaseModel

from app.config import settings
from app.models.conversation import Conversation
from app.models.message import Message

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
_BODY_TRUNCATE = 2000

_SYSTEM_PROMPT = """You are an email task extractor. Analyze the given email conversation and extract actionable tasks.

RULES:
- IGNORE (always create an "ignored" task so the user can audit the filter): newsletters, promotions, automated notifications, OTP codes, receipts, CC-only informational emails
- PROCESS: emails requiring action, appointments, important information requiring follow-up

OUTPUT:
- Raw JSON only — no markdown fences, no explanatory text

PRIORITY:
- high: time-sensitive, urgent, or email sent directly To the user requiring prompt response
- medium: 2-3 day horizon, needs attention soon
- low: no deadline, informational action

DEDUPLICATION:
- You will receive EXISTING_TASK_KEYS — reuse these exact keys for tasks that match an existing task
- When reusing an existing task_key (follow-up), bump priority one level higher than what you would otherwise assign

CATEGORIES: reply | appointment | action | info | ignored

OUTPUT FORMAT (raw JSON only, no markdown):
{"tasks": [{"task_key": "reply-john-thursday", "title": "Reply to John about Thursday meeting", "category": "reply", "priority": "high", "summary": "John asked about the Thursday meeting agenda and needs a response.", "due_at": null, "ignore_reason": null}]}

For ignored emails include: {"task_key": "ignore-newsletter-acme", "title": "Newsletter from Acme Corp", "category": "ignored", "priority": "low", "summary": null, "due_at": null, "ignore_reason": "Automated promotional newsletter"}

task_key must be a short hyphenated slug like "reply-john-thursday" or "schedule-dentist-appointment".
due_at must be ISO-8601 string or null."""


class LLMTask(BaseModel):
    task_key: str
    title: str
    category: str   # reply | appointment | action | info | ignored
    priority: str   # high | medium | low
    summary: str | None = None
    due_at: str | None = None     # ISO-8601 string; parsed in task_engine
    ignore_reason: str | None = None


class LLMResponse(BaseModel):
    tasks: list[LLMTask]


def build_prompt(
    conversation: Conversation,
    messages: list[Message],
    existing_task_keys: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"SUBJECT: {conversation.subject or '(no subject)'}")
    lines.append(f"SOURCE: {conversation.source}")
    lines.append(
        f"EXISTING_TASK_KEYS: {', '.join(existing_task_keys) if existing_task_keys else 'none'}"
    )
    lines.append("")

    for msg in messages:
        direction = "USER" if msg.is_from_user else "SENDER"
        sender = msg.sender_handle or msg.sender_name or "unknown"
        sent_at = msg.sent_at.isoformat() if msg.sent_at else "unknown"
        body = (msg.body_text or "").strip()
        if len(body) > _BODY_TRUNCATE:
            body = body[:_BODY_TRUNCATE] + "...[truncated]"
        lines.append(f"[{direction}] From: {sender} | Sent: {sent_at}")
        lines.append(body)
        lines.append("")

    return "\n".join(lines)


def parse_llm_response(raw_text: str) -> LLMResponse:
    """Strip accidental markdown fences, parse JSON, and validate with Pydantic."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw_text).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse LLM response as JSON: {exc}\nRaw: {raw_text!r}"
        ) from exc
    try:
        return LLMResponse.model_validate(data)
    except Exception as exc:
        raise ValueError(
            f"LLM response failed validation: {exc}\nData: {data}"
        ) from exc


def process_conversation(
    conversation: Conversation,
    messages: list[Message],
    existing_task_keys: list[str],
) -> tuple[list[LLMTask], str, dict]:
    """Call the Anthropic API and return (tasks, raw_text, usage)."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = build_prompt(conversation, messages, existing_task_keys)

    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    logger.debug("llm_processor: model=%s usage=%s", _MODEL, usage)

    llm_tasks = parse_llm_response(raw_text).tasks
    return llm_tasks, raw_text, usage
