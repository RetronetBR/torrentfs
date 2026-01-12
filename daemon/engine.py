from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import libtorrent as lt

# Tenta usar o PathIndex do projeto.
# Se ainda não existir, usa um fallback simples.
try:
    from .index import PathIndex  # esperado: list_dir(path), stat(path)-> dict {type,size,file_index?}
except Exception:
    PathIndex = None  # fallback abaixo


# -----------------------------
# Fallback simples de índice (se você ainda não criou index.py)
# -----------------------------
@dataclass
class _Node:
    name: str
    is_dir: bool = True
    children: Dict[str, "_Node"] = None
    file_index: Optional[int] = None
    size: int = 0

    def __post_init__(self):
        if self.children is None:
            self.children = {}


class _FallbackPathIndex:
    def __init__(self) -> None:
        self.root = _Node("", True)

    def add_file(self, path: str, file_index: int, size: int) -> None:
        parts = [p for p in path.split("/") if p]
        cur = self.root
        for p in parts[:-1]:
            cur = cur.children.setdefault(p, _Node(p, True))
        leaf = cur.children.get(parts[-1])
        if leaf is None:
            leaf = _Node(parts[-1], False)
            cur.children[parts[-1]] = leaf
        leaf.is_dir = False
        leaf.file_index = file_index
        leaf.size = size

    def _walk(self, path: str) -> _Node:
        if path in ("", "/"):
            return self.root
        parts = [p for p in path.strip("/").split("/") if p]
        cur = self.root
        for p in parts:
            if p not in cur.children:
                raise FileNotFoundError(path)
            cur = cur.children[p]
        return cur

    def list_dir(self, path: str) -> List[dict]:
        node = self._walk(path)
        if not node.is_dir:
            raise NotADirectoryError(path)
        out = []
        for name, ch in sorted(node.children.items(), key=lambda kv: kv[0]):
            out.append(
                {
                    "name": name,
                    "type": "dir" if ch.is_dir else "file",
                    "size": 0 if ch.is_dir else ch.size,
                }
            )
        return out

    def stat(self, path: str) -> dict:
        node = self._walk(path)
        if node.is_dir:
            return {"type": "dir", "size": 0}
        return {"type": "file", "size": node.size, "file_index": node.file_index}


def _get_index() -> Any:
    if PathIndex is None:
        return _FallbackPathIndex()
    return PathIndex()


# -----------------------------
# Engine
# -----------------------------
class TorrentEngine:
    """
    Engine BitTorrent (libtorrent) mantido vivo pelo daemon.
    FUSE e CLI só chamam via RPC.

    - Um engine = uma sessão + um torrent handle
    - read() bloqueia até as pieces necessárias estarem disponíveis
    """

    def __init__(
        self,
        torrent_path: str,
        cache_dir: str,
        listen_from: int = 6881,
        listen_to: int = 6891,
    ) -> None:
        self.torrent_path = os.path.abspath(torrent_path)
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

        # Lock para proteger chamadas de prioridade / leitura
        self._lock = threading.RLock()

        # Session
        self.ses = lt.session()
        self.ses.listen_on(listen_from, listen_to)

        # Torrent info + handle
        self.info = lt.torrent_info(self.torrent_path)
        self.handle = self.ses.add_torrent(
            {
                "ti": self.info,
                "save_path": self.cache_dir,
                "storage_mode": lt.storage_mode_t.storage_mode_sparse,
            }
        )

        # Prioridades: começa com tudo 0
        for i in range(self.info.num_files()):
            self.handle.file_priority(i, 0)

        # Índice de paths
        self.index = _get_index()
        for i, f in enumerate(self.info.files()):
            # f.path (string com caminho relativo dentro do torrent)
            self.index.add_file(f.path, i, f.size)

    # -----------------------------
    # Utilidades
    # -----------------------------
    def _real_path(self, file_index: int) -> str:
        rel = self.info.files().file_path(file_index)
        return os.path.join(self.cache_dir, rel)

    def _is_media_path(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext in (
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
            ".m4v",
            ".webm",
            ".mp3",
            ".flac",
            ".aac",
            ".ogg",
            ".wav",
        )

    def _map_file(self, file_index: int, offset: int, size: int):
        """
        Normaliza o retorno de torrent_info.map_file().

        Pode retornar:
        - um único peer_request
        - ou uma lista de peer_request
        """

        m = self.info.map_file(file_index, offset, size)

        # Caso 1: retorno único (peer_request)
        if hasattr(m, "piece"):
            return [(int(m.piece), int(m.start), int(m.length))]

        # Caso 2: iterável de peer_request
        out = []
        for req in m:
            out.append((int(req.piece), int(req.start), int(req.length)))

        return out


    def _prioritize_for_read(
        self,
        file_index: int,
        offset: int,
        size: int,
        mode: str,
    ) -> List[int]:
        """
        Define prioridades para pieces/arquivo necessárias ao read.
        Retorna lista de piece indexes requeridas.
        """
        mapping = self._map_file(file_index, offset, size)
        needed_pieces = [p for (p, _, _) in mapping]

        stream = (mode == "stream") or (mode == "auto" and self._is_media_path(self.info.files().file_path(file_index)))

        # Sequential download ajuda muito vídeo/áudio
        if stream:
            self.handle.set_sequential_download(True)

        # Prioriza o arquivo como um todo
        self.handle.file_priority(file_index, 7 if stream else 1)

        # Prioriza as pieces necessárias (alto)
        for p in needed_pieces:
            try:
                self.handle.piece_priority(p, 7)
            except Exception:
                # alguns builds podem não expor piece_priority; nesse caso, só file_priority já ajuda
                pass

        return needed_pieces

    def _wait_pieces(self, needed_pieces: List[int], deadline_s: Optional[float] = None) -> None:
        """
        Bloqueia até todas as pieces em needed_pieces estarem disponíveis.
        deadline_s: se não None, levanta TimeoutError após esse tempo.
        """
        start = time.time()
        # loop leve
        while True:
            missing = 0
            for p in needed_pieces:
                if not self.handle.have_piece(p):
                    missing += 1
            if missing == 0:
                return

            if deadline_s is not None and (time.time() - start) > deadline_s:
                raise TimeoutError("Timeout waiting for pieces")

            time.sleep(0.02)

    # -----------------------------
    # API usada pelo RPC / FUSE / CLI
    # -----------------------------
    def list_dir(self, path: str = "") -> List[dict]:
        with self._lock:
            return self.index.list_dir(path)

    def stat(self, path: str) -> dict:
        with self._lock:
            return self.index.stat(path)

    def pin(self, path: str) -> None:
        """
        Pinar = priorizar arquivo inteiro (download total com o tempo)
        O download efetivo acontece conforme swarm/peers; o daemon mantém sessão viva e seedará.
        """
        with self._lock:
            st = self.index.stat(path)
            if st["type"] != "file":
                raise IsADirectoryError(path)
            fi = int(st["file_index"])
            self.handle.file_priority(fi, 7)

    def read(self, path: str, offset: int, size: int, mode: str = "auto") -> bytes:
        """
        Read por offset/size: ideal para FUSE e streaming (mpv/vlc).

        mode:
          - "auto": streaming para mídia (ext) e normal para demais
          - "stream": força sequential + prioridades altas
          - "normal": sem sequential (ainda prioriza pieces necessárias)
        """
        with self._lock:
            st = self.index.stat(path)
            if st["type"] != "file":
                raise IsADirectoryError(path)

            fi = int(st["file_index"])
            fsize = int(st["size"])

            if offset < 0 or size < 0:
                raise ValueError("offset/size must be >= 0")
            if offset >= fsize:
                return b""
            size = min(size, fsize - offset)

            # Ajusta prioridades e espera pieces
            needed_pieces = self._prioritize_for_read(fi, offset, size, mode=mode)

            # Bloqueia até pieces chegarem (para FUSE isso é esperado)
            self._wait_pieces(needed_pieces, deadline_s=None)

            # Lê do arquivo materializado no cache
            rp = self._real_path(fi)
            # Observação: o arquivo pode não existir ainda se nenhuma piece foi baixada;
            # mas como esperamos have_piece, normalmente ele já estará criado.
            with open(rp, "rb") as f:
                f.seek(offset)
                return f.read(size)

    def status(self) -> dict:
        with self._lock:
            s = self.handle.status()
            return {
                "name": self.info.name(),
                "progress": float(s.progress),
                "peers": int(s.num_peers),
                "downloaded": int(s.total_download),
                "uploaded": int(s.total_upload),
                "download_rate": int(s.download_rate),
                "upload_rate": int(s.upload_rate),
                "state": str(s.state),
            }
