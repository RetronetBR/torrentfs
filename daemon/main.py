# daemon/main.py
import argparse
import os
import sys

from daemon.manager import TorrentManager
from daemon.engine import get_effective_config
from daemon.ftp_server import start_ftp_server
from daemon.watcher import TorrentDirWatcher
from daemon.server import run_server


def main():
    ap = argparse.ArgumentParser("torrentfsd")

    ap.add_argument(
        "--torrent",
        help="Arquivo .torrent único (modo single-torrent)",
    )
    ap.add_argument(
        "--torrent-dir",
        help="Diretório monitorado com múltiplos .torrent (modo multi-torrent)",
    )
    ap.add_argument(
        "--cache",
        default="./cache",
        help="Diretório de cache dos dados",
    )
    ap.add_argument(
        "--socket",
        default="/tmp/torrentfsd.sock",
        help="Socket UNIX do daemon",
    )
    ap.add_argument(
        "--prefetch",
        action="store_true",
        help="Prefetch automatico ao carregar torrents",
    )
    ap.add_argument(
        "--skip-check",
        action="store_true",
        help="Pula verificacao de hash ao carregar torrents (mais rapido, menos seguro)",
    )
    ftp_group = ap.add_mutually_exclusive_group()
    ftp_group.add_argument(
        "--ftp",
        action="store_true",
        help="Habilita servidor FTP read-only",
    )
    ftp_group.add_argument(
        "--no-ftp",
        action="store_true",
        help="Desabilita servidor FTP",
    )
    ap.add_argument("--ftp-bind", help="Bind do FTP (ex.: 0.0.0.0)")
    ap.add_argument("--ftp-port", type=int, help="Porta do FTP (ex.: 2121)")
    ap.add_argument("--ftp-mount", help="Mount root do FUSE para exports")
    ap.add_argument(
        "--ftp-export",
        action="append",
        help="Exporta um caminho do FUSE (pode repetir)",
    )
    ap.add_argument("--ftp-no-pin", action="store_true", help="Nao auto-pin exports do FTP")
    ap.add_argument("--ftp-pin-max-files", type=int, help="Max files para auto-pin do FTP")
    ap.add_argument("--ftp-pin-depth", type=int, help="Profundidade de auto-pin do FTP")

    args = ap.parse_args()

    # -----------------------------
    # Validação de modo
    # -----------------------------
    if not args.torrent and not args.torrent_dir:
        ap.error("é obrigatório usar --torrent OU --torrent-dir")

    if args.torrent and args.torrent_dir:
        ap.error("--torrent e --torrent-dir são mutuamente exclusivos")

    # -----------------------------
    # Inicialização do manager
    # -----------------------------
    cfg = get_effective_config()
    skip_check = bool(args.skip_check or cfg.get("skip_check"))
    prefetch_on_start = bool(args.prefetch or cfg.get("prefetch_on_start"))
    prefetch_max_files = int(cfg.get("prefetch_max_files", 0))
    prefetch_sleep_ms = int(cfg.get("prefetch_sleep_ms", 25))
    prefetch_batch_size = int(cfg.get("prefetch_batch_size", 10))
    prefetch_batch_sleep_ms = int(cfg.get("prefetch_batch_sleep_ms", 200))
    prefetch_on_start_mode = str(cfg.get("prefetch_on_start_mode", "media"))
    prefetch_scan_sleep_ms = int(cfg.get("prefetch_scan_sleep_ms", 5))
    prefetch_max_dirs = int(cfg.get("prefetch_max_dirs", 0))
    prefetch_max_bytes = int(cfg.get("prefetch_max_bytes", 0))
    checking_max_active = int(cfg.get("checking_max_active", 0))
    ftp_cfg = dict(cfg.get("ftp", {}) or {})
    if args.ftp:
        ftp_cfg["enable"] = True
    elif args.no_ftp:
        ftp_cfg["enable"] = False
    if args.ftp_bind:
        ftp_cfg["bind"] = args.ftp_bind
    if args.ftp_port:
        ftp_cfg["port"] = args.ftp_port
    if args.ftp_mount:
        ftp_cfg["mount"] = args.ftp_mount
    if args.ftp_export:
        ftp_cfg["exports"] = args.ftp_export
    if args.ftp_no_pin:
        ftp_cfg["auto_pin"] = False
    if args.ftp_pin_max_files is not None:
        ftp_cfg["pin_max_files"] = args.ftp_pin_max_files
    if args.ftp_pin_depth is not None:
        ftp_cfg["pin_depth"] = args.ftp_pin_depth
    manager = TorrentManager(
        args.cache,
        prefetch_on_start=prefetch_on_start,
        prefetch_max_files=prefetch_max_files,
        prefetch_sleep_ms=prefetch_sleep_ms,
        prefetch_batch_size=prefetch_batch_size,
        prefetch_batch_sleep_ms=prefetch_batch_sleep_ms,
        prefetch_on_start_mode=prefetch_on_start_mode,
        prefetch_scan_sleep_ms=prefetch_scan_sleep_ms,
        prefetch_max_dirs=prefetch_max_dirs,
        prefetch_max_bytes=prefetch_max_bytes,
        skip_check=skip_check,
        checking_max_active=checking_max_active,
    )

    # -----------------------------
    # Modo single-torrent
    # -----------------------------
    if args.torrent:
        try:
            manager.wait_for_check_slot(pending_name=os.path.basename(args.torrent))
            manager.add_torrent(args.torrent)
        except Exception as e:
            print(f"[torrentfs] erro ao carregar torrent: {e}", file=sys.stderr)
            sys.exit(1)

    # -----------------------------
    # Modo multi-torrent (watcher)
    # -----------------------------
    if args.torrent_dir:
        watcher = TorrentDirWatcher(
            torrent_dir=args.torrent_dir,
            manager=manager,
        )
        watcher.start()

    start_ftp_server(manager, {"ftp": ftp_cfg})

    # -----------------------------
    # Sobe o servidor RPC
    # -----------------------------
    print(f"[torrentfs] socket: {args.socket}")
    run_server(manager, args.socket)


if __name__ == "__main__":
    main()
