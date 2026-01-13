from __future__ import annotations

import argparse
import errno
import importlib.machinery
import importlib.util
import os
import stat
import sys
import time
from typing import Dict, Iterable

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
    return FuseOSError(errno.EIO)


class TorrentFS(Operations):
    """
    FUSE read-only mapeando operações para o RPC do daemon.
    """

    def __init__(self, socket_path: str, torrent: str, read_mode: str = "auto"):
        self.socket_path = socket_path
        self.torrent = torrent
        self.read_mode = read_mode
        self._start_time = time.time()

    # ---------------
    # Helpers
    # ---------------
    def _stat(self, path: str) -> Dict:
        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "stat", "torrent": self.torrent, "path": _clean_path(path)},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        return resp["stat"]

    def _list(self, path: str):
        resp, _ = rpc_call_sync(
            self.socket_path,
            {"cmd": "list", "torrent": self.torrent, "path": _clean_path(path)},
        )
        if not resp.get("ok"):
            raise _error_from_resp(resp)
        return resp.get("entries", [])

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
        "--foreground",
        action="store_true",
        help="Não daemonizar (útil para debug)",
    )

    args = ap.parse_args()

    if not os.path.isdir(args.mount):
        raise SystemExit(f"Mountpoint inválido: {args.mount}")

    fs = TorrentFS(args.socket, args.torrent, read_mode=args.mode)
    FUSE(
        fs,
        args.mount,
        nothreads=True,
        foreground=args.foreground,
        ro=True,
        allow_other=args.allow_other,
        uid=args.uid,
        gid=args.gid,
    )


if __name__ == "__main__":
    main()
