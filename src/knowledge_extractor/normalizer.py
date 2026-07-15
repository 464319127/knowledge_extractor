from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from knowledge_extractor.models import (
    NormalizedCorpus,
    NormalizedSession,
    NormalizedTurn,
    SourceMetadata,
    TurnKind,
)


class NormalizationError(ValueError):
    pass


_NOISE_BLOCK_RE = re.compile(
    r"<(system-reminder|ide_opened_file)\b[^>]*>.*?</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)
_CORRECTION_RE = re.compile(
    r"(?:不对|错误|错了|修正|纠正|incorrect|\bwrong\b|not correct)",
    flags=re.IGNORECASE,
)
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s]+")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
_API_KEY_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}")


def _sanitize_text(text: str) -> str:
    cleaned = _NOISE_BLOCK_RE.sub("", text)
    cleaned = _HOME_PATH_RE.sub("~", cleaned)
    cleaned = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", cleaned)
    cleaned = _API_KEY_RE.sub("[REDACTED_API_KEY]", cleaned)
    lines = [line.rstrip() for line in cleaned.strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _text_blocks(content: Any, *, sanitize: bool = False) -> list[str]:
    if isinstance(content, str):
        values = [content]
    elif isinstance(content, list):
        values = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    else:
        values = []

    if sanitize:
        values = [_sanitize_text(value) for value in values]
    else:
        values = [value.strip() for value in values]
    return [value for value in values if value]


def _find_latest_question(messages: Any) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        texts = _text_blocks(message.get("content"), sanitize=True)
        if texts:
            return "\n\n".join(texts)
    return None


def _extract_answer(record: dict[str, Any]) -> tuple[str | None, str | None]:
    response_data = record.get("response", {}).get("response_data", {})
    if not isinstance(response_data, dict):
        return None, None
    if response_data.get("stop_reason") != "end_turn":
        return None, None
    texts = _text_blocks(response_data.get("content"), sanitize=True)
    answer = "\n\n".join(texts) if texts else None
    response_id = response_data.get("id")
    return answer, str(response_id) if response_id else None


def _read_bytes(path: Path) -> tuple[bytes, str, int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NormalizationError(f"cannot read input file {path}: {exc}") from exc
    return raw, hashlib.sha256(raw).hexdigest(), len(raw)


def _read_records(raw: bytes) -> list[tuple[int, dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise NormalizationError(
                f"invalid JSON on line {line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise NormalizationError(f"line {line_number} must contain a JSON object")
        records.append((line_number, value))

    if not records:
        raise NormalizationError("input contains no JSON records")
    return records


def _normalize_records(
    records: list[tuple[int, dict[str, Any]]],
    *,
    source_name: str,
    sha256: str,
    byte_count: int,
) -> NormalizedCorpus:
    sessions: OrderedDict[str, list[NormalizedTurn]] = OrderedDict()
    session_models: dict[str, str | None] = {}
    seen_request_ids: set[tuple[str, str]] = set()
    warnings: list[str] = []

    for line_number, record in records:
        answer, response_id = _extract_answer(record)
        if answer is None:
            continue

        session_id = str(record.get("session_id") or "").strip()
        request_id = str(record.get("request_id") or "").strip()
        if not session_id or not request_id:
            warnings.append(
                f"line {line_number}: skipped completed response without "
                "session_id or request_id"
            )
            continue
        request_key = (session_id, request_id)
        if request_key in seen_request_ids:
            warnings.append(
                f"line {line_number}: skipped duplicate request_id {request_id}"
            )
            continue

        request = record.get("request", {})
        messages = request.get("messages") if isinstance(request, dict) else None
        question = _find_latest_question(messages)
        if not question:
            warnings.append(
                f"line {line_number}: skipped completed response without a user question"
            )
            continue

        kind = (
            TurnKind.CORRECTION
            if _CORRECTION_RE.search(question)
            else TurnKind.COMPLETED
        )
        turn = NormalizedTurn(
            request_id=request_id,
            response_id=response_id,
            source_line=line_number,
            question=question,
            answer=answer,
            kind=kind,
        )
        sessions.setdefault(session_id, []).append(turn)
        model = request.get("model") if isinstance(request, dict) else None
        session_models.setdefault(session_id, str(model) if model else None)
        seen_request_ids.add(request_key)

    if not sessions:
        raise NormalizationError(
            "input contains no complete end_turn question/answer pairs"
        )

    normalized_sessions = [
        NormalizedSession(
            session_id=session_id,
            model=session_models.get(session_id),
            turns=turns,
        )
        for session_id, turns in sessions.items()
    ]
    return NormalizedCorpus(
        source=SourceMetadata(
            name=source_name,
            sha256=sha256,
            record_count=len(records),
            byte_count=byte_count,
        ),
        sessions=normalized_sessions,
        warnings=warnings,
    )


def _normalize_messages(
    document: dict[str, Any],
    *,
    source_name: str,
    sha256: str,
    byte_count: int,
) -> NormalizedCorpus:
    messages = document.get("messages")
    if not isinstance(messages, list):
        raise NormalizationError("messages document must contain a list")

    conversation = document.get("conversation")
    conversation_id = document.get("conversation_id")
    if isinstance(conversation, dict):
        conversation_id = conversation.get("id") or conversation_id
    session_id = str(conversation_id or f"conversation-{sha256[:12]}").strip()
    if not session_id:
        raise NormalizationError("messages document has an empty conversation id")

    turns: list[NormalizedTurn] = []
    warnings: list[str] = []
    pending_question: str | None = None
    pending_question_index: int | None = None
    latest_answer: str | None = None
    latest_answer_index: int | None = None

    def finish_pending() -> None:
        nonlocal pending_question, pending_question_index
        nonlocal latest_answer, latest_answer_index
        if pending_question is None:
            return
        if latest_answer is None or latest_answer_index is None:
            warnings.append(
                f"message {pending_question_index}: user question has no assistant answer"
            )
        else:
            request_id = f"message-{latest_answer_index:04d}"
            kind = (
                TurnKind.CORRECTION
                if _CORRECTION_RE.search(pending_question)
                else TurnKind.COMPLETED
            )
            turns.append(
                NormalizedTurn(
                    request_id=request_id,
                    response_id=None,
                    source_line=latest_answer_index,
                    question=pending_question,
                    answer=latest_answer,
                    kind=kind,
                )
            )
        pending_question = None
        pending_question_index = None
        latest_answer = None
        latest_answer_index = None

    for message_index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            warnings.append(f"message {message_index}: skipped non-object message")
            continue
        role = message.get("role")
        texts = _text_blocks(message.get("content"), sanitize=True)
        if role == "user":
            finish_pending()
            if texts:
                pending_question = "\n\n".join(texts)
                pending_question_index = message_index
            else:
                warnings.append(f"message {message_index}: skipped empty user message")
        elif role == "assistant" and pending_question is not None and texts:
            # Exported conversations may contain progress updates; retain only the
            # final assistant text before the next user question.
            latest_answer = "\n\n".join(texts)
            latest_answer_index = message_index

    finish_pending()
    if not turns:
        raise NormalizationError(
            "input contains no complete user/assistant message pairs"
        )

    return NormalizedCorpus(
        source=SourceMetadata(
            name=source_name,
            sha256=sha256,
            record_count=len(messages),
            byte_count=byte_count,
        ),
        sessions=[
            NormalizedSession(
                session_id=session_id,
                model=None,
                turns=turns,
            )
        ],
        warnings=warnings,
    )


def normalize_jsonl(path: str | Path) -> NormalizedCorpus:
    """Normalize cumulative JSONL or export-conversation messages JSON."""
    source_path = Path(path)
    raw, sha256, byte_count = _read_bytes(source_path)
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        document = None
    if isinstance(document, dict) and "messages" in document:
        return _normalize_messages(
            document,
            source_name=source_path.name,
            sha256=sha256,
            byte_count=byte_count,
        )
    records = _read_records(raw)
    return _normalize_records(
        records,
        source_name=source_path.name,
        sha256=sha256,
        byte_count=byte_count,
    )
