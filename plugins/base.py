from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceItem:
    kind: str
    value: str
    name: str | None = None


class SourcePlugin(Protocol):
    name: str

    def can_handle(self, uri: str) -> bool:
        ...

    def resolve(self, uri: str) -> List[SourceItem]:
        ...
