from __future__ import annotations

import argparse
import errno
import importlib.machinery
import importlib.util
import os
import stat
import sys
import time
from typing import Dict, Iterable, Optional

from fuse.client import rpc_call_sync


def _load_fusepy():
    """
    Evita colisão com este pacote local chamado 'fuse', carregando o módulo
    externo fusepy (também chamado 'fuse') fora do diretório do projeto.
    """
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    alt_paths = [p for p in sys.path if os.path.abspath(p) != here]
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


class TorrentFS(Operations):
    """
    FUSE read-only mapeando operações para o RPC do daemon.
    """

    def __init__(self, socket_path: str, torrent: str, read_mode: str = "auto", timeout_s: float = 5.0):
        self.socket_path = socket_path
        self.torrent = torrent
        self.read_mode = read_mode
        self.timeout_s = timeout_s
        self._start_time = time.time()
        self._stat_cache: Dict[str, Dict] = {}
        self._stat_ttl = 10.0  # segundos

    # ---------------
    # Helpers
    # ---------------
    def _cache_get(self, path: str) -> Optional[Dict]:
        item = self._stat_cache.get(path)
        if not item:
            return None
        ts = item.get("_ts", 0)
        if (time.time() - ts) > self._stat_ttl:
            self._stat_cache.pop(path, None)
            return None
        return {k: v for k, v in item.items() if k != "_ts"}

    def _cache_set(self, path: str, stat_obj: Dict) -> None:
        data = dict(stat_obj)
        data["_ts"] = time.time()
        self._stat_cache[path] = data

    def _make_child_path(self, parent: str, name: str) -> str:
        parent = _clean_path(parent)
        if not parent:
            return name
        return f"{parent}/{name}"

    def _stat(self, path: str) -> Dict:
        if path in ("", "/"):
            return {
                "type": "dir",
                "size": 0,
            }

        cached = self._cache_get(path)
        if cached:
            return cached

        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "stat", "torrent": self.torrent, "path": _clean_path(path)},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        st = resp["stat"]
        self._cache_set(path, st)
        return st

    def _list(self, path: str):
        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "list", "torrent": self.torrent, "path": _clean_path(path)},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        entries = resp.get("entries", [])
        # Preenche cache com resultados da listagem.
        for e in entries:
            child_path = self._make_child_path(path, e["name"])
            if e["type"] == "dir":
                self._cache_set(child_path, {"type": "dir", "size": 0})
            else:
                self._cache_set(
                    child_path,
                    {"type": "file", "size": int(e.get("size", 0))},
                )
        return entries

    def _read(self, path: str, offset: int, size: int):
        resp, data = rpc_call_sync(
            self.socket_path,
            {
                "cmd": "read",
                "torrent": self.torrent,
                "path": _clean_path(path),
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

    def open(self, path: str, flags):
        st = self._stat(path)
        if st["type"] == "dir":
            raise FuseOSError(errno.EISDIR)

        # read-only: rejeita apenas se solicitarem WR ou RDWR
        if flags & os.O_WRONLY or flags & os.O_RDWR:
            raise FuseOSError(errno.EACCES)
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


def main():
    ap = argparse.ArgumentParser("torrentfs-fuse")
    ap.add_argument("--socket", default="/tmp/torrentfsd.sock", help="Socket UNIX do daemon")
    ap.add_argument("--torrent", required=True, help="ID ou nome do torrent a montar")
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
        "--foreground",
        action="store_true",
        help="Não daemonizar (útil para debug)",
    )

    args = ap.parse_args()

    if not os.path.isdir(args.mount):
        raise SystemExit(f"Mountpoint inválido: {args.mount}")

    fs = TorrentFS(args.socket, args.torrent, read_mode=args.mode, timeout_s=args.timeout)
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
