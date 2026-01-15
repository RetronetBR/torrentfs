from __future__ import annotations

import os
import json
import sys
import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import libtorrent as lt

# Limites mais altos para torrents com metadata grande.
DEFAULT_MAX_METADATA_BYTES = 100 * 1024 * 1024
DEFAULT_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "torrentfsd.json")
)
PREFETCH_MEDIA_START_PCT = 0.10
PREFETCH_MEDIA_END_PCT = 0.02
PREFETCH_MEDIA_START_MIN = 4 * 1024 * 1024
PREFETCH_MEDIA_START_MAX = 64 * 1024 * 1024
PREFETCH_MEDIA_END_MIN = 1 * 1024 * 1024
PREFETCH_MEDIA_END_MAX = 16 * 1024 * 1024
PREFETCH_OTHER_START_PCT = 0.10
PREFETCH_OTHER_END_PCT = 0.05
PREFETCH_OTHER_START_MIN = 1 * 1024 * 1024
PREFETCH_OTHER_START_MAX = 32 * 1024 * 1024
PREFETCH_OTHER_END_MIN = 1 * 1024 * 1024
PREFETCH_OTHER_END_MAX = 16 * 1024 * 1024


def _parse_size_mb(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value) * 1024 * 1024)
    except (TypeError, ValueError):
        return None


def _parse_pct(value, default: float) -> float:
    if value is None:
        return default
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if val > 1.0:
        val = val / 100.0
    if val <= 0:
        return default
    return val


def _get_cfg(cfg: dict, path: str, default):
    cur = cfg
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _resolve_max_metadata(cfg: dict) -> int:
    max_metadata = DEFAULT_MAX_METADATA_BYTES
    mb_value = _parse_size_mb(cfg.get("max_metadata_mb"))
    if mb_value:
        max_metadata = mb_value
    elif "max_metadata_bytes" in cfg:
        max_metadata = int(cfg.get("max_metadata_bytes", DEFAULT_MAX_METADATA_BYTES))
    return max_metadata


def _resolve_prefetch_max_bytes(cfg: dict) -> int:
    mb_value = _parse_size_mb(_get_cfg(cfg, "prefetch.max_mb", None))
    if mb_value:
        return mb_value
    val = _get_cfg(cfg, "prefetch.max_bytes", None)
    try:
        return int(val) if val else 0
    except (TypeError, ValueError):
        return 0


def _load_prefetch_cfg(cfg: dict) -> dict:
    media = {
        "start_pct": _parse_pct(_get_cfg(cfg, "prefetch.media.start_pct", None), PREFETCH_MEDIA_START_PCT),
        "end_pct": _parse_pct(_get_cfg(cfg, "prefetch.media.end_pct", None), PREFETCH_MEDIA_END_PCT),
        "start_min": _parse_size_mb(_get_cfg(cfg, "prefetch.media.start_min_mb", None)) or PREFETCH_MEDIA_START_MIN,
        "start_max": _parse_size_mb(_get_cfg(cfg, "prefetch.media.start_max_mb", None)) or PREFETCH_MEDIA_START_MAX,
        "end_min": _parse_size_mb(_get_cfg(cfg, "prefetch.media.end_min_mb", None)) or PREFETCH_MEDIA_END_MIN,
        "end_max": _parse_size_mb(_get_cfg(cfg, "prefetch.media.end_max_mb", None)) or PREFETCH_MEDIA_END_MAX,
    }
    other = {
        "start_pct": _parse_pct(_get_cfg(cfg, "prefetch.other.start_pct", None), PREFETCH_OTHER_START_PCT),
        "end_pct": _parse_pct(_get_cfg(cfg, "prefetch.other.end_pct", None), PREFETCH_OTHER_END_PCT),
        "start_min": _parse_size_mb(_get_cfg(cfg, "prefetch.other.start_min_mb", None)) or PREFETCH_OTHER_START_MIN,
        "start_max": _parse_size_mb(_get_cfg(cfg, "prefetch.other.start_max_mb", None)) or PREFETCH_OTHER_START_MAX,
        "end_min": _parse_size_mb(_get_cfg(cfg, "prefetch.other.end_min_mb", None)) or PREFETCH_OTHER_END_MIN,
        "end_max": _parse_size_mb(_get_cfg(cfg, "prefetch.other.end_max_mb", None)) or PREFETCH_OTHER_END_MAX,
    }
    media["extensions"] = _load_media_exts(cfg)
    return {"media": media, "other": other}


def _load_media_exts(cfg: dict) -> List[str]:
    exts = _get_cfg(cfg, "prefetch.media.extensions", None)
    if not exts:
        return [
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
            ".pdf",
        ]
    if isinstance(exts, list):
        out = []
        for item in exts:
            if not isinstance(item, str):
                continue
            ext = item.strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            out.append(ext)
        return out
    return []


def get_effective_config() -> dict:
    cfg = _load_config_with_meta()
    return {
        "config_path": cfg.get("_config_path"),
        "max_metadata_bytes": _resolve_max_metadata(cfg),
        "prefetch": _load_prefetch_cfg(cfg),
        "media_extensions": _load_media_exts(cfg),
        "prefetch_on_start": bool(_get_cfg(cfg, "prefetch.on_start", False)),
        "prefetch_on_start_mode": _get_cfg(cfg, "prefetch.on_start_mode", "media"),
        "prefetch_max_files": int(_get_cfg(cfg, "prefetch.max_files", 0) or 0),
        "prefetch_sleep_ms": int(_get_cfg(cfg, "prefetch.sleep_ms", 25) or 0),
        "prefetch_batch_size": int(_get_cfg(cfg, "prefetch.batch_size", 10) or 10),
        "prefetch_batch_sleep_ms": int(_get_cfg(cfg, "prefetch.batch_sleep_ms", 200) or 0),
        "prefetch_scan_sleep_ms": int(_get_cfg(cfg, "prefetch.scan_sleep_ms", 5) or 0),
        "prefetch_max_dirs": int(_get_cfg(cfg, "prefetch.max_dirs", 0) or 0),
        "prefetch_max_bytes": _resolve_prefetch_max_bytes(cfg),
        "skip_check": bool(_get_cfg(cfg, "skip_check", False)),
        "resume_save_interval_s": int(_get_cfg(cfg, "resume.save_interval_s", 300) or 0),
        "checking_max_active": int(_get_cfg(cfg, "checking.max_active", 0) or 0),
    }


_SKIP_CHECK_WARNED = False


def _build_add_torrent_params(info: lt.torrent_info, cache_dir: str, skip_check: bool) -> dict:
    global _SKIP_CHECK_WARNED
    params = {
        "ti": info,
        "save_path": cache_dir,
        "storage_mode": lt.storage_mode_t.storage_mode_sparse,
    }
    if not skip_check:
        return params

    try:
        tflags = getattr(lt, "torrent_flags_t", None)
    except Exception:
        tflags = None
    if tflags is None:
        if not _SKIP_CHECK_WARNED:
            print("[torrentfs] skip_check nao suportado nesta versao do libtorrent", file=sys.stderr)
            _SKIP_CHECK_WARNED = True
        return params

    flag = None
    for name in ("flag_no_verify_files", "flag_disable_hash_checks", "flag_skip_hash_checking"):
        flag = getattr(tflags, name, None)
        if flag is not None:
            break
    if flag is None:
        if not _SKIP_CHECK_WARNED:
            print("[torrentfs] skip_check nao suportado nesta versao do libtorrent", file=sys.stderr)
            _SKIP_CHECK_WARNED = True
        return params

    try:
        base_flags = tflags.default_flags
    except Exception:
        try:
            base_flags = tflags(0)
        except Exception:
            return params

    params["flags"] = base_flags | flag
    return params


def _load_config() -> dict:
    path = os.environ.get("TORRENTFSD_CONFIG", DEFAULT_CONFIG_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[torrentfs] config invalida: {e}", file=sys.stderr)
        return {}


def _load_config_with_meta() -> dict:
    path = os.environ.get("TORRENTFSD_CONFIG", DEFAULT_CONFIG_PATH)
    data = _load_config()
    data["_config_path"] = path
    return data

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


def _load_torrent_info(path: str, max_metadata: int) -> lt.torrent_info:
    try:
        return lt.torrent_info(
            path,
            {
                "max_metadata_size": max_metadata,
                "max_torrent_file_size": max_metadata,
            },
        )
    except Exception as e:
        if "metadata too large" not in str(e):
            raise

    # Fallback: carrega o .torrent manualmente e bdecode
    with open(path, "rb") as f:
        data = f.read()
    return lt.torrent_info(lt.bdecode(data))


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
        skip_check: Optional[bool] = None,
    ) -> None:
        self.torrent_path = os.path.abspath(torrent_path)
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

        # Lock para proteger chamadas de prioridade / leitura
        self._lock = threading.RLock()
        self._pinned_files: set[int] = set()
        self._pinned_paths: set[str] = set()
        self._pins_path = os.path.join(self.cache_dir, ".pinned.json")

        # Session
        self.ses = lt.session()
        cfg = _load_config_with_meta()
        self._config_path = cfg.get("_config_path")
        max_metadata = _resolve_max_metadata(cfg)
        self._max_metadata_bytes = max_metadata
        self._prefetch_cfg = _load_prefetch_cfg(cfg)
        self._media_exts = set(_load_media_exts(cfg))
        self._prefetch_max_bytes = _resolve_prefetch_max_bytes(cfg)
        self._skip_check = bool(cfg.get("skip_check")) if skip_check is None else bool(skip_check)
        self._resume_save_interval_s = int(_get_cfg(cfg, "resume.save_interval_s", 300) or 0)
        self._resume_path = os.path.join(self.cache_dir, ".resume_data")
        self._resume_stop = threading.Event()
        self._checking_max_active = int(_get_cfg(cfg, "checking.max_active", 0) or 0)
        try:
            settings = {
                "max_metadata_size": max_metadata,
                "max_torrent_file_size": max_metadata,
            }
            if self._checking_max_active > 0:
                settings["max_active_checking_torrents"] = self._checking_max_active
            self.ses.apply_settings(settings)
        except Exception:
            # Algumas builds nao expõem todas as chaves.
            pass
        self.ses.listen_on(listen_from, listen_to)

        # Torrent info + handle
        self.info = _load_torrent_info(self.torrent_path, max_metadata)
        params = _build_add_torrent_params(self.info, self.cache_dir, self._skip_check)
        resume_data = self._load_resume_data()
        if resume_data:
            params["resume_data"] = resume_data
        self.handle = self.ses.add_torrent(params)

        # Prioridades: começa com tudo 0
        for i in range(self.info.num_files()):
            self.handle.file_priority(i, 0)

        # Índice de paths
        self.index = _get_index()
        for i, f in enumerate(self.info.files()):
            # f.path (string com caminho relativo dentro do torrent)
            self.index.add_file(f.path, i, f.size)

        self._load_pins()
        if self._resume_save_interval_s > 0:
            threading.Thread(target=self._resume_loop, daemon=True).start()

    # -----------------------------
    # Utilidades
    # -----------------------------
    def _real_path(self, file_index: int) -> str:
        rel = self.info.files().file_path(file_index)
        return os.path.join(self.cache_dir, rel)

    def _load_resume_data(self) -> Optional[bytes]:
        try:
            with open(self._resume_path, "rb") as f:
                data = f.read()
            return data or None
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def _write_resume_data(self, data) -> None:
        try:
            if isinstance(data, (bytes, bytearray, memoryview)):
                out = bytes(data)
            else:
                out = lt.bencode(data)
        except Exception:
            return
        tmp = f"{self._resume_path}.tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, self._resume_path)

    def _save_resume_data(self, timeout_s: float = 5.0) -> None:
        try:
            self.handle.save_resume_data()
        except Exception:
            return
        start = time.time()
        alert_ok = getattr(lt, "save_resume_data_alert", None)
        alert_fail = getattr(lt, "save_resume_data_failed_alert", None)
        while (time.time() - start) < timeout_s:
            alerts = self.ses.pop_alerts()
            for a in alerts:
                if alert_fail and isinstance(a, alert_fail):
                    return
                if alert_ok and isinstance(a, alert_ok):
                    try:
                        self._write_resume_data(a.resume_data)
                    except Exception:
                        pass
                    return
            time.sleep(0.05)

    def _resume_loop(self) -> None:
        while not self._resume_stop.is_set():
            self._resume_stop.wait(self._resume_save_interval_s)
            if self._resume_stop.is_set():
                break
            with self._lock:
                self._save_resume_data()

    def _is_media_path(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext in self._media_exts

    def is_media_path(self, path: str) -> bool:
        return self._is_media_path(path)

    def prefetch_bytes(self, path: str) -> int:
        st = self.index.stat(path)
        if st["type"] != "file":
            raise IsADirectoryError(path)
        fi = int(st["file_index"])
        size = int(st["size"])
        ranges = self._prefetch_ranges(self.info.files().file_path(fi), size)
        return sum(length for _, length in ranges)

    def prefetch_info(self, path: str) -> dict:
        st = self.index.stat(path)
        if st["type"] != "file":
            raise IsADirectoryError(path)
        fi = int(st["file_index"])
        size = int(st["size"])
        ranges = self._prefetch_ranges(self.info.files().file_path(fi), size)
        pieces = set()
        total_bytes = 0
        for offset, length in ranges:
            total_bytes += length
            for p, _, _ in self._map_file(fi, offset, length):
                pieces.add(p)
        prefetch_pct = round((total_bytes / size) * 100.0, 2) if size > 0 else 0.0
        return {
            "path": path,
            "size": size,
            "prefetch_bytes": total_bytes,
            "prefetch_pieces": len(pieces),
            "prefetch_pct": prefetch_pct,
            "ranges": [{"offset": o, "length": l} for o, l in ranges],
        }

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

    def _calc_prefetch_len(self, size: int, pct: float, min_b: int, max_b: int) -> int:
        if size <= 0:
            return 0
        if size <= min_b:
            return size
        target = int(size * pct)
        if target < min_b:
            target = min_b
        if max_b and target > max_b:
            target = max_b
        if target > size:
            target = size
        return target

    def _prefetch_ranges(self, path: str, size: int) -> List[Tuple[int, int]]:
        is_media = self._is_media_path(path)
        cfg = self._prefetch_cfg["media"] if is_media else self._prefetch_cfg["other"]
        if is_media:
            start_len = self._calc_prefetch_len(
                size, cfg["start_pct"], cfg["start_min"], cfg["start_max"]
            )
            end_len = self._calc_prefetch_len(
                size, cfg["end_pct"], cfg["end_min"], cfg["end_max"]
            )
        else:
            start_len = self._calc_prefetch_len(
                size, cfg["start_pct"], cfg["start_min"], cfg["start_max"]
            )
            end_len = self._calc_prefetch_len(
                size, cfg["end_pct"], cfg["end_min"], cfg["end_max"]
            )

        ranges = []
        if start_len > 0:
            ranges.append((0, start_len))

        if end_len > 0 and end_len < size:
            end_start = size - end_len
            if end_start > start_len:
                ranges.append((end_start, end_len))

        return ranges

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

        stream = (mode == "stream") or (
            mode == "auto" and self._is_media_path(self.info.files().file_path(file_index))
        )

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
            self._pinned_files.add(fi)
            self._pinned_paths.add(path)
            self._save_pins()

    def unpin(self, path: str) -> None:
        with self._lock:
            st = self.index.stat(path)
            if st["type"] != "file":
                raise IsADirectoryError(path)
            fi = int(st["file_index"])
            try:
                self.handle.file_priority(fi, 0)
            except Exception:
                pass
            self._pinned_files.discard(fi)
            self._pinned_paths.discard(path)
            self._save_pins()

    def list_pins(self) -> List[dict]:
        with self._lock:
            try:
                file_progress = self.handle.file_progress()
            except Exception:
                file_progress = None
            items = []
            for fi in sorted(self._pinned_files):
                path = self.info.files().file_path(fi)
                size = int(self.info.files().file_size(fi))
                downloaded = 0
                if file_progress is not None and fi < len(file_progress):
                    downloaded = int(file_progress[fi])
                status = "complete" if size > 0 and downloaded >= size else "downloading"
                progress = float(downloaded / size) if size > 0 else 0.0
                remaining = max(size - downloaded, 0)
                progress_pct = round(progress * 100.0, 2)
                items.append(
                    {
                        "path": path,
                        "file_name": os.path.basename(path),
                        "torrent_name": self.info.name(),
                        "size": size,
                        "downloaded": downloaded,
                        "remaining": remaining,
                        "progress": progress,
                        "progress_pct": progress_pct,
                        "status": status,
                    }
                )
            return items

    def _load_pins(self) -> None:
        try:
            with open(self._pins_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            return

        paths = data.get("paths") if isinstance(data, dict) else data
        if not isinstance(paths, list):
            return

        for path in paths:
            if not isinstance(path, str):
                continue
            try:
                st = self.index.stat(path)
            except Exception:
                continue
            if st.get("type") != "file":
                continue
            fi = int(st["file_index"])
            self._pinned_files.add(fi)
            self._pinned_paths.add(path)
            self.handle.file_priority(fi, 7)

    def _save_pins(self) -> None:
        data = {"paths": sorted(self._pinned_paths)}
        tmp_path = f"{self._pins_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, self._pins_path)

    def read(
        self,
        path: str,
        offset: int,
        size: int,
        mode: str = "auto",
        timeout_s: Optional[float] = None,
    ) -> bytes:
        """
        Read por offset/size: ideal para FUSE e streaming (mpv/vlc).

        mode:
          - "auto": streaming para mídia (ext) e normal para demais
          - "stream": força sequential + prioridades altas
          - "normal": sem sequential (ainda prioriza pieces necessárias)
        timeout_s:
          - None = espera indefinida
          - float = limite de espera por pieces
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

            # Ajusta prioridades e obtém lista de pieces necessárias.
            needed_pieces = self._prioritize_for_read(fi, offset, size, mode=mode)
            rp = self._real_path(fi)

        # Bloqueia até pieces chegarem (para FUSE isso é esperado)
        self._wait_pieces(needed_pieces, deadline_s=timeout_s)

        # Lê do arquivo materializado no cache
        # Observação: o arquivo pode não existir ainda se nenhuma piece foi baixada;
        # mas como esperamos have_piece, normalmente ele já estará criado.
        with open(rp, "rb") as f:
            f.seek(offset)
            return f.read(size)

    def prefetch(self, path: str) -> None:
        with self._lock:
            st = self.index.stat(path)
            if st["type"] != "file":
                raise IsADirectoryError(path)

            fi = int(st["file_index"])
            fsize = int(st["size"])
            ranges = self._prefetch_ranges(self.info.files().file_path(fi), fsize)

            pieces = set()
            for offset, length in ranges:
                for p, _, _ in self._map_file(fi, offset, length):
                    pieces.add(p)

            for p in pieces:
                try:
                    self.handle.piece_priority(p, 6)
                except Exception:
                    pass

    def status(self) -> dict:
        with self._lock:
            s = self.handle.status()
            pieces_total = int(self.info.num_pieces())
            pieces_done = 0
            try:
                pieces_done = sum(1 for p in s.pieces if p)
            except Exception:
                pieces_done = int(round(float(s.progress) * pieces_total)) if pieces_total > 0 else 0
            pieces_missing = max(pieces_total - pieces_done, 0)
            state_str = str(s.state)
            checking = state_str == "checking_files"
            checking_progress = float(s.progress) if checking else None
            return {
                "name": self.info.name(),
                "progress": float(s.progress),
                "peers": int(s.num_peers),
                "seeds": int(getattr(s, "num_seeds", 0)),
                "pieces_total": pieces_total,
                "pieces_done": pieces_done,
                "pieces_missing": pieces_missing,
                "downloaded": int(s.total_download),
                "uploaded": int(s.total_upload),
                "download_rate": int(s.download_rate),
                "upload_rate": int(s.upload_rate),
                "state": state_str,
                "checking": checking,
                "checking_progress": checking_progress,
            }

    def downloading_files(self, max_files: Optional[int] = None) -> List[dict]:
        with self._lock:
            try:
                progress = self.handle.file_progress()
            except Exception:
                return []
            try:
                priorities = list(self.handle.file_priorities())
            except Exception:
                priorities = []

            items = []
            files = self.info.files()
            total_files = files.num_files()
            for fi in range(total_files):
                size = int(files.file_size(fi))
                if size <= 0:
                    continue
                downloaded = int(progress[fi]) if fi < len(progress) else 0
                if downloaded >= size:
                    continue
                prio = priorities[fi] if fi < len(priorities) else 0
                if prio <= 0:
                    continue
                remaining = max(size - downloaded, 0)
                pct = round((downloaded / size) * 100.0, 2) if size > 0 else 0.0
                items.append(
                    {
                        "path": files.file_path(fi),
                        "size": size,
                        "downloaded": downloaded,
                        "remaining": remaining,
                        "progress_pct": pct,
                        "priority": prio,
                    }
                )
                if max_files and len(items) >= max_files:
                    break
            return items

    def reannounce(self) -> None:
        with self._lock:
            try:
                self.handle.force_reannounce()
            except Exception:
                pass
            try:
                self.handle.force_dht_announce()
            except Exception:
                pass

    def shutdown(self) -> None:
        self._resume_stop.set()
        with self._lock:
            self._save_resume_data()
            try:
                self.handle.pause()
            except Exception:
                pass
            try:
                self.ses.remove_torrent(self.handle)
            except Exception:
                pass

    def file_info(self, path: str) -> dict:
        with self._lock:
            st = self.index.stat(path)
            if st["type"] != "file":
                raise IsADirectoryError(path)
            fi = int(st["file_index"])
            size = int(st["size"])
            pieces = set()
            for p, _, _ in self._map_file(fi, 0, size):
                pieces.add(p)
            pieces_total = len(pieces)
            pieces_done = 0
            for p in pieces:
                if self.handle.have_piece(p):
                    pieces_done += 1
            pieces_missing = max(pieces_total - pieces_done, 0)
        return {
            "path": path,
            "size": size,
            "file_index": fi,
            "pieces_total": pieces_total,
            "pieces_done": pieces_done,
            "pieces_missing": pieces_missing,
        }

    def files_completion(self) -> Optional[tuple[int, int]]:
        with self._lock:
            try:
                progress = self.handle.file_progress()
            except Exception:
                return None
            files = self.info.files()
            total_files = files.num_files()
            done = 0
            for fi in range(total_files):
                size = int(files.file_size(fi))
                if size <= 0:
                    done += 1
                    continue
                downloaded = int(progress[fi]) if fi < len(progress) else 0
                if downloaded >= size:
                    done += 1
            return done, total_files

    def config(self) -> dict:
        return {
            "config_path": self._config_path,
            "max_metadata_bytes": self._max_metadata_bytes,
            "prefetch": self._prefetch_cfg,
            "media_extensions": sorted(self._media_exts),
            "prefetch_max_bytes": self._prefetch_max_bytes,
            "skip_check": self._skip_check,
            "resume_save_interval_s": self._resume_save_interval_s,
            "checking_max_active": self._checking_max_active,
        }
