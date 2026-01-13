# daemon/main.py
import argparse
import sys

from daemon.manager import TorrentManager
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
    manager = TorrentManager(args.cache)

    # -----------------------------
    # Modo single-torrent
    # -----------------------------
    if args.torrent:
        try:
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

    # -----------------------------
    # Sobe o servidor RPC
    # -----------------------------
    run_server(manager, args.socket)


if __name__ == "__main__":
    main()
