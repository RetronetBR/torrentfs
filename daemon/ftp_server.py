# daemon/ftp_server.py
from __future__ import annotations

import os
import threading
import functools
from typing import List, Optional

from .manager import TorrentManager


def _ensure_exports(exports: List[str], mount_root: Optional[str]) -> List[str]:
    cleaned = []
    for item in exports:
        if not item:
            continue
        cleaned.append(os.path.abspath(item))
    if not cleaned and mount_root:
        cleaned.append(os.path.abspath(mount_root))
    return cleaned


def start_ftp_server(manager: TorrentManager, cfg: dict) -> None:
    ftp_cfg = cfg.get("ftp", {}) if isinstance(cfg, dict) else {}
    if not ftp_cfg or not ftp_cfg.get("enable", False):
        return

    try:
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer
        from pyftpdlib.filesystems import AbstractedFS
    except Exception as e:
        print(f"[torrentfs] ftp indisponivel (pyftpdlib): {e}")
        return

    bind = str(ftp_cfg.get("bind", "0.0.0.0"))
    port = int(ftp_cfg.get("port", 2121))
    mount_root = ftp_cfg.get("mount")
    exports = _ensure_exports(ftp_cfg.get("exports", []) or [], mount_root)
    if not exports:
        print("[torrentfs] ftp habilitado sem exports")
        return

    auto_pin = bool(ftp_cfg.get("auto_pin", True))
    pin_max_files = int(ftp_cfg.get("pin_max_files", 0) or 0)
    pin_depth = int(ftp_cfg.get("pin_depth", -1) or -1)

    export_map = {os.path.basename(p.rstrip(os.sep)): p for p in exports}

    class ExportFS(AbstractedFS):
        def __init__(self, root, cmd_channel):
            super().__init__(root, cmd_channel)

        def _is_allowed(self, real_path: str) -> bool:
            for root in exports:
                if real_path == root or real_path.startswith(root + os.sep):
                    return True
            return False

        def ftp2fs(self, ftppath):
            if ftppath in ("", "/"):
                return "/"
            parts = ftppath.strip("/").split("/")
            head = parts[0]
            if head in export_map:
                return os.path.join(export_map[head], *parts[1:])
            return super().ftp2fs(ftppath)

        def fs2ftp(self, fspath):
            for name, root in export_map.items():
                if fspath == root or fspath.startswith(root + os.sep):
                    rel = os.path.relpath(fspath, root)
                    rel = "" if rel == "." else rel.replace(os.sep, "/")
                    return f"/{name}" + (f"/{rel}" if rel else "")
            return super().fs2ftp(fspath)

        def validpath(self, path):
            if path in ("", "/"):
                return True
            return self._is_allowed(self.ftp2fs(path))

        def listdir(self, path):
            if path in ("", "/"):
                return list(export_map.keys())
            return os.listdir(self.ftp2fs(path))

    authorizer = DummyAuthorizer()
    authorizer.add_anonymous("/", perm="elr")

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.abstracted_fs = functools.partial(ExportFS, "/")
    handler.banner = "TorrentFS FTP (read-only)"

    server = FTPServer((bind, port), handler)

    def _serve():
        print(f"[torrentfs] ftp escutando em {bind}:{port}")
        server.serve_forever(timeout=1, blocking=True)

    threading.Thread(target=_serve, daemon=True).start()

    if auto_pin:
        manager.enqueue_export_pins(exports, mount_root, pin_max_files, pin_depth)
