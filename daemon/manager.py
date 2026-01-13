# daemon/manager.py
import os
import hashlib
import threading
from typing import Dict, List

from .engine import TorrentEngine


def torrent_id_from_path(path: str) -> str:
    h = hashlib.sha1(os.path.abspath(path).encode())
    return h.hexdigest()[:12]


class TorrentManager:
    def __init__(self, cache_root: str):
        self.cache_root = os.path.abspath(cache_root)
        os.makedirs(self.cache_root, exist_ok=True)

        self._lock = threading.RLock()
        self.engines: Dict[str, TorrentEngine] = {}
        self.by_name: Dict[str, List[str]] = {}

    def add_torrent(self, torrent_path: str) -> str:
        tid = torrent_id_from_path(torrent_path)
        with self._lock:
            if tid in self.engines:
                return tid

            cache_dir = os.path.join(self.cache_root, tid)
            engine = TorrentEngine(
                torrent_path=torrent_path,
                cache_dir=cache_dir,
            )

            name = engine.info.name()
            self.engines[tid] = engine
            self.by_name.setdefault(name, []).append(tid)

            return tid

    def get_engine(self, torrent: str) -> TorrentEngine:
        with self._lock:
            if torrent in self.engines:
                return self.engines[torrent]

            if torrent in self.by_name:
                ids = self.by_name[torrent]
                if len(ids) == 1:
                    return self.engines[ids[0]]
                raise ValueError(f"TorrentNameAmbiguous:{torrent}")

            raise KeyError(f"TorrentNotFound:{torrent}")

    def list_torrents(self):
        with self._lock:
            items = list(self.engines.items())
        return [
            {
                "id": tid,
                "name": eng.info.name(),
                "cache": eng.cache_dir,
            }
            for tid, eng in items
        ]
