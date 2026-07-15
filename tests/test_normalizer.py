from __future__ import annotations

import json

import pytest

from knowledge_extractor.models import TurnKind
from knowledge_extractor.normalizer import NormalizationError, normalize_jsonl


def _write_jsonl(path, records) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _record(
    *,
    request_id: str,
    messages: list[dict],
    stop_reason: str,
    content: list[dict],
) -> dict:
    return {
        "session_id": "session-1",
        "request_id": request_id,
        "request": {"model": "test-model", "messages": messages},
        "response": {
            "response_data": {
                "id": f"response-{request_id}",
                "stop_reason": stop_reason,
                "content": content,
            }
        },
    }


def test_normalize_keeps_only_completed_sanitized_turns(tmp_path):
    initial_user = {
        "role": "user",
        "content": [
            {"type": "text", "text": "<system-reminder>secret setup</system-reminder>"},
            {
                "type": "text",
                "text": (
                    "<ide_opened_file>The user opened "
                    "/Users/alice/private/base.py</ide_opened_file>"
                ),
            },
            {"type": "text", "text": "分析 /Users/alice/project/model.py 的显存占用"},
        ],
    }
    records = [
        _record(
            request_id="request-tool",
            messages=[initial_user],
            stop_reason="tool_use",
            content=[
                {"type": "thinking", "thinking": "private reasoning"},
                {"type": "tool_use", "name": "read"},
            ],
        ),
        _record(
            request_id="request-first",
            messages=[
                initial_user,
                {"role": "assistant", "content": [{"type": "tool_use"}]},
                {"role": "user", "content": [{"type": "tool_result"}]},
            ],
            stop_reason="end_turn",
            content=[
                {"type": "thinking", "thinking": "must not be retained"},
                {
                    "type": "text",
                    "text": (
                        "第一版建议。路径 /home/bob/private；"
                        "token Bearer abcdefgh；key sk-1234567890"
                    ),
                },
            ],
        ),
        _record(
            request_id="request-correction",
            messages=[
                initial_user,
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "每个样本不同，这种方案不对"}
                    ],
                },
            ],
            stop_reason="end_turn",
            content=[{"type": "text", "text": "修正后的建议。"}],
        ),
    ]
    path = tmp_path / "input.jsonl"
    _write_jsonl(path, records)

    corpus = normalize_jsonl(path)

    assert corpus.source.record_count == 3
    assert len(corpus.sessions) == 1
    assert [turn.request_id for turn in corpus.sessions[0].turns] == [
        "request-first",
        "request-correction",
    ]
    first, correction = corpus.sessions[0].turns
    assert first.question == "分析 ~/project/model.py 的显存占用"
    assert first.answer == (
        "第一版建议。路径 ~/private；token Bearer [REDACTED]；"
        "key [REDACTED_API_KEY]"
    )
    assert first.source_line == 2
    assert correction.kind == TurnKind.CORRECTION
    serialized = corpus.model_dump_json()
    assert "system-reminder" not in serialized
    assert "private reasoning" not in serialized
    assert "/Users/alice" not in serialized
    assert "/home/bob" not in serialized
    assert "sk-1234567890" not in serialized


def test_normalize_reports_invalid_json_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"valid": true}\nnot-json\n', encoding="utf-8")

    with pytest.raises(NormalizationError, match="line 2"):
        normalize_jsonl(path)


def test_normalize_requires_completed_turn(tmp_path):
    path = tmp_path / "no-complete.jsonl"
    _write_jsonl(
        path,
        [
            _record(
                request_id="request-tool",
                messages=[],
                stop_reason="tool_use",
                content=[],
            )
        ],
    )

    with pytest.raises(NormalizationError, match="no complete end_turn"):
        normalize_jsonl(path)


def test_normalize_accepts_exported_messages_and_keeps_final_answer(tmp_path):
    path = tmp_path / "conversation.json"
    path.write_text(
        json.dumps(
            {
                "conversation": {"id": "conversation-1"},
                "messages": [
                    {"role": "user", "content": "第一个问题"},
                    {"role": "assistant", "content": "处理中"},
                    {
                        "role": "assistant",
                        "content": "最终答案，路径 /Users/alice/project；Bearer abcdefgh",
                    },
                    {"role": "user", "content": "这个方案不对"},
                    {"role": "assistant", "content": "修正后的答案"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    corpus = normalize_jsonl(path)

    assert corpus.source.record_count == 5
    assert corpus.sessions[0].session_id == "conversation-1"
    assert [turn.request_id for turn in corpus.sessions[0].turns] == [
        "message-0003",
        "message-0005",
    ]
    first, correction = corpus.sessions[0].turns
    assert first.answer == "最终答案，路径 ~/project；Bearer [REDACTED]"
    assert correction.kind == TurnKind.CORRECTION
