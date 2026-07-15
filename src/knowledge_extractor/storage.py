from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


def read_model(path: str | Path, model_type: type[ModelT]) -> ModelT:
    source = Path(path)
    return model_type.model_validate_json(source.read_text(encoding="utf-8"))


def write_model(path: str | Path, model: BaseModel) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        model.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination

