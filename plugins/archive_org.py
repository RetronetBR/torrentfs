from __future__ import annotations

from .base import SourceError, SourceItem


class ArchiveOrgPlugin:
    name = "archive.org"

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("archive:") or "archive.org" in uri

    def resolve(self, uri: str) -> list[SourceItem]:
        raise SourceError(
            "Plugin archive.org ainda nao implementado. "
            "Use add-magnet ou forneca um .torrent."
        )
