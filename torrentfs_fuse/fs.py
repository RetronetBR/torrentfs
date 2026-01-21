from __future__ import annotations

import argparse
import errno
import importlib.machinery
import importlib.util
import os
import stat
import sys
import threading
import site
from collections import deque
import time
from typing import Dict, Iterable, Optional, Tuple

from .client import rpc_call_sync


def _load_fusepy():
    """
    Carrega o módulo externo fusepy (também chamado 'fuse') fora do diretório
    do projeto para evitar colisões com módulos locais.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    def _try_import():
        import fuse as module
        mod_path = os.path.abspath(getattr(module, "__file__", ""))
        if mod_path and os.path.abspath(os.path.join(repo_root, "fuse")) not in mod_path:
            return module
        return None

    try:
        module = _try_import()
        if module:
            return module
    except Exception:
        pass

    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.append(user_site)
        try:
            module = _try_import()
            if module:
                return module
        except Exception:
            pass

    alt_paths = [p for p in sys.path if os.path.abspath(p) != repo_root]
    spec = importlib.machinery.PathFinder.find_spec("fuse", alt_paths)
    if spec is None or spec.loader is None:
        raise ImportError("Não foi possível carregar fusepy. Instale com 'pip install fusepy'.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_fuse_mod = _load_fusepy()
FUSE = _fuse_mod.FUSE
FuseOSError = _fuse_mod.FuseOSError
Operations = _fuse_mod.Operations


def _default_uid_gid():
    # Se estiver rodando com sudo, preserva o usuário original.
    uid = int(os.environ.get("SUDO_UID", os.getuid()))
    gid = int(os.environ.get("SUDO_GID", os.getgid()))
    return uid, gid


def _clean_path(path: str) -> str:
    if path in ("", "/"):
        return ""
    return path.lstrip("/")


def _error_from_resp(resp: dict) -> FuseOSError:
    err = resp.get("error", "")
    if err == "FileNotFound":
        return FuseOSError(errno.ENOENT)
    if err == "NotADirectory":
        return FuseOSError(errno.ENOTDIR)
    if err == "IsADirectory":
        return FuseOSError(errno.EISDIR)
    if "Timeout" in err:
        return FuseOSError(errno.EAGAIN)
    return FuseOSError(errno.EIO)


def _is_archive_torrent(tname: str, name: str) -> bool:
    tname = str(tname or "")
    lname = tname.lower()
    if "archive.org" in lname:
        return True
    if lname.endswith("_archive.torrent") or lname.endswith("_archive.torrent.torrent"):
        return True
    base = os.path.splitext(tname)[0].lower() if tname else str(name or "").lower()
    if base.endswith("_archive"):
        return True
    return False


class TorrentFS(Operations):
    _ARCHIVE_GROUP = "Archive.org"
    """
    FUSE read-only mapeando operações para o RPC do daemon.
    """

    def __init__(
        self,
        socket_path: str,
        torrent: Optional[str],
        read_mode: str = "auto",
        timeout_s: float = 5.0,
        stat_ttl: float = 10.0,
        list_ttl: float = 5.0,
        readdir_prefetch: int = 0,
        readdir_prefetch_mode: str = "media",
    ):
        self.socket_path = socket_path
        self.torrent = torrent
        self.read_mode = read_mode
        self.timeout_s = timeout_s
        self._start_time = time.time()
        self._stat_cache: Dict[str, Dict] = {}
        self._stat_ttl = float(stat_ttl)  # segundos
        self._list_cache: Dict[str, Dict] = {}
        self._list_ttl = float(list_ttl)  # segundos
        self._torrents_cache: Dict[str, Dict] = {}
        self._torrents_ttl = 5.0  # segundos
        self._readdir_prefetch = max(0, int(readdir_prefetch))
        self._readdir_prefetch_mode = readdir_prefetch_mode
        self._prefetch_recent: Dict[str, float] = {}
        self._prefetch_recent_ttl = 30.0  # segundos
        self._prefetch_queue = deque()
        self._prefetch_cond = threading.Condition()
        self._prefetch_worker_started = False
        self._prefetch_queue_max = 8

    # ---------------
    # Helpers
    # ---------------
    def _cache_get(self, key: str) -> Optional[Dict]:
        item = self._stat_cache.get(key)
        if not item:
            return None
        ts = item.get("_ts", 0)
        if (time.time() - ts) > self._stat_ttl:
            self._stat_cache.pop(key, None)
            return None
        return {k: v for k, v in item.items() if k != "_ts"}

    def _cache_set(self, key: str, stat_obj: Dict) -> None:
        data = dict(stat_obj)
        data["_ts"] = time.time()
        self._stat_cache[key] = data

    def _list_cache_get(self, key: str) -> Optional[list]:
        item = self._list_cache.get(key)
        if not item:
            return None
        ts = item.get("_ts", 0)
        if (time.time() - ts) > self._list_ttl:
            self._list_cache.pop(key, None)
            return None
        return item.get("entries", None)

    def _list_cache_set(self, key: str, entries: list) -> None:
        self._list_cache[key] = {"_ts": time.time(), "entries": entries}

    def _make_child_path(self, parent: str, name: str) -> str:
        parent = _clean_path(parent)
        if not parent:
            return name
        return f"{parent}/{name}"

    def _cache_key(self, torrent: Optional[str], path: str) -> str:
        t = torrent if torrent else "_root"
        return f"{t}:{_clean_path(path)}"

    def _list_torrents(self):
        cached = self._torrents_cache.get("list")
        if cached:
            ts = cached.get("_ts", 0)
            if (time.time() - ts) <= self._torrents_ttl:
                return cached["items"]

        resp, _ = rpc_call_sync(self.socket_path, {"cmd": "torrents"})
        if not resp.get("ok"):
            raise _error_from_resp(resp)

        torrents = resp.get("torrents", [])
        name_counts: Dict[str, int] = {}
        for t in torrents:
            tname = str(t.get("torrent_name", ""))
            base = os.path.splitext(tname)[0] if tname else str(t.get("name", ""))
            name_counts[base] = name_counts.get(base, 0) + 1

        mapped = []
        for t in torrents:
            tid = str(t.get("id", ""))
            name = str(t.get("name", tid))
            tname = str(t.get("torrent_name", ""))
            base = os.path.splitext(tname)[0] if tname else name
            if name_counts.get(base, 0) <= 1:
                dir_name = base
            else:
                dir_name = f"{base}__{tid}"
            group = self._ARCHIVE_GROUP if _is_archive_torrent(tname, name) else ""
            mapped.append(
                {
                    "id": tid,
                    "name": name,
                    "torrent_name": tname,
                    "dir_name": dir_name,
                    "group": group,
                }
            )

        self._torrents_cache["list"] = {"_ts": time.time(), "items": mapped}
        return mapped

    def _torrent_dir_map(self, group: Optional[str] = None) -> Dict[str, str]:
        out = {}
        for t in self._list_torrents():
            if group and t.get("group") != group:
                continue
            if not group and t.get("group"):
                continue
            out[t["dir_name"]] = t["id"]
        return out

    def _resolve_path(self, path: str) -> Tuple[Optional[str], str, bool]:
        clean = _clean_path(path)
        if self.torrent:
            return self.torrent, clean, clean == ""

        if clean == "":
            return None, "", True

        if clean == self._ARCHIVE_GROUP:
            return None, self._ARCHIVE_GROUP, True

        if clean.startswith(f"{self._ARCHIVE_GROUP}/"):
            parts = clean.split("/", 2)
            dir_name = parts[1] if len(parts) > 1 else ""
            inner = parts[2] if len(parts) > 2 else ""
            tid = self._torrent_dir_map(group=self._ARCHIVE_GROUP).get(dir_name)
            if not tid:
                raise FuseOSError(errno.ENOENT)
            return tid, inner, inner == ""

        parts = clean.split("/", 1)
        dir_name = parts[0]
        inner = parts[1] if len(parts) > 1 else ""
        tid = self._torrent_dir_map().get(dir_name)
        if not tid:
            raise FuseOSError(errno.ENOENT)
        return tid, inner, inner == ""

    def _stat(self, path: str) -> Dict:
        torrent, inner, is_root = self._resolve_path(path)
        if is_root:
            return {
                "type": "dir",
                "size": 0,
            }

        cache_key = self._cache_key(torrent, inner)
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "stat", "torrent": torrent, "path": inner},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        st = resp["stat"]
        self._cache_set(cache_key, st)
        return st

    def _list(self, path: str):
        clean = _clean_path(path)
        if not self.torrent and clean == "":
            items = self._list_torrents()
            entries = [{"name": t["dir_name"], "type": "dir", "size": 0} for t in items if not t.get("group")]
            has_archive = any(t.get("group") == self._ARCHIVE_GROUP for t in items)
            if has_archive:
                entries.append({"name": self._ARCHIVE_GROUP, "type": "dir", "size": 0})
            return entries

        if not self.torrent and clean == self._ARCHIVE_GROUP:
            items = [t for t in self._list_torrents() if t.get("group") == self._ARCHIVE_GROUP]
            return [{"name": t["dir_name"], "type": "dir", "size": 0} for t in items]

        torrent, inner, _ = self._resolve_path(path)
        list_key = self._cache_key(torrent, inner)
        cached = self._list_cache_get(list_key)
        if cached is not None:
            return cached
        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "list", "torrent": torrent, "path": inner},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        entries = resp.get("entries", [])
        self._list_cache_set(list_key, entries)
        # Preenche cache com resultados da listagem.
        for e in entries:
            child_path = self._make_child_path(inner, e["name"])
            cache_key = self._cache_key(torrent, child_path)
            if e["type"] == "dir":
                self._cache_set(cache_key, {"type": "dir", "size": 0})
            else:
                self._cache_set(
                    cache_key,
                    {"type": "file", "size": int(e.get("size", 0))},
                )
        return entries

    def _read(self, path: str, offset: int, size: int):
        torrent, inner, is_root = self._resolve_path(path)
        if is_root and inner == "":
            raise FuseOSError(errno.EISDIR)
        resp, data = rpc_call_sync(
            self.socket_path,
            {
                "cmd": "read",
                "torrent": torrent,
                "path": inner,
                "offset": int(offset),
                "size": int(size),
                "mode": self.read_mode,
                "timeout_s": self.timeout_s,
            },
            want_bytes=True,
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        return data

    def _prefetch(self, path: str) -> None:
        torrent, inner, is_root = self._resolve_path(path)
        if is_root and inner == "":
            raise FuseOSError(errno.EISDIR)
        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "prefetch", "torrent": torrent, "path": inner},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)

    # ---------------
    # FUSE callbacks
    # ---------------
    def getattr(self, path: str, fh=None):
        if path in ("/", ""):
            return {
                "st_mode": stat.S_IFDIR | 0o755,
                "st_nlink": 2,
                "st_ctime": self._start_time,
                "st_mtime": self._start_time,
                "st_atime": self._start_time,
            }

        st = self._stat(path)
        if st["type"] == "dir":
            return {
                "st_mode": stat.S_IFDIR | 0o755,
                "st_nlink": 2,
                "st_ctime": self._start_time,
                "st_mtime": self._start_time,
                "st_atime": self._start_time,
            }

        return {
            "st_mode": stat.S_IFREG | 0o444,
            "st_nlink": 1,
            "st_size": int(st["size"]),
            "st_ctime": self._start_time,
            "st_mtime": self._start_time,
            "st_atime": self._start_time,
        }

    def readdir(self, path: str, fh) -> Iterable[str]:
        entries = self._list(path)
        yield "."
        yield ".."
        for e in entries:
            yield e["name"]
        self._schedule_readdir_prefetch(path, entries)

    def open(self, path: str, flags):
        st = self._stat(path)
        if st["type"] == "dir":
            raise FuseOSError(errno.EISDIR)

        # read-only: rejeita apenas se solicitarem WR ou RDWR
        if flags & os.O_WRONLY or flags & os.O_RDWR:
            raise FuseOSError(errno.EACCES)
        try:
            self._prefetch(path)
        except Exception:
            pass
        return 0

    def read(self, path: str, size: int, offset: int, fh):
        return self._read(path, offset, size)

    def release(self, path: str, fh):
        return 0

    def statfs(self, path: str):
        # Valores fictícios apenas para deixar montável
        return {
            "f_bsize": 4096,
            "f_frsize": 4096,
            "f_blocks": 1,
            "f_bfree": 0,
            "f_bavail": 0,
            "f_files": 1024,
            "f_ffree": 1023,
            "f_favail": 1023,
        }

    def _is_media_name(self, name: str) -> bool:
        ext = os.path.splitext(name)[1].lower()
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
            ".pdf",
            ".epub",
            ".cbz",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
        )

    def _schedule_readdir_prefetch(self, path: str, entries: list) -> None:
        if self._readdir_prefetch <= 0:
            return
        if not entries or path in ("", "/") and not self.torrent:
            return

        with self._prefetch_cond:
            if not self._prefetch_worker_started:
                threading.Thread(target=self._prefetch_worker, daemon=True).start()
                self._prefetch_worker_started = True
            if len(self._prefetch_queue) >= self._prefetch_queue_max:
                return
            self._prefetch_queue.append((path, entries))
            self._prefetch_cond.notify()

    def _prefetch_worker(self) -> None:
        while True:
            with self._prefetch_cond:
                while not self._prefetch_queue:
                    self._prefetch_cond.wait()
                path, entries = self._prefetch_queue.popleft()

            count = 0
            now = time.time()
            files = [e for e in entries if e.get("type") == "file"]

            def sort_key(e):
                name = e.get("name", "")
                lower = name.lower()
                is_pdf = lower.endswith(".pdf")
                is_media = self._is_media_name(name)
                if is_pdf:
                    return (0, name)
                if self._readdir_prefetch_mode == "media" and is_media:
                    return (1, name)
                if self._readdir_prefetch_mode == "all":
                    return (1 if is_media else 2, name)
                return (2, name)

            files.sort(key=sort_key)
            for e in files:
                if count >= self._readdir_prefetch:
                    break
                name = e.get("name", "")
                is_media = self._is_media_name(name)
                if self._readdir_prefetch_mode == "media" and not is_media:
                    continue
                full_path = self._make_child_path(path, name)
                last = self._prefetch_recent.get(full_path, 0)
                if (now - last) < self._prefetch_recent_ttl:
                    continue
                try:
                    self._prefetch(full_path)
                    self._prefetch_recent[full_path] = time.time()
                    count += 1
                except Exception:
                    continue


def main():
    ap = argparse.ArgumentParser("torrentfs-fuse")
    ap.add_argument("--socket", default="/tmp/torrentfsd.sock", help="Socket UNIX do daemon")
    ap.add_argument("--torrent", help="ID ou nome do torrent a montar (se omitido, monta todos)")
    ap.add_argument("--mount", required=True, help="Diretório de mountpoint")
    ap.add_argument(
        "--allow-other",
        action="store_true",
        help="Permite que outros usuários leiam o mount (requer user_allow_other no /etc/fuse.conf)",
    )
    default_uid, default_gid = _default_uid_gid()
    ap.add_argument(
        "--uid",
        type=int,
        default=default_uid,
        help="UID dono dos arquivos no mount (default: UID do usuário chamador ou SUDO_UID).",
    )
    ap.add_argument(
        "--gid",
        type=int,
        default=default_gid,
        help="GID dono dos arquivos no mount (default: GID do usuário chamador ou SUDO_GID).",
    )
    ap.add_argument(
        "--mode",
        choices=["auto", "stream", "normal"],
        default="auto",
        help="Modo de leitura repassado ao daemon",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout (segundos) para leitura de pieces; em caso de falta de peers retorna EAGAIN.",
    )
    ap.add_argument(
        "--stat-ttl",
        type=float,
        default=10.0,
        help="TTL do cache de stat (segundos)",
    )
    ap.add_argument(
        "--list-ttl",
        type=float,
        default=5.0,
        help="TTL do cache de list (segundos)",
    )
    ap.add_argument(
        "--readdir-prefetch",
        type=int,
        default=0,
        help="Prefetch de N arquivos ao listar diretórios (0 = desliga)",
    )
    ap.add_argument(
        "--readdir-prefetch-mode",
        choices=["media", "all"],
        default="media",
        help="Modo do prefetch no readdir",
    )
    ap.add_argument(
        "--foreground",
        action="store_true",
        help="Não daemonizar (útil para debug)",
    )

    args = ap.parse_args()

    if not os.path.isdir(args.mount):
        raise SystemExit(f"Mountpoint inválido: {args.mount}")

    fs = TorrentFS(
        args.socket,
        args.torrent,
        read_mode=args.mode,
        timeout_s=args.timeout,
        stat_ttl=args.stat_ttl,
        list_ttl=args.list_ttl,
        readdir_prefetch=args.readdir_prefetch,
        readdir_prefetch_mode=args.readdir_prefetch_mode,
    )
    FUSE(
        fs,
        args.mount,
        nothreads=False,  # permite processar múltiplas requisições em paralelo (mais responsivo p/ file managers)
        foreground=args.foreground,
        ro=True,
        allow_other=args.allow_other,
        uid=args.uid,
        gid=args.gid,
    )


if __name__ == "__main__":
    main()
