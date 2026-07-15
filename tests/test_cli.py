from __future__ import annotations

import json

from knowledge_extractor.cli import main
from knowledge_extractor.models import NormalizedCorpus


def test_normalize_command(tmp_path):
    source = tmp_path / "source.jsonl"
    record = {
        "session_id": "session-1",
        "request_id": "request-1",
        "request": {
            "model": "test-model",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "问题"}],
                }
            ],
        },
        "response": {
            "response_data": {
                "id": "response-1",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "答案"}],
            }
        },
    }
    source.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "normalized.json"

    assert main(["normalize", str(source), "--out", str(output)]) == 0
    corpus = NormalizedCorpus.model_validate_json(output.read_text(encoding="utf-8"))
    assert corpus.sessions[0].turns[0].question == "问题"

