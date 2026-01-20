from __future__ import annotations

from .base import SourceItem


class MagnetPlugin:
    name = "magnet"

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("magnet:")

    def resolve(self, uri: str) -> list[SourceItem]:
        return [SourceItem(kind="magnet", value=uri)]
