from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from knowledge_extractor import __version__
from knowledge_extractor.extractor import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    MessagesAPIExtractor,
)
from knowledge_extractor.models import CandidateBundle, NormalizedCorpus
from knowledge_extractor.normalizer import NormalizationError, normalize_jsonl
from knowledge_extractor.okf import OKFError, render_bundle, validate_bundle
from knowledge_extractor.storage import read_model, write_model
from knowledge_extractor.validation import CandidateValidationError


def _add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--api-key-file",
        type=Path,
        default=None,
        help=(
            "Read the Bearer token from this file. The "
            "KNOWLEDGE_EXTRACTOR_API_KEY environment variable takes precedence."
        ),
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-extractor")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    normalize = commands.add_parser(
        "normalize",
        help=(
            "Convert cumulative API JSONL or export-conversation messages JSON "
            "into sanitized Q/A turns."
        ),
    )
    normalize.add_argument("input", type=Path)
    normalize.add_argument("--out", required=True, type=Path)

    extract = commands.add_parser(
        "extract", help="Extract draft knowledge candidates with a model API."
    )
    extract.add_argument("normalized", type=Path)
    extract.add_argument("--out", required=True, type=Path)
    _add_model_arguments(extract)

    render = commands.add_parser(
        "render", help="Render explicitly approved candidates as an OKF bundle."
    )
    render.add_argument("--normalized", required=True, type=Path)
    render.add_argument("--candidates", required=True, type=Path)
    render.add_argument("--out", required=True, type=Path)
    render.add_argument(
        "--timestamp",
        default=None,
        help="ISO 8601 document timestamp; defaults to the current UTC time.",
    )
    validate = commands.add_parser(
        "validate", help="Validate frontmatter, links and leak markers in a bundle."
    )
    validate.add_argument("bundle", type=Path)

    run = commands.add_parser(
        "run",
        help="Normalize input and extract drafts into a staging directory.",
    )
    run.add_argument("input", type=Path)
    run.add_argument("--staging", required=True, type=Path)
    _add_model_arguments(run)
    return parser


def _extractor(args: argparse.Namespace) -> MessagesAPIExtractor:
    return MessagesAPIExtractor(
        endpoint=args.endpoint,
        model=args.model,
        max_tokens=args.max_tokens,
        api_key_file=args.api_key_file,
        timeout=args.timeout,
    )


def _normalize(input_path: Path, output_path: Path) -> NormalizedCorpus:
    corpus = normalize_jsonl(input_path)
    write_model(output_path, corpus)
    print(
        f"Normalized {sum(len(session.turns) for session in corpus.sessions)} "
        f"turn(s) from {corpus.source.record_count} record(s) into {output_path}",
        file=sys.stderr,
    )
    for warning in corpus.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return corpus


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "normalize":
            _normalize(args.input, args.out)
            return 0

        if args.command == "extract":
            corpus = read_model(args.normalized, NormalizedCorpus)
            candidates = _extractor(args).extract(corpus)
            write_model(args.out, candidates)
            print(
                f"Wrote {len(candidates.concepts)} draft concept(s) to {args.out}; "
                "review and approve them before rendering",
                file=sys.stderr,
            )
            return 0

        if args.command == "render":
            corpus = read_model(args.normalized, NormalizedCorpus)
            candidates = read_model(args.candidates, CandidateBundle)
            stats = render_bundle(
                corpus,
                candidates,
                args.out,
                timestamp=args.timestamp,
            )
            print(
                f"Rendered {stats.concepts} concept(s), {stats.references} "
                f"reference(s), and {stats.indexes} index file(s) into {args.out}",
                file=sys.stderr,
            )
            return 0

        if args.command == "validate":
            validate_bundle(args.bundle)
            print(f"Validated OKF bundle at {args.bundle}", file=sys.stderr)
            return 0

        if args.command == "run":
            normalized_path = args.staging / "normalized.json"
            candidates_path = args.staging / "candidates.json"
            corpus = _normalize(args.input, normalized_path)
            candidates = _extractor(args).extract(corpus)
            write_model(candidates_path, candidates)
            print(
                f"Wrote {len(candidates.concepts)} draft concept(s) to "
                f"{candidates_path}",
                file=sys.stderr,
            )
            print(
                "Review candidates.json, set selected review_status values to "
                "approved, then run `knowledge-extractor render`.",
                file=sys.stderr,
            )
            return 0
    except (
        CandidateValidationError,
        NormalizationError,
        OKFError,
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1
