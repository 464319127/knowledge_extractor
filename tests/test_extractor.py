from __future__ import annotations

import json
import stat
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from knowledge_extractor.extractor import MessagesAPIExtractor, _response_text
from knowledge_extractor.models import (
    Claim,
    ClaimKind,
    ClaimStatus,
    Confidence,
    KnowledgeConcept,
    ModelExtraction,
    NormalizedCorpus,
    NormalizedSession,
    NormalizedTurn,
    ReviewStatus,
    SourceMetadata,
)


def _corpus() -> NormalizedCorpus:
    return NormalizedCorpus(
        source=SourceMetadata(
            name="source.jsonl",
            sha256="a" * 64,
            record_count=1,
            byte_count=10,
        ),
        sessions=[
            NormalizedSession(
                session_id="session-1",
                model="source-model",
                turns=[
                    NormalizedTurn(
                        request_id="request-1",
                        source_line=1,
                        question="问题",
                        answer="答案",
                    )
                ],
            )
        ],
    )


def _model_extraction() -> ModelExtraction:
    return ModelExtraction(
        concepts=[
            KnowledgeConcept(
                slug="example-concept",
                type="Engineering Guidance",
                title="示例知识",
                description="一条用于测试的知识。",
                summary="摘要。",
                tags=["test"],
                claims=[
                    Claim(
                        id="claim-one",
                        statement="待验证结论。",
                        kind=ClaimKind.RECOMMENDATION,
                        status=ClaimStatus.UNVERIFIED,
                        confidence=Confidence.LOW,
                        source_request_ids=["request-1"],
                    )
                ],
                source_session_ids=["session-1"],
                source_request_ids=["request-1"],
                review_status=ReviewStatus.APPROVED,
            )
        ]
    )


def test_messages_api_extracts_fenced_anthropic_response(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("test-secret\n", encoding="utf-8")
    key_file.chmod(0o600)
    response_text = "```json\n" + _model_extraction().model_dump_json() + "\n```"
    payload = {"content": [{"type": "text", "text": response_text}]}
    extractor = MessagesAPIExtractor(api_key_file=key_file)

    completed = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(payload, ensure_ascii=False),
        stderr="",
    )
    captured = {}

    def fake_run(args, **kwargs):
        request_path = Path(args[-1].removeprefix("@"))
        captured["args"] = args
        captured["config"] = kwargs["input"]
        captured["body"] = json.loads(request_path.read_text(encoding="utf-8"))
        captured["mode"] = stat.S_IMODE(request_path.stat().st_mode)
        captured["kwargs"] = kwargs
        captured["request_path"] = request_path
        return completed

    with patch(
        "knowledge_extractor.extractor.subprocess.run", side_effect=fake_run
    ):
        result = extractor.extract(_corpus())

    assert result.source_sha256 == "a" * 64
    assert result.concepts[0].review_status == ReviewStatus.DRAFT
    args = captured["args"]
    assert all("test-secret" not in argument for argument in args)
    assert "Authorization: Bearer test-secret" in captured["config"]
    assert not captured["request_path"].exists()
    assert captured["mode"] == 0o600
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["check"] is False
    sent = captured["body"]
    assert sent["model"] == "gpt-5.5"
    assert "Required JSON Schema" in sent["messages"][0]["content"]


def test_response_text_accepts_openai_wrapper():
    assert (
        _response_text({"choices": [{"message": {"content": "result"}}]})
        == "result"
    )


def test_messages_api_requires_key():
    extractor = MessagesAPIExtractor()
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="missing API key"):
            extractor.extract(_corpus())


def test_messages_api_rejects_broad_key_permissions(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("test-secret\n", encoding="utf-8")
    key_file.chmod(0o644)
    extractor = MessagesAPIExtractor(api_key_file=key_file)

    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="permissions are too broad"):
            extractor.extract(_corpus())
