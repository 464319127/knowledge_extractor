from __future__ import annotations

from knowledge_extractor.models import CandidateBundle, NormalizedCorpus


class CandidateValidationError(ValueError):
    pass


def validate_candidate_sources(
    candidates: CandidateBundle,
    corpus: NormalizedCorpus,
) -> None:
    if candidates.source_sha256 != corpus.source.sha256:
        raise CandidateValidationError(
            "candidate source_sha256 does not match the normalized source"
        )

    request_to_session = {
        turn.request_id: session.session_id
        for session in corpus.sessions
        for turn in session.turns
    }
    known_sessions = {session.session_id for session in corpus.sessions}
    known_requests = set(request_to_session)

    for concept in candidates.concepts:
        concept_sessions = set(concept.source_session_ids)
        concept_requests = set(concept.source_request_ids)
        unknown_sessions = concept_sessions - known_sessions
        if unknown_sessions:
            raise CandidateValidationError(
                f"concept {concept.slug!r} references unknown sessions: "
                f"{sorted(unknown_sessions)}"
            )
        unknown_requests = concept_requests - known_requests
        if unknown_requests:
            raise CandidateValidationError(
                f"concept {concept.slug!r} references unknown requests: "
                f"{sorted(unknown_requests)}"
            )
        undeclared_sessions = {
            request_to_session[request_id]
            for request_id in concept_requests
            if request_to_session[request_id] not in concept_sessions
        }
        if undeclared_sessions:
            raise CandidateValidationError(
                f"concept {concept.slug!r} does not declare sessions used by "
                f"its request ids: {sorted(undeclared_sessions)}"
            )

