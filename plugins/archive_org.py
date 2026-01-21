from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .base import SourceError, SourceItem


class ArchiveOrgPlugin:
    name = "archive.org"

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("archive:") or "archive.org" in uri

    def resolve(self, uri: str) -> list[SourceItem]:
        identifier = _extract_identifier(uri)
        if not identifier:
            raise SourceError("identificador archive.org invalido")

        metadata_url = f"https://archive.org/metadata/{identifier}"
        try:
            with urllib.request.urlopen(metadata_url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise SourceError(f"falha ao buscar metadata: {e}")

        files = data.get("files", [])
        if not isinstance(files, list):
            raise SourceError("metadata inesperada do archive.org")

        best = None
        for item in files:
            name = str(item.get("name", ""))
            if not name.endswith(".torrent"):
                continue
            fmt = str(item.get("format", "")).lower()
            if fmt == "archive bittorrent":
                best = name
                break
            if best is None:
                best = name

        if not best:
            raise SourceError("nenhum .torrent encontrado no archive.org")

        torrent_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(best)}"
        return [SourceItem(kind="torrent_url", value=torrent_url, name=best)]


def _extract_identifier(uri: str) -> str:
    if uri.startswith("archive:"):
        return uri.split(":", 1)[1].strip()
    if "archive.org" in uri:
        parsed = urllib.parse.urlparse(uri)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "details":
            return parts[1]
    return ""
