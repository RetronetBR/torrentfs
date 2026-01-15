# cli/main.py
import argparse
import asyncio
import math
import os
import json
import sys
import time

from cli.client import rpc_call


async def get_default_torrent(socket, explicit=None):
    if explicit:
        return explicit

    resp, _ = await rpc_call(socket, {"cmd": "torrents"})
    if not resp.get("ok"):
        print(json.dumps(resp, indent=2), file=sys.stderr)
        sys.exit(1)

    torrents = resp.get("torrents", [])

    if not torrents:
        print("Nenhum torrent carregado no daemon", file=sys.stderr)
        sys.exit(1)

    if len(torrents) == 1:
        return torrents[0]["id"]

    print("Mais de um torrent carregado. Use --torrent.", file=sys.stderr)
    for t in torrents:
        print(f" - {t['name']} ({t['id']})", file=sys.stderr)
    sys.exit(1)


def _build_torrent_dir_map(torrents):
    name_counts = {}
    for t in torrents:
        tname = str(t.get("torrent_name", ""))
        base = os.path.splitext(tname)[0] if tname else str(t.get("name", ""))
        name_counts[base] = name_counts.get(base, 0) + 1
    out = {}
    for t in torrents:
        tid = str(t.get("id", ""))
        name = str(t.get("name", tid))
        tname = str(t.get("torrent_name", ""))
        base = os.path.splitext(tname)[0] if tname else name
        if name_counts.get(base, 0) <= 1:
            dir_name = base
        else:
            dir_name = f"{base}__{tid}"
        out[dir_name] = tid
    return out


def _normalize_path(path: str) -> str:
    if path in ("", "."):
        return ""
    return path.replace(os.sep, "/")


def main():
    ap = argparse.ArgumentParser("torrentfs")
    ap.add_argument("--socket", default="/tmp/torrentfsd.sock")
    ap.add_argument("--torrent", help="Nome ou ID do torrent")
    ap.add_argument(
        "--mount",
        help="Mountpoint do FUSE para resolver paths do filesystem",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Saida em JSON (default: texto simples)",
    )

    sub = ap.add_subparsers(dest="cmd", required=True)

    # -----------------------------
    # torrents
    # -----------------------------
    sub.add_parser("torrents", help="Listar torrents carregados")

    # -----------------------------
    # config
    # -----------------------------
    sub.add_parser("config", help="Mostrar configuracao efetiva do daemon")

    # -----------------------------
    # cache-size
    # -----------------------------
    sub.add_parser("cache-size", help="Tamanho total do cache")

    # -----------------------------
    # status
    # -----------------------------
    p_status = sub.add_parser("status")
    p_status.add_argument("--unit", choices=["bytes", "kb", "mb", "gb"], default="bytes")
    p_status.add_argument(
        "--human",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibe tamanhos e taxas em formato legivel",
    )

    # -----------------------------
    # status-all
    # -----------------------------
    p_status_all = sub.add_parser("status-all", help="Resumo global de todos os torrents")
    p_status_all.add_argument("--unit", choices=["bytes", "kb", "mb", "gb"], default="bytes")
    p_status_all.add_argument(
        "--human",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibe tamanhos e taxas em formato legivel",
    )

    # -----------------------------
    # downloads
    # -----------------------------
    p_downloads = sub.add_parser("downloads", help="Listar downloads em execucao")
    p_downloads.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos por torrent (0 = sem limite)",
    )

    # -----------------------------
    # reannounce
    # -----------------------------
    sub.add_parser("reannounce", help="Forcar announce do tracker/DHT")

    # -----------------------------
    # reannounce-all
    # -----------------------------
    sub.add_parser("reannounce-all", help="Forcar announce em todos os torrents")

    # -----------------------------
    # file-info
    # -----------------------------
    p_file_info = sub.add_parser("file-info", help="Info de pieces de um arquivo")
    p_file_info.add_argument("path")

    # -----------------------------
    # prefetch-info
    # -----------------------------
    p_prefetch_info = sub.add_parser("prefetch-info", help="Info de prefetch de um arquivo")
    p_prefetch_info.add_argument("path")

    # -----------------------------
    # ls
    # -----------------------------
    p_ls = sub.add_parser("ls")
    p_ls.add_argument("path", nargs="?", default="")

    # -----------------------------
    # cat
    # -----------------------------
    p_cat = sub.add_parser("cat")
    p_cat.add_argument("path")
    p_cat.add_argument("--size", type=int, default=65536)
    p_cat.add_argument("--offset", type=int, default=0)
    p_cat.add_argument("--mode", default="auto")

    # -----------------------------
    # pin
    # -----------------------------
    p_pin = sub.add_parser("pin")
    p_pin.add_argument("path")

    # -----------------------------
    # cp
    # -----------------------------
    p_cp = sub.add_parser("cp", help="Copiar do mount para o disco local")
    p_cp.add_argument("src")
    p_cp.add_argument("dest")
    p_cp.add_argument(
        "--chunk-size",
        type=int,
        default=1024 * 1024,
        help="Tamanho do bloco de leitura (bytes)",
    )
    p_cp.add_argument(
        "--read-timeout",
        type=float,
        default=1.0,
        help="Timeout de leitura por bloco (segundos). Use 0 para esperar indefinidamente.",
    )
    p_cp.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibe progresso e ETA no stderr",
    )
    p_cp.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_cp.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # du
    # -----------------------------
    p_du = sub.add_parser("du", help="Somar tamanho dos arquivos por path")
    p_du.add_argument("path", nargs="?", default="")
    p_du.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # pin-dir
    # -----------------------------
    p_pin_dir = sub.add_parser("pin-dir", help="Pinar todos os arquivos de um diretório")
    p_pin_dir.add_argument("path")
    p_pin_dir.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_pin_dir.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # unpin
    # -----------------------------
    p_unpin = sub.add_parser("unpin")
    p_unpin.add_argument("path")

    # -----------------------------
    # unpin-dir
    # -----------------------------
    p_unpin_dir = sub.add_parser("unpin-dir", help="Despinar todos os arquivos de um diretório")
    p_unpin_dir.add_argument("path")
    p_unpin_dir.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_unpin_dir.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # pinned
    # -----------------------------
    sub.add_parser("pinned", help="Listar arquivos pinados")

    # -----------------------------
    # prefetch
    # -----------------------------
    p_prefetch = sub.add_parser("prefetch", help="Pré-cache de arquivo ou diretório")
    p_prefetch.add_argument("path")
    p_prefetch.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos a prefetchar (0 = sem limite)",
    )
    p_prefetch.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    args = ap.parse_args()

    async def run():
        def _print_json(obj):
            print(json.dumps(obj, indent=2, ensure_ascii=False))

        def _print_error(msg):
            print(f"erro: {msg}", file=sys.stderr)

        def _print_ok(msg: str):
            print(msg)

        def _fmt_bytes(value: float) -> str:
            units = ["B", "KiB", "MiB", "GiB", "TiB"]
            v = float(value)
            idx = 0
            while v >= 1024.0 and idx < len(units) - 1:
                v /= 1024.0
                idx += 1
            return f"{v:.2f} {units[idx]}"

        def _fmt_rate(value: float) -> str:
            return f"{_fmt_bytes(value)}/s"

        def _print_status_all(resp):
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter status global"))
                return
            totals = resp.get("totals", {})
            torrents = resp.get("torrents", [])
            if not args.human and args.unit != "bytes":
                divisors = {
                    "kb": 1024,
                    "mb": 1024 * 1024,
                    "gb": 1024 * 1024 * 1024,
                }
                d = divisors[args.unit]
                for key in ("downloaded", "uploaded", "download_rate", "upload_rate"):
                    totals[key] = totals.get(key, 0) / d
            if args.human:
                totals["downloaded"] = _fmt_bytes(totals.get("downloaded", 0))
                totals["uploaded"] = _fmt_bytes(totals.get("uploaded", 0))
                totals["download_rate"] = _fmt_rate(totals.get("download_rate", 0))
                totals["upload_rate"] = _fmt_rate(totals.get("upload_rate", 0))
            print(
                "totals: "
                f"downloaded={totals.get('downloaded')} "
                f"uploaded={totals.get('uploaded')} "
                f"download_rate={totals.get('download_rate')} "
                f"upload_rate={totals.get('upload_rate')} "
                f"peers={totals.get('peers')} "
                f"seeds={totals.get('seeds')}"
            )
            for item in torrents:
                tid = item.get("id", "")
                st = item.get("status", {})
                name = st.get("name", "")
                peers = st.get("peers", 0)
                seeds = st.get("seeds", 0)
                progress = st.get("progress", 0)
                if st.get("checking"):
                    chk = st.get("checking_progress")
                    print(f"{tid}\t{name}\tchecking={chk}\tpeers={peers}\tseeds={seeds}\tprogress={progress}")
                else:
                    print(f"{tid}\t{name}\tpeers={peers}\tseeds={seeds}\tprogress={progress}")

        # -----------------------------
        # torrents
        # -----------------------------
        if args.cmd == "torrents":
            resp, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar torrents"))
                return
            for t in resp.get("torrents", []):
                tid = t.get("id", "")
                name = t.get("name", "")
                tname = t.get("torrent_name", "")
                cache = t.get("cache", "")
                print(f"{tid}\t{name}\t{tname}\t{cache}")
            return

        if args.cmd == "config":
            resp, _ = await rpc_call(args.socket, {"cmd": "config"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao ler config"))
                return
            cfg = resp.get("config", {})
            print(f"config_path: {cfg.get('config_path', '')}")
            print(f"max_metadata_bytes: {cfg.get('max_metadata_bytes', '')}")
            pf = cfg.get("prefetch", {})
            print("prefetch.media:")
            for k, v in pf.get("media", {}).items():
                print(f"  {k}: {v}")
            print("prefetch.other:")
            for k, v in pf.get("other", {}).items():
                print(f"  {k}: {v}")
            return

        if args.cmd == "cache-size":
            resp, _ = await rpc_call(args.socket, {"cmd": "cache-size"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter cache"))
                return
            logical = resp.get("logical_bytes", 0)
            disk = resp.get("disk_bytes", 0)
            print(f"cache_logical: {_fmt_bytes(logical)}")
            print(f"cache_disk: {_fmt_bytes(disk)}")
            return

        if args.cmd == "status-all":
            resp, _ = await rpc_call(args.socket, {"cmd": "status-all"})
            _print_status_all(resp)
            return

        if args.cmd == "downloads":
            max_files = int(args.max_files)
            payload = {"cmd": "downloads"}
            if max_files > 0:
                payload["max_files"] = max_files
            resp, _ = await rpc_call(args.socket, payload)
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter downloads"))
                return
            torrents = resp.get("torrents", [])
            for item in torrents:
                tid = item.get("id", "")
                st = item.get("status", {})
                name = st.get("name", "")
                peers = st.get("peers", 0)
                seeds = st.get("seeds", 0)
                pieces_done = st.get("pieces_done", 0)
                pieces_total = st.get("pieces_total", 0)
                pieces_missing = st.get("pieces_missing", 0)
                progress = st.get("progress", 0)
                rate = st.get("download_rate", 0)
                print(
                    f"{tid}\t{name}\tpieces={pieces_done}/{pieces_total}\tmissing={pieces_missing}\t"
                    f"rate={rate}\tpeers={peers}\tseeds={seeds}\tprogress={progress}"
                )
                for f in item.get("files", []):
                    fpath = f.get("path", "")
                    pct = f.get("progress_pct", 0.0)
                    remaining = f.get("remaining", 0)
                    size = f.get("size", 0)
                    print(f"  file\t{pct:.2f}%\t{remaining}/{size}\t{fpath}")
            return

        if args.cmd == "reannounce-all":
            resp, _ = await rpc_call(args.socket, {"cmd": "reannounce-all"})
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("reannounce-all ok")
                else:
                    _print_error(resp.get("error", "falha ao reannounce-all"))
            return

        async def _resolve_mount_path(path: str, torrent_hint: str | None):
            if not path:
                return torrent_hint, path

            abs_mount = os.path.abspath(args.mount) if args.mount else None
            abs_path = path
            if not os.path.isabs(abs_path):
                abs_path = os.path.abspath(os.path.join(os.getcwd(), path))

            if abs_mount:
                mount_prefix = abs_mount.rstrip(os.sep) + os.sep
                if abs_path != abs_mount and not abs_path.startswith(mount_prefix):
                    return torrent_hint, path

            resp, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            if not resp.get("ok"):
                return torrent_hint, path
            dir_map = _build_torrent_dir_map(resp.get("torrents", []))

            if abs_mount:
                rel = os.path.relpath(abs_path, abs_mount)
                if rel == ".":
                    rel = ""
                if torrent_hint:
                    return torrent_hint, _normalize_path(rel)

                parts = rel.split(os.sep) if rel else []
                if parts and parts[0] in dir_map:
                    tid = dir_map[parts[0]]
                    inner = os.path.join(*parts[1:]) if len(parts) > 1 else ""
                    return tid, _normalize_path(inner)
                return None, _normalize_path(rel)

            # Sem --mount: tenta inferir pelo nome do torrent no caminho absoluto.
            parts = abs_path.split(os.sep)
            for idx, part in enumerate(parts):
                if part in dir_map:
                    tid = dir_map[part]
                    inner = os.path.join(*parts[idx + 1 :]) if idx + 1 < len(parts) else ""
                    return tid, _normalize_path(inner)

            return torrent_hint, path

        if args.cmd == "status" and not args.torrent:
            resp, _ = await rpc_call(args.socket, {"cmd": "status-all"})
            _print_status_all(resp)
            return

        # -----------------------------
        # comandos que exigem torrent
        # -----------------------------
        path_cmds = {"ls", "cat", "pin", "pin-dir", "unpin", "unpin-dir", "prefetch", "du", "file-info", "prefetch-info"}
        src_cmds = {"cp"}
        torrent = args.torrent
        if args.cmd in path_cmds:
            torrent, args.path = await _resolve_mount_path(args.path, torrent)
        if args.cmd in src_cmds:
            torrent, args.src = await _resolve_mount_path(args.src, torrent)

        torrent = await get_default_torrent(args.socket, torrent)

        async def _walk_and_apply(path: str, max_files: int, max_depth: int, apply_fn):
            applied = 0
            errors = []

            def _join_path(parent: str, name: str) -> str:
                if parent in ("", "/"):
                    return name
                if parent.endswith("/"):
                    return f"{parent}{name}"
                return f"{parent}/{name}"

            async def _apply_file(path: str) -> None:
                nonlocal applied
                if max_files > 0 and applied >= max_files:
                    return
                resp, _ = await apply_fn(path)
                if resp.get("ok"):
                    applied += 1
                else:
                    errors.append({"path": path, "error": resp.get("error")})

            async def _walk(path: str, depth: int) -> None:
                if max_files > 0 and applied >= max_files:
                    return
                resp, _ = await rpc_call(
                    args.socket,
                    {"cmd": "stat", "torrent": torrent, "path": path},
                )
                if not resp.get("ok"):
                    errors.append({"path": path, "error": resp.get("error")})
                    return

                st = resp.get("stat", {})
                if st.get("type") == "dir":
                    resp, _ = await rpc_call(
                        args.socket,
                        {"cmd": "list", "torrent": torrent, "path": path},
                    )
                    if not resp.get("ok"):
                        errors.append({"path": path, "error": resp.get("error")})
                        return
                    entries = resp.get("entries", [])
                    for e in entries:
                        if max_files > 0 and applied >= max_files:
                            return
                        child = _join_path(path, e.get("name", ""))
                        if e.get("type") == "dir":
                            if max_depth >= 0 and depth >= max_depth:
                                continue
                            await _walk(child, depth + 1)
                        else:
                            await _apply_file(child)
                    return

                await _apply_file(path)

            await _walk(path, 0)
            return applied, errors

        async def _walk_files(path: str, max_files: int, max_depth: int):
            files = []
            errors = []

            def _join_path(parent: str, name: str) -> str:
                if parent in ("", "/"):
                    return name
                if parent.endswith("/"):
                    return f"{parent}{name}"
                return f"{parent}/{name}"

            async def _walk(path: str, depth: int) -> None:
                if max_files > 0 and len(files) >= max_files:
                    return
                resp, _ = await rpc_call(
                    args.socket,
                    {"cmd": "stat", "torrent": torrent, "path": path},
                )
                if not resp.get("ok"):
                    errors.append({"path": path, "error": resp.get("error")})
                    return

                st = resp.get("stat", {})
                if st.get("type") == "dir":
                    if max_depth >= 0 and depth >= max_depth:
                        return
                    resp, _ = await rpc_call(
                        args.socket,
                        {"cmd": "list", "torrent": torrent, "path": path},
                    )
                    if not resp.get("ok"):
                        errors.append({"path": path, "error": resp.get("error")})
                        return
                    entries = resp.get("entries", [])
                    for e in entries:
                        if max_files > 0 and len(files) >= max_files:
                            return
                        child = _join_path(path, e.get("name", ""))
                        if e.get("type") == "dir":
                            await _walk(child, depth + 1)
                        else:
                            files.append(
                                {
                                    "path": child,
                                    "size": int(e.get("size", 0)),
                                }
                            )
                    return

                files.append({"path": path, "size": int(st.get("size", 0))})

            await _walk(path, 0)
            return files, errors

        if args.cmd == "status":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "status", "torrent": torrent},
            )
            if resp.get("ok") and not args.human and args.unit != "bytes":
                st = resp.get("status", {})
                divisors = {
                    "kb": 1024,
                    "mb": 1024 * 1024,
                    "gb": 1024 * 1024 * 1024,
                }
                d = divisors[args.unit]
                st["downloaded"] = st.get("downloaded", 0) / d
                st["uploaded"] = st.get("uploaded", 0) / d
                st["download_rate"] = st.get("download_rate", 0) / d
                st["upload_rate"] = st.get("upload_rate", 0) / d
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter status"))
                return
            st = resp.get("status", {})
            if args.human:
                st["downloaded"] = _fmt_bytes(st.get("downloaded", 0))
                st["uploaded"] = _fmt_bytes(st.get("uploaded", 0))
                st["download_rate"] = _fmt_rate(st.get("download_rate", 0))
                st["upload_rate"] = _fmt_rate(st.get("upload_rate", 0))
            for key in (
                "name",
                "state",
                "progress",
                "peers",
                "seeds",
                "downloaded",
                "uploaded",
                "download_rate",
                "upload_rate",
            ):
                print(f"{key}: {st.get(key)}")
            if st.get("checking"):
                print(f"checking_progress: {st.get('checking_progress')}")

        elif args.cmd == "reannounce":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "reannounce", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("reannounce ok")
                else:
                    _print_error(resp.get("error", "falha ao reannounce"))

        elif args.cmd == "file-info":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "file-info", "torrent": torrent, "path": args.path},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter info"))
                return
            info = resp.get("info", {})
            print(f"path: {info.get('path')}")
            print(f"size: {info.get('size')}")
            print(f"file_index: {info.get('file_index')}")
            print(f"pieces_total: {info.get('pieces_total')}")
            print(f"pieces_done: {info.get('pieces_done')}")
            print(f"pieces_missing: {info.get('pieces_missing')}")

        elif args.cmd == "prefetch-info":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "prefetch-info", "torrent": torrent, "path": args.path},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter prefetch info"))
                return
            info = resp.get("info", {})
            print(f"path: {info.get('path')}")
            print(f"size: {info.get('size')}")
            print(f"prefetch_bytes: {info.get('prefetch_bytes')}")
            print(f"prefetch_pieces: {info.get('prefetch_pieces')}")
            print(f"prefetch_pct: {info.get('prefetch_pct')}")
            ranges = info.get("ranges", [])
            if ranges:
                print("ranges:")
                for r in ranges:
                    print(f"  offset={r.get('offset')} length={r.get('length')}")

        elif args.cmd == "ls":
            resp, _ = await rpc_call(
                args.socket,
                {
                    "cmd": "list",
                    "torrent": torrent,
                    "path": args.path,
                },
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar"))
                return
            for e in resp.get("entries", []):
                etype = e.get("type", "")
                size = e.get("size", 0)
                name = e.get("name", "")
                print(f"{etype}\t{size}\t{name}")

        elif args.cmd == "cat":
            resp, data = await rpc_call(
                args.socket,
                {
                    "cmd": "read",
                    "torrent": torrent,
                    "path": args.path,
                    "offset": args.offset,
                    "size": args.size,
                    "mode": args.mode,
                },
                want_bytes=True,
            )
            if not resp.get("ok"):
                if args.json:
                    _print_json(resp)
                else:
                    _print_error(resp.get("error", "falha ao ler arquivo"))
                return
            os.write(1, data)

        elif args.cmd == "pin":
            resp, _ = await rpc_call(
                args.socket,
                {
                    "cmd": "pin",
                    "torrent": torrent,
                    "path": args.path,
                },
            )
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("pin ok")
                else:
                    _print_error(resp.get("error", "falha ao pinar"))

        elif args.cmd == "pin-dir":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _pin(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "pin", "torrent": torrent, "path": path},
                )

            pinned, errors = await _walk_and_apply(args.path, max_files, max_depth, _pin)
            out = {"ok": len(errors) == 0, "pinned": pinned, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"pinned: {pinned} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "unpin":
            resp, _ = await rpc_call(
                args.socket,
                {
                    "cmd": "unpin",
                    "torrent": torrent,
                    "path": args.path,
                },
            )
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("unpin ok")
                else:
                    _print_error(resp.get("error", "falha ao despinar"))

        elif args.cmd == "unpin-dir":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _unpin(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "unpin", "torrent": torrent, "path": path},
                )

            unpinned, errors = await _walk_and_apply(args.path, max_files, max_depth, _unpin)
            out = {"ok": len(errors) == 0, "unpinned": unpinned, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"unpinned: {unpinned} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "pinned":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "pinned", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar pins"))
                return
            for p in resp.get("pins", []):
                status = p.get("status", "")
                pct = p.get("progress_pct", 0)
                size = p.get("size", 0)
                path = p.get("path", "")
                print(f"{status}\t{pct:.2f}%\t{size}\t{path}")

        elif args.cmd == "prefetch":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _prefetch(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "prefetch", "torrent": torrent, "path": path},
                )

            prefetched, errors = await _walk_and_apply(args.path, max_files, max_depth, _prefetch)
            out = {"ok": len(errors) == 0, "prefetched": prefetched, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"prefetched: {prefetched} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "du":
            max_depth = int(args.depth)
            files, errors = await _walk_files(args.path, 0, max_depth)
            total = sum(f.get("size", 0) for f in files)
            out = {
                "ok": len(errors) == 0,
                "path": args.path,
                "total_bytes": total,
                "files": len(files),
                "errors": errors,
            }
            if args.json:
                _print_json(out)
            else:
                print(f"ok: {out['ok']}")
                print(f"path: {out['path']}")
                print(f"total_bytes: {out['total_bytes']}")
                print(f"files: {out['files']}")
                if errors:
                    print("errors:")
                    for err in errors:
                        _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "cp":
            max_files = int(args.max_files)
            max_depth = int(args.depth)
            chunk_size = int(args.chunk_size)
            if chunk_size <= 0:
                if args.json:
                    _print_json({"ok": False, "error": "chunk-size invalido"})
                else:
                    _print_error("chunk-size invalido")
                return
            show_progress = bool(args.progress)
            read_timeout = float(args.read_timeout)
            if read_timeout <= 0:
                read_timeout = None

            def _format_eta(seconds: float) -> str:
                if seconds < 0 or seconds == float("inf"):
                    return "?"
                seconds = int(seconds)
                h = seconds // 3600
                m = (seconds % 3600) // 60
                s = seconds % 60
                if h > 0:
                    return f"{h:02d}:{m:02d}:{s:02d}"
                return f"{m:02d}:{s:02d}"

            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "stat", "torrent": torrent, "path": args.src},
            )
            if not resp.get("ok"):
                if args.json:
                    _print_json(resp)
                else:
                    _print_error(resp.get("error", "falha ao ler origem"))
                return

            src_stat = resp.get("stat", {})
            src_is_dir = src_stat.get("type") == "dir"
            dest = args.dest
            copied_bytes = 0
            copied_blocks = 0
            total_bytes = 0
            total_blocks = 0
            start_ts = time.monotonic()
            last_report = start_ts

            def _maybe_report(done: bool = False) -> None:
                nonlocal last_report
                if not show_progress:
                    return
                now = time.monotonic()
                if not done and (now - last_report) < 0.5:
                    return
                last_report = now
                rate = copied_bytes / max(now - start_ts, 1e-6)
                remaining = max(total_bytes - copied_bytes, 0)
                eta = remaining / rate if rate > 0 else float("inf")
                pct = (copied_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
                msg = (
                    f"copiado {copied_bytes}/{total_bytes} bytes ({pct:.2f}%) "
                    f"blocos {copied_blocks}/{total_blocks} eta { _format_eta(eta) }"
                )
                if done:
                    sys.stderr.write("\r" + msg + "\n")
                else:
                    sys.stderr.write("\r" + msg)
                sys.stderr.flush()

            if src_is_dir:
                os.makedirs(dest, exist_ok=True)
                files, errors = await _walk_files(args.src, max_files, max_depth)
                total_bytes = sum(f.get("size", 0) for f in files)
                total_blocks = sum(
                    math.ceil(int(f.get("size", 0)) / chunk_size) for f in files if int(f.get("size", 0)) > 0
                )
                copied = 0
                for item in files:
                    rel = item["path"][len(args.src) :].lstrip("/")
                    target = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    offset = 0
                    size = int(item.get("size", 0))
                    with open(target, "wb") as f:
                        while offset < size:
                            to_read = min(chunk_size, size - offset)
                            resp, data = await rpc_call(
                                args.socket,
                                {
                                    "cmd": "read",
                                    "torrent": torrent,
                                    "path": item["path"],
                                    "offset": offset,
                                    "size": to_read,
                                    "timeout_s": read_timeout,
                                },
                                want_bytes=True,
                            )
                            if not resp.get("ok"):
                                err = resp.get("error", "")
                                if "Timeout" in err:
                                    _maybe_report()
                                    await asyncio.sleep(0.2)
                                    continue
                                errors.append({"path": item["path"], "error": err})
                                break
                            if not data:
                                break
                            f.write(data)
                            offset += len(data)
                            copied_bytes += len(data)
                            copied_blocks += 1
                            _maybe_report()
                    copied += 1
                _maybe_report(done=True)
                out = {
                    "ok": len(errors) == 0,
                    "copied": copied,
                    "copied_bytes": copied_bytes,
                    "total_bytes": total_bytes,
                    "copied_blocks": copied_blocks,
                    "total_blocks": total_blocks,
                    "errors": errors,
                }
                if args.json:
                    _print_json(out)
                else:
                    _print_ok(
                        f"copied: {copied} bytes: {copied_bytes}/{total_bytes} blocks: {copied_blocks}/{total_blocks} errors: {len(errors)}"
                    )
                    for err in errors:
                        _print_error(f"{err.get('path')}: {err.get('error')}")
                return

            if os.path.isdir(dest):
                dest = os.path.join(dest, os.path.basename(args.src))
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            size = int(src_stat.get("size", 0))
            total_bytes = size
            total_blocks = math.ceil(size / chunk_size) if size > 0 else 0
            offset = 0
            errors = []
            with open(dest, "wb") as f:
                while offset < size:
                    to_read = min(chunk_size, size - offset)
                    resp, data = await rpc_call(
                        args.socket,
                        {
                            "cmd": "read",
                            "torrent": torrent,
                            "path": args.src,
                            "offset": offset,
                            "size": to_read,
                            "timeout_s": read_timeout,
                        },
                        want_bytes=True,
                    )
                    if not resp.get("ok"):
                        err = resp.get("error", "")
                        if "Timeout" in err:
                            _maybe_report()
                            await asyncio.sleep(0.2)
                            continue
                        errors.append({"path": args.src, "error": err})
                        break
                    if not data:
                        break
                    f.write(data)
                    offset += len(data)
                    copied_bytes += len(data)
                    copied_blocks += 1
                    _maybe_report()
            _maybe_report(done=True)
            out = {
                "ok": len(errors) == 0,
                "copied": 1 if not errors else 0,
                "copied_bytes": copied_bytes,
                "total_bytes": total_bytes,
                "copied_blocks": copied_blocks,
                "total_blocks": total_blocks,
                "errors": errors,
            }
            if args.json:
                _print_json(out)
            else:
                _print_ok(
                    f"copied: {out['copied']} bytes: {copied_bytes}/{total_bytes} blocks: {copied_blocks}/{total_blocks} errors: {len(errors)}"
                )
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
