from __future__ import annotations

import pytest

from knowledge_extractor.models import (
    CandidateBundle,
    Claim,
    ClaimKind,
    ClaimStatus,
    Confidence,
    KnowledgeConcept,
    NormalizedCorpus,
    NormalizedSession,
    NormalizedTurn,
    ReviewStatus,
    SourceMetadata,
)
from knowledge_extractor.okf import OKFDocument, OKFError, render_bundle, validate_bundle


def _corpus() -> NormalizedCorpus:
    return NormalizedCorpus(
        source=SourceMetadata(
            name="source.jsonl",
            sha256="b" * 64,
            record_count=2,
            byte_count=100,
        ),
        sessions=[
            NormalizedSession(
                session_id="session-1",
                model="source-model",
                turns=[
                    NormalizedTurn(
                        request_id="request-old",
                        source_line=1,
                        question="最初的问题",
                        answer="SECRET COMPLETE ANSWER",
                    ),
                    NormalizedTurn(
                        request_id="request-new",
                        source_line=2,
                        question="这个方案不对",
                        answer="SECRET CORRECTED ANSWER",
                        kind="correction",
                    ),
                ],
            )
        ],
    )


def _candidates(review_status: ReviewStatus) -> CandidateBundle:
    return CandidateBundle(
        source_sha256="b" * 64,
        concepts=[
            KnowledgeConcept(
                slug="routing-constraint",
                type="Engineering Guidance",
                title="逐样本路由约束",
                description="解释逐样本专家路由对优化方案的约束。",
                summary="每个样本可以激活不同专家。",
                tags=["moe", "routing"],
                claims=[
                    Claim(
                        id="global-gather",
                        statement="整个批次统一 gather 专家。",
                        kind=ClaimKind.RECOMMENDATION,
                        status=ClaimStatus.SUPERSEDED,
                        confidence=Confidence.LOW,
                        source_request_ids=["request-old"],
                    ),
                    Claim(
                        id="per-sample-routing",
                        statement="必须保留逐样本路由语义。",
                        kind=ClaimKind.CORRECTION,
                        status=ClaimStatus.ACCEPTED,
                        confidence=Confidence.HIGH,
                        source_request_ids=["request-new"],
                        supersedes=["global-gather"],
                    ),
                ],
                source_session_ids=["session-1"],
                source_request_ids=["request-old", "request-new"],
                review_status=review_status,
            )
        ],
    )


def test_render_requires_approved_concept(tmp_path):
    with pytest.raises(OKFError, match="no approved concepts"):
        render_bundle(_corpus(), _candidates(ReviewStatus.DRAFT), tmp_path / "bundle")


def test_render_creates_valid_sanitized_bundle(tmp_path):
    root = tmp_path / "bundle"
    stats = render_bundle(
        _corpus(),
        _candidates(ReviewStatus.APPROVED),
        root,
        timestamp="2026-07-15T00:00:00+08:00",
    )

    assert stats.concepts == 1
    assert stats.references == 1
    assert (root / "index.md").exists()
    concept_path = root / "concepts" / "routing-constraint.md"
    reference_path = root / "references" / "conversations" / "session-1.md"
    concept = OKFDocument.parse(concept_path.read_text(encoding="utf-8"))
    assert concept.frontmatter["review_status"] == "approved"
    assert "# 已失效建议" in concept.body
    assert "../references/conversations/session-1.md" in concept.body
    reference_text = reference_path.read_text(encoding="utf-8")
    assert "最初的问题" in reference_text
    assert "SECRET COMPLETE ANSWER" not in reference_text
    assert "SECRET CORRECTED ANSWER" not in reference_text
    validate_bundle(root)


def test_render_rejects_nonempty_output(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "user-file.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(OKFError, match="not empty"):
        render_bundle(
            _corpus(),
            _candidates(ReviewStatus.APPROVED),
            root,
        )
    assert (root / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_validate_rejects_link_outside_bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    document = OKFDocument(
        frontmatter={
            "type": "Reference",
            "title": "Unsafe link",
            "description": "Contains a link outside the bundle.",
            "timestamp": "2026-07-15T00:00:00+08:00",
        },
        body="[outside](../outside.md)",
    )
    (root / "concept.md").write_text(document.serialize(), encoding="utf-8")

    with pytest.raises(OKFError, match="link escapes bundle"):
        validate_bundle(root)
