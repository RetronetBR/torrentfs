# daemon/ftp_server.py
from __future__ import annotations

import asyncio
import os
import threading
from typing import Iterable, List, Optional, Tuple

from cli.client import rpc_call


def _ensure_exports(exports: List[str], mount_root: Optional[str]) -> List[str]:
    cleaned = []
    for item in exports:
        if not item:
            continue
        cleaned.append(os.path.abspath(item))
    if not cleaned and mount_root:
        cleaned.append(os.path.abspath(mount_root))
    return cleaned


def _export_map(exports: List[str]) -> dict:
    out = {}
    for export in exports:
        base = os.path.basename(export.rstrip(os.sep))
        path = export
        if base in out:
            suffix = 2
            name = f"{base}-{suffix}"
            while name in out:
                suffix += 1
                name = f"{base}-{suffix}"
            base = name
        out[base] = path
    return out


def _default_socket_path() -> str:
    env = os.environ.get("TORRENTFSD_SOCKET")
    if env:
        return env
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        sock = os.path.join(runtime_dir, "torrentfsd.sock")
        if os.path.exists(sock):
            return sock
    return "/tmp/torrentfsd.sock"


async def _rpc(sock: str, payload: dict):
    return await rpc_call(sock, payload)


async def _list_torrents(sock: str) -> List[dict]:
    resp, _ = await _rpc(sock, {"cmd": "torrents"})
    if not resp.get("ok"):
        return []
    out = []
    for item in resp.get("torrents", []):
        name = str(item.get("name", "") or "")
        tname = str(item.get("torrent_name", "") or "")
        if name or tname:
            out.append({"name": name, "torrent_name": tname})
    return out


def _resolve_exports(
    exports: List[str],
    mount_root: Optional[str],
    torrents: Iterable[dict],
) -> List[Tuple[str, str]]:
    names = {t.get("name"): t.get("name") for t in torrents if t.get("name")}
    torrent_names = {t.get("torrent_name"): t.get("name") for t in torrents if t.get("torrent_name")}
    mount_root_abs = os.path.abspath(mount_root) if mount_root else None
    resolved: List[Tuple[str, str]] = []
    for export in exports:
        exp = os.path.abspath(export)
        name = None
        rel = ""
        if mount_root_abs and exp.startswith(mount_root_abs + os.sep):
            rel = os.path.relpath(exp, mount_root_abs)
            parts = rel.split(os.sep) if rel else []
            if parts:
                if parts[0] in names:
                    name = parts[0]
                elif parts[0] in torrent_names:
                    name = torrent_names.get(parts[0])
                rel = os.path.join(*parts[1:]) if len(parts) > 1 else ""
        if not name:
            parts = exp.split(os.sep)
            for idx, part in enumerate(parts):
                if part in names:
                    name = part
                    rel = os.path.join(*parts[idx + 1 :]) if idx + 1 < len(parts) else ""
                    break
                if part in torrent_names:
                    name = torrent_names.get(part)
                    rel = os.path.join(*parts[idx + 1 :]) if idx + 1 < len(parts) else ""
                    break
        if not name:
            try:
                entries = [
                    entry
                    for entry in os.listdir(exp)
                    if entry in names and os.path.isdir(os.path.join(exp, entry))
                ]
            except Exception:
                entries = []
            if len(entries) == 1:
                name = entries[0]
                rel = ""
        if not name and mount_root_abs and exp == mount_root_abs:
            try:
                entries = [
                    entry
                    for entry in os.listdir(exp)
                    if (entry in names or entry in torrent_names)
                    and os.path.isdir(os.path.join(exp, entry))
                ]
            except Exception:
                entries = []
            if len(entries) == 1:
                entry = entries[0]
                name = entry if entry in names else torrent_names.get(entry)
                rel = ""
        if name:
            resolved.append((name, rel))
        else:
            print(f"[torrentfs] ftp export ignorado (nao encontrado): {export}")
    return resolved


async def _walk_and_pin(
    sock: str,
    torrent: str,
    path: str,
    max_files: int,
    max_depth: int,
    depth: int = 0,
) -> Tuple[int, int]:
    pinned = 0
    errors = 0
    if max_files > 0 and pinned >= max_files:
        return pinned, errors
    resp, _ = await _rpc(sock, {"cmd": "list", "torrent": torrent, "path": path})
    if not resp.get("ok"):
        return pinned, errors + 1
    entries = resp.get("entries", [])
    for entry in entries:
        if max_files > 0 and pinned >= max_files:
            break
        name = entry.get("name")
        if not name:
            continue
        etype = entry.get("type")
        child = f"{path}/{name}" if path else name
        if etype == "dir":
            if max_depth >= 0 and depth >= max_depth:
                continue
            p, e = await _walk_and_pin(sock, torrent, child, max_files, max_depth, depth + 1)
            pinned += p
            errors += e
        else:
            resp, _ = await _rpc(sock, {"cmd": "pin", "torrent": torrent, "path": child})
            if resp.get("ok"):
                pinned += 1
            else:
                errors += 1
    return pinned, errors


async def _pin_exports(
    sock: str,
    exports: List[str],
    mount_root: Optional[str],
    max_files: int,
    max_depth: int,
) -> None:
    try:
        torrents = await _list_torrents(sock)
    except Exception as e:
        print(f"[torrentfs] ftp auto-pin ignorado: daemon indisponivel ({e})")
        return
    if not torrents:
        print("[torrentfs] ftp auto-pin ignorado: nenhum torrent carregado")
        return
    resolved = _resolve_exports(exports, mount_root, torrents)
    for name, rel in resolved:
        if mount_root:
            print(
                "[torrentfs] ftp export em FUSE: o torrent sera pinado para uso no FTP"
            )
        pinned, errors = await _walk_and_pin(sock, name, rel, max_files, max_depth, 0)
        print(
            f"[torrentfs] ftp pin concluido: {name} path={rel or '/'} pinned={pinned} errors={errors}"
        )


def start_ftp_server(cfg: dict, socket_path: Optional[str] = None) -> None:
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
    valid_exports = []
    for export in exports:
        if not os.path.isdir(export):
            print(f"[torrentfs] ftp export ignorado (path invalido): {export}")
            continue
        valid_exports.append(export)
    exports = valid_exports
    if not exports:
        print("[torrentfs] ftp habilitado sem exports")
        return

    auto_pin = bool(ftp_cfg.get("auto_pin", True))
    pin_max_files = int(ftp_cfg.get("pin_max_files", 0) or 0)
    pin_depth = int(ftp_cfg.get("pin_depth", -1) or -1)
    export_map = _export_map(exports)
    if export_map:
        for name, path in export_map.items():
            print(f"[torrentfs] ftp export: /{name} -> {path}")

    class ExportFS(AbstractedFS):
        def __init__(self, root, cmd_channel, *args, **kwargs):
            super().__init__("/", cmd_channel)

        def _map_virtual(self, path: str) -> Optional[str]:
            if path in ("", "/", "."):
                return None
            clean = path.strip("/")
            if not clean:
                return None
            head = clean.split("/")[0]
            if head in export_map:
                return os.path.join(export_map[head], *clean.split("/")[1:])
            return None

        def _is_allowed(self, real_path: str) -> bool:
            for root in exports:
                if real_path == root or real_path.startswith(root + os.sep):
                    return True
            return False

        def ftp2fs(self, ftppath):
            if ftppath in ("", "/", "."):
                return "/"
            path = ftppath
            if not path.startswith("/"):
                cwd = getattr(self, "cwd", "/") or "/"
                if cwd == "/":
                    path = f"/{path}"
                else:
                    path = f"{cwd.rstrip('/')}/{path}"
            if path.startswith("/") and any(path.startswith(root) for root in exports):
                return path
            parts = path.strip("/").split("/")
            head = parts[0]
            if head in export_map:
                return os.path.join(export_map[head], *parts[1:])
            return super().ftp2fs(path)

        def fs2ftp(self, fspath):
            for name, root in export_map.items():
                if fspath == root or fspath.startswith(root + os.sep):
                    rel = os.path.relpath(fspath, root)
                    rel = "" if rel == "." else rel.replace(os.sep, "/")
                    return f"/{name}" + (f"/{rel}" if rel else "")
            return super().fs2ftp(fspath)

        def validpath(self, path):
            if path in ("", "/", "."):
                return True
            return self._is_allowed(self.ftp2fs(path))

        def listdir(self, path):
            if path in ("", "/", "."):
                try:
                    print(f"[torrentfs] ftp listdir root (path={path}) exports={list(export_map.keys())}")
                except Exception:
                    pass
                return list(export_map.keys())
            return os.listdir(self.ftp2fs(path))

        def stat(self, path):
            mapped = self._map_virtual(path)
            if mapped:
                return os.stat(mapped)
            if path in ("", "/", "."):
                return os.stat("/")
            return super().stat(path)

        def lstat(self, path):
            mapped = self._map_virtual(path)
            if mapped:
                return os.lstat(mapped)
            if path in ("", "/", "."):
                return os.lstat("/")
            return super().lstat(path)

    authorizer = DummyAuthorizer()
    authorizer.add_anonymous("/", perm="elr")

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.abstracted_fs = ExportFS
    handler.banner = "TorrentFS FTP (read-only)"

    server = FTPServer((bind, port), handler)

    def _serve():
        print(f"[torrentfs] ftp escutando em {bind}:{port}")
        server.serve_forever(timeout=1, blocking=True)

    threading.Thread(target=_serve, daemon=True).start()

    if auto_pin:
        sock = socket_path or _default_socket_path()
        threading.Thread(
            target=lambda: asyncio.run(_pin_exports(sock, exports, mount_root, pin_max_files, pin_depth)),
            daemon=True,
        ).start()
