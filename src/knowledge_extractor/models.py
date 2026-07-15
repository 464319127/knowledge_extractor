from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnKind(StrEnum):
    COMPLETED = "completed"
    CORRECTION = "correction"


class SourceMetadata(StrictModel):
    name: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)


class NormalizedTurn(StrictModel):
    request_id: str = Field(min_length=1)
    response_id: str | None = None
    source_line: int = Field(ge=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    kind: TurnKind = TurnKind.COMPLETED


class NormalizedSession(StrictModel):
    session_id: str = Field(min_length=1)
    model: str | None = None
    turns: list[NormalizedTurn] = Field(min_length=1)


class NormalizedCorpus(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source: SourceMetadata
    sessions: list[NormalizedSession] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class ClaimKind(StrEnum):
    FACT = "fact"
    CONSTRAINT = "constraint"
    RECOMMENDATION = "recommendation"
    DECISION = "decision"
    EXAMPLE = "example"
    CORRECTION = "correction"


class ClaimStatus(StrEnum):
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    UNVERIFIED = "unverified"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class Claim(StrictModel):
    id: str = Field(pattern=SLUG_PATTERN)
    statement: str = Field(min_length=1)
    kind: ClaimKind
    status: ClaimStatus
    confidence: Confidence
    conditions: list[str] = Field(default_factory=list)
    source_request_ids: list[str] = Field(min_length=1)
    supersedes: list[str] = Field(default_factory=list)

    @field_validator("source_request_ids", "supersedes", mode="after")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class KnowledgeConcept(StrictModel):
    slug: str = Field(pattern=SLUG_PATTERN)
    type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(min_length=1)
    source_session_ids: list[str] = Field(min_length=1)
    source_request_ids: list[str] = Field(min_length=1)
    review_status: ReviewStatus = ReviewStatus.DRAFT

    @field_validator(
        "tags", "source_session_ids", "source_request_ids", mode="after"
    )
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_claim_relationships(self) -> "KnowledgeConcept":
        claim_ids = [claim.id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim ids must be unique within a concept")
        known_claims = set(claim_ids)
        known_requests = set(self.source_request_ids)
        for claim in self.claims:
            unknown_claims = set(claim.supersedes) - known_claims
            if unknown_claims:
                raise ValueError(
                    f"claim {claim.id!r} supersedes unknown claim ids: "
                    f"{sorted(unknown_claims)}"
                )
            unknown_requests = set(claim.source_request_ids) - known_requests
            if unknown_requests:
                raise ValueError(
                    f"claim {claim.id!r} references request ids not declared "
                    f"by the concept: {sorted(unknown_requests)}"
                )
        return self


class ModelExtraction(StrictModel):
    concepts: list[KnowledgeConcept] = Field(min_length=1)

    @model_validator(mode="after")
    def force_draft_review(self) -> "ModelExtraction":
        for concept in self.concepts:
            concept.review_status = ReviewStatus.DRAFT
        return self


class CandidateBundle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    concepts: list[KnowledgeConcept] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_slugs(self) -> "CandidateBundle":
        slugs = [concept.slug for concept in self.concepts]
        if len(slugs) != len(set(slugs)):
            raise ValueError("concept slugs must be unique")
        return self

