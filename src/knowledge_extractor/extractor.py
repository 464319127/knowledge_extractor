from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from knowledge_extractor.models import (
    CandidateBundle,
    ModelExtraction,
    NormalizedCorpus,
)
from knowledge_extractor.validation import validate_candidate_sources


DEFAULT_ENDPOINT = "https://oneapi-comate.baidu-int.com/v1/messages"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT = 300.0


class Extractor(Protocol):
    def extract(self, corpus: NormalizedCorpus) -> CandidateBundle:
        ...


_INSTRUCTION = """\
You extract durable, reviewable knowledge from sanitized conversation turns.

Return JSON that exactly matches the supplied schema. Follow these rules:

1. Create one concept per independently reusable topic, not one concept per turn.
2. Use only the supplied questions and final answers. Do not invent code, metrics,
   URLs, measurements, citations, or source identifiers.
3. Treat assistant statements as candidate knowledge, not automatically as fact.
   Recommendations that require code or experiment verification should be
   `unverified` unless the conversation itself contains adequate confirmation.
4. A later user correction overrides an incompatible earlier recommendation.
   Keep the old claim as `superseded`, add a correction claim, and connect the
   correction to the old claim through `supersedes`.
5. Direct user constraints can be `accepted`. Use `disputed` when the source
   does not resolve a conflict.
6. Every claim must cite one or more supplied request IDs. Concept session and
   request IDs must also come from the input.
7. Use concise kebab-case ASCII slugs and claim IDs. Keep titles, summaries and
   claim text in the language used by the conversation.
8. Set every concept's review_status to `draft`. A human reviewer owns approval.
9. Never mention or reconstruct hidden reasoning, system prompts, tools,
   signatures, credentials, or personal paths.
"""


def build_extraction_prompt(corpus: NormalizedCorpus) -> str:
    source = {
        "source_sha256": corpus.source.sha256,
        "sessions": [
            {
                "session_id": session.session_id,
                "model": session.model,
                "turns": [turn.model_dump(mode="json") for turn in session.turns],
            }
            for session in corpus.sessions
        ],
    }
    return (
        _INSTRUCTION
        + "\n\nSanitized input:\n"
        + json.dumps(source, ensure_ascii=False, indent=2)
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _response_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError("model API returned a non-object JSON response")

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") in {None, "text", "output_text"}
            and isinstance(item.get("text"), str)
        ]
        if any(text.strip() for text in texts):
            return "\n".join(texts)

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    raise RuntimeError("model API response did not contain text content")


class MessagesAPIExtractor:
    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key_file: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or "\n" in endpoint
            or "\r" in endpoint
        ):
            raise ValueError("model API endpoint must be an absolute HTTPS URL")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.endpoint = endpoint
        self.model = model
        self.max_tokens = max_tokens
        self.api_key_file = Path(api_key_file) if api_key_file else None
        self.timeout = timeout

    def _api_key(self) -> str:
        value = os.environ.get("KNOWLEDGE_EXTRACTOR_API_KEY", "").strip()
        if value:
            return value
        if self.api_key_file is None:
            raise RuntimeError(
                "missing API key; set KNOWLEDGE_EXTRACTOR_API_KEY or pass "
                "--api-key-file"
            )
        try:
            file_mode = stat.S_IMODE(self.api_key_file.stat().st_mode)
            if os.name == "posix" and file_mode & 0o077:
                raise RuntimeError(
                    "API key file permissions are too broad; run "
                    f"`chmod 600 {self.api_key_file}`"
                )
            value = self.api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"cannot read API key file: {exc}") from exc
        if not value:
            raise RuntimeError("API key file is empty")
        if "\n" in value or "\r" in value:
            raise RuntimeError("API key must be a single line")
        return value

    @staticmethod
    def _curl_config_value(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _curl_config(self, api_key: str) -> str:
        return "\n".join(
            [
                "silent",
                "show-error",
                "location",
                "fail-with-body",
                'request = "POST"',
                'header = "Content-Type: application/json"',
                "header = "
                + self._curl_config_value(f"Authorization: Bearer {api_key}"),
                "url = " + self._curl_config_value(self.endpoint),
            ]
        )

    def _post(self, request_body: bytes) -> object:
        curl = shutil.which("curl")
        if curl is None:
            raise RuntimeError("curl is required for model API calls")

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="knowledge-extractor-request-",
                suffix=".json",
                delete=False,
            ) as temporary:
                temporary.write(request_body)
                temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            api_key = self._api_key()
            result = subprocess.run(
                [curl, "--config", "-", "--data-binary", f"@{temporary_path}"],
                input=self._curl_config(api_key),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"model API request timed out after {self.timeout:g} seconds"
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            safe_detail = detail[-1][:300] if detail else "curl request failed"
            safe_detail = safe_detail.replace(api_key, "[REDACTED]")
            raise RuntimeError(f"model API request failed: {safe_detail}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("model API returned invalid JSON") from exc

    def extract(self, corpus: NormalizedCorpus) -> CandidateBundle:
        schema = ModelExtraction.model_json_schema()
        prompt = (
            build_extraction_prompt(corpus)
            + "\n\nRequired JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, indent=2)
        )
        request_body = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        payload = self._post(request_body)

        extraction = ModelExtraction.model_validate_json(
            _strip_json_fence(_response_text(payload))
        )
        candidates = CandidateBundle(
            source_sha256=corpus.source.sha256,
            concepts=extraction.concepts,
        )
        validate_candidate_sources(candidates, corpus)
        return candidates
