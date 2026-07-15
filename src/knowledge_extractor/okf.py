from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from knowledge_extractor.models import (
    CandidateBundle,
    Claim,
    ClaimKind,
    ClaimStatus,
    KnowledgeConcept,
    NormalizedCorpus,
    NormalizedSession,
    ReviewStatus,
)
from knowledge_extractor.validation import validate_candidate_sources


_FRONTMATTER_DELIMITER = "---"
_REQUIRED_KEYS = ("type", "title", "description", "timestamp")
_RESERVED_FILENAMES = {"index.md", "log.md"}
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FORBIDDEN_MARKERS = (
    "<system-reminder>",
    "<ide_opened_file>",
    "cot_plaintext",
)


class OKFError(ValueError):
    pass


@dataclass
class OKFDocument:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @classmethod
    def parse(cls, text: str) -> "OKFDocument":
        lines = text.splitlines()
        if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
            raise OKFError("concept document must start with YAML frontmatter")
        end_index = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() == _FRONTMATTER_DELIMITER
            ),
            None,
        )
        if end_index is None:
            raise OKFError("unterminated YAML frontmatter")
        try:
            frontmatter = yaml.safe_load("\n".join(lines[1:end_index])) or {}
        except yaml.YAMLError as exc:
            raise OKFError(f"invalid YAML frontmatter: {exc}") from exc
        if not isinstance(frontmatter, dict):
            raise OKFError("frontmatter must be a YAML mapping")
        body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
        return cls(frontmatter=frontmatter, body=body)

    def validate(self) -> None:
        missing = [key for key in _REQUIRED_KEYS if not self.frontmatter.get(key)]
        if missing:
            raise OKFError(f"missing required frontmatter keys: {', '.join(missing)}")
        if not isinstance(self.frontmatter.get("type"), str):
            raise OKFError("frontmatter type must be a string")

    def serialize(self) -> str:
        self.validate()
        frontmatter = yaml.safe_dump(
            self.frontmatter,
            sort_keys=False,
            allow_unicode=True,
        ).rstrip()
        body = self.body.rstrip() + "\n"
        return f"---\n{frontmatter}\n---\n\n{body}"


@dataclass(frozen=True)
class RenderStats:
    concepts: int
    references: int
    indexes: int
    files: tuple[Path, ...]


def _timestamp(value: str | None) -> str:
    if value:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OKFError(f"invalid ISO 8601 timestamp: {value}") from exc
        return value
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_session_slug(session_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-.")
    if not slug or slug in {"index", "log"}:
        raise OKFError(f"session id cannot be converted to a safe path: {session_id!r}")
    return slug


def _claim_lines(claim: Claim) -> list[str]:
    lines = [f"- **`{claim.id}`**：{claim.statement}"]
    if claim.conditions:
        lines.append(f"  - 适用条件：{'; '.join(claim.conditions)}")
    lines.append(
        "  - 来源请求："
        + ", ".join(f"`{request_id}`" for request_id in claim.source_request_ids)
    )
    if claim.supersedes:
        lines.append(
            "  - 取代：" + ", ".join(f"`{claim_id}`" for claim_id in claim.supersedes)
        )
    return lines


def _claim_section(title: str, claims: list[Claim]) -> list[str]:
    if not claims:
        return []
    lines = [f"# {title}", ""]
    for claim in claims:
        lines.extend(_claim_lines(claim))
    return lines


def _render_concept(
    concept: KnowledgeConcept,
    *,
    timestamp: str,
    reference_links: dict[str, str],
) -> OKFDocument:
    frontmatter: dict[str, Any] = {
        "type": concept.type,
        "title": concept.title,
        "description": concept.description,
        "tags": concept.tags,
        "timestamp": timestamp,
        "review_status": concept.review_status.value,
        "source_session_ids": concept.source_session_ids,
        "source_request_ids": concept.source_request_ids,
    }
    lines = [concept.summary]

    accepted = [
        claim
        for claim in concept.claims
        if claim.status == ClaimStatus.ACCEPTED
        and claim.kind != ClaimKind.RECOMMENDATION
    ]
    recommendations = [
        claim
        for claim in concept.claims
        if claim.status == ClaimStatus.ACCEPTED
        and claim.kind == ClaimKind.RECOMMENDATION
    ]
    unverified = [
        claim for claim in concept.claims if claim.status == ClaimStatus.UNVERIFIED
    ]
    disputed = [
        claim for claim in concept.claims if claim.status == ClaimStatus.DISPUTED
    ]
    superseded = [
        claim for claim in concept.claims if claim.status == ClaimStatus.SUPERSEDED
    ]

    for section in (
        _claim_section("已接受知识", accepted),
        _claim_section("建议", recommendations),
        _claim_section("待验证", unverified),
        _claim_section("存在争议", disputed),
        _claim_section("已失效建议", superseded),
    ):
        if section:
            lines.extend(["", *section])

    lines.extend(["", "# Provenance", ""])
    lines.append(
        "- 会话："
        + ", ".join(f"`{session_id}`" for session_id in concept.source_session_ids)
    )
    lines.append(
        "- 请求："
        + ", ".join(f"`{request_id}`" for request_id in concept.source_request_ids)
    )
    lines.extend(["", "# Citations", ""])
    for index, session_id in enumerate(concept.source_session_ids, start=1):
        lines.append(
            f"[{index}] [会话 {session_id}]({reference_links[session_id]})"
        )
    return OKFDocument(frontmatter=frontmatter, body="\n".join(lines))


def _quote_markdown(text: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in text.splitlines()]


def _render_session_reference(
    session: NormalizedSession,
    corpus: NormalizedCorpus,
    *,
    timestamp: str,
    included_request_ids: set[str],
) -> OKFDocument:
    turns = [
        turn for turn in session.turns if turn.request_id in included_request_ids
    ]
    frontmatter: dict[str, Any] = {
        "type": "Conversation Evidence",
        "resource": f"urn:knowledge-extractor:conversation:{session.session_id}",
        "title": f"会话证据 {session.session_id}",
        "description": "知识提取所使用的脱敏会话来源索引。",
        "tags": ["conversation", "provenance"],
        "timestamp": timestamp,
        "source_name": corpus.source.name,
        "source_sha256": corpus.source.sha256,
        "source_request_ids": [turn.request_id for turn in turns],
    }
    lines = [
        "本文档只保存已清洗的问题和来源定位信息，不包含完整回答、"
        "思维链、系统提示或工具运行内容。",
        "",
        "# Source",
        "",
        f"- 文件：`{corpus.source.name}`",
        f"- SHA-256：`{corpus.source.sha256}`",
        f"- 会话：`{session.session_id}`",
    ]
    if session.model:
        lines.append(f"- 模型：`{session.model}`")
    lines.extend(["", "# Questions", ""])
    for turn in turns:
        lines.extend(
            [
                f"## `{turn.request_id}`",
                "",
                f"来源行：`{turn.source_line}`；类型：`{turn.kind.value}`。",
                "",
                *_quote_markdown(turn.question),
                "",
            ]
        )
    return OKFDocument(frontmatter=frontmatter, body="\n".join(lines))


def _load_frontmatter(path: Path) -> dict[str, Any]:
    return OKFDocument.parse(path.read_text(encoding="utf-8")).frontmatter


def _build_index(entries: list[tuple[str, str, str, str]]) -> str:
    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for type_name, title, link, description in entries:
        grouped[type_name or "Other"].append((title, link, description))
    sections: list[str] = []
    for type_name in sorted(grouped):
        lines = [f"# {type_name}", ""]
        for title, link, description in sorted(
            grouped[type_name], key=lambda item: item[0].casefold()
        ):
            suffix = f" - {description}" if description else ""
            lines.append(f"* [{title}]({link}){suffix}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def regenerate_indexes(bundle_root: Path) -> list[Path]:
    directories = {bundle_root}
    for path in bundle_root.rglob("*.md"):
        if path.name in _RESERVED_FILENAMES:
            continue
        current = path.parent
        while current == bundle_root or bundle_root in current.parents:
            directories.add(current)
            if current == bundle_root:
                break
            current = current.parent

    descriptions: dict[Path, str] = {}
    written: list[Path] = []
    for directory in sorted(
        directories,
        key=lambda path: (-len(path.relative_to(bundle_root).parts), str(path)),
    ):
        entries: list[tuple[str, str, str, str]] = []
        for child in sorted(directory.iterdir()):
            if child.name in _RESERVED_FILENAMES:
                continue
            if child.is_file() and child.suffix == ".md":
                frontmatter = _load_frontmatter(child)
                entries.append(
                    (
                        str(frontmatter.get("type") or "Other"),
                        str(frontmatter.get("title") or child.stem),
                        child.name,
                        str(frontmatter.get("description") or ""),
                    )
                )
            elif child.is_dir() and (child / "index.md").exists():
                entries.append(
                    (
                        "Subdirectories",
                        child.name,
                        f"{child.name}/index.md",
                        descriptions.get(child, ""),
                    )
                )
        if not entries:
            continue
        index_path = directory / "index.md"
        index_path.write_text(_build_index(entries), encoding="utf-8")
        written.append(index_path)
        descriptions[directory] = f"包含 {len(entries)} 个知识条目。"
    return written


def _prepare_bundle_root(bundle_root: Path) -> None:
    if bundle_root.exists() and not bundle_root.is_dir():
        raise OKFError(f"output path is not a directory: {bundle_root}")
    if bundle_root.exists() and any(bundle_root.iterdir()):
        raise OKFError(
            f"output directory is not empty: {bundle_root}; choose an empty "
            "directory to avoid overwriting user files or stale concepts"
        )


def render_bundle(
    corpus: NormalizedCorpus,
    candidates: CandidateBundle,
    bundle_root: str | Path,
    *,
    timestamp: str | None = None,
) -> RenderStats:
    validate_candidate_sources(candidates, corpus)
    approved = [
        concept
        for concept in candidates.concepts
        if concept.review_status == ReviewStatus.APPROVED
    ]
    if not approved:
        raise OKFError(
            "no approved concepts; review candidates.json and set selected "
            "concepts to review_status=approved"
        )

    root = Path(bundle_root)
    _prepare_bundle_root(root)
    rendered_timestamp = _timestamp(timestamp)
    sessions_by_id = {session.session_id: session for session in corpus.sessions}
    used_sessions = {
        session_id for concept in approved for session_id in concept.source_session_ids
    }
    used_requests = {
        request_id for concept in approved for request_id in concept.source_request_ids
    }

    slug_to_session: dict[str, str] = {}
    for session_id in used_sessions:
        session_slug = _safe_session_slug(session_id)
        previous = slug_to_session.setdefault(session_slug, session_id)
        if previous != session_id:
            raise OKFError(
                f"session ids {previous!r} and {session_id!r} map to the same "
                f"reference filename {session_slug!r}"
            )

    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".knowledge-extractor-render-", dir=root.parent
    ) as temporary_directory:
        staging_root = Path(temporary_directory) / "bundle"
        staging_root.mkdir()
        staged_files: list[Path] = []
        reference_links: dict[str, str] = {}
        for session_id in sorted(used_sessions):
            session_slug = _safe_session_slug(session_id)
            relative_path = (
                Path("references") / "conversations" / f"{session_slug}.md"
            )
            destination = staging_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            document = _render_session_reference(
                sessions_by_id[session_id],
                corpus,
                timestamp=rendered_timestamp,
                included_request_ids=used_requests,
            )
            destination.write_text(document.serialize(), encoding="utf-8")
            staged_files.append(destination)
            reference_links[session_id] = f"../{relative_path.as_posix()}"

        for concept in sorted(approved, key=lambda item: item.slug):
            destination = staging_root / "concepts" / f"{concept.slug}.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            document = _render_concept(
                concept,
                timestamp=rendered_timestamp,
                reference_links=reference_links,
            )
            destination.write_text(document.serialize(), encoding="utf-8")
            staged_files.append(destination)

        indexes = regenerate_indexes(staging_root)
        staged_files.extend(indexes)
        validate_bundle(staging_root)
        relative_files = [path.relative_to(staging_root) for path in staged_files]
        if root.exists():
            root.rmdir()
        staging_root.replace(root)

    written = tuple(root / path for path in relative_files)
    return RenderStats(
        concepts=len(approved),
        references=len(used_sessions),
        indexes=len(indexes),
        files=written,
    )


def validate_bundle(bundle_root: str | Path) -> None:
    root = Path(bundle_root)
    errors: list[str] = []
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root)
        for marker in _FORBIDDEN_MARKERS:
            if marker.casefold() in text.casefold():
                errors.append(f"{relative}: contains forbidden marker {marker!r}")
        if path.name in _RESERVED_FILENAMES:
            continue
        try:
            document = OKFDocument.parse(text)
            document.validate()
        except OKFError as exc:
            errors.append(f"{relative}: {exc}")
            continue
        for raw_target in _MARKDOWN_LINK_RE.findall(document.body):
            target = raw_target.split("#", 1)[0].strip()
            parsed = urlparse(target)
            if not target.endswith(".md") or parsed.scheme:
                continue
            resolved = (
                root / target.lstrip("/")
                if target.startswith("/")
                else path.parent / target
            ).resolve()
            if not resolved.is_relative_to(root.resolve()):
                errors.append(f"{relative}: Markdown link escapes bundle: {raw_target!r}")
            elif not resolved.is_file():
                errors.append(f"{relative}: broken Markdown link {raw_target!r}")
    if errors:
        raise OKFError("bundle validation failed:\n- " + "\n- ".join(errors))
