# daemon/ftp_main.py
import argparse
import os
import time

from daemon.engine import get_effective_config
from daemon.ftp_server import start_ftp_server


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


def main():
    ap = argparse.ArgumentParser("torrentfs-ftp")
    ap.add_argument("--socket", help="Socket UNIX do torrentfsd")
    ap.add_argument("--bind", help="Bind do FTP (ex.: 0.0.0.0)")
    ap.add_argument("--port", type=int, help="Porta do FTP (ex.: 2121)")
    ap.add_argument("--mount", help="Mount root do FUSE para exports")
    ap.add_argument(
        "--export",
        action="append",
        help="Exporta um caminho do FUSE (pode repetir)",
    )
    ap.add_argument("--no-pin", action="store_true", help="Nao auto-pin exports do FTP")
    ap.add_argument("--pin-max-files", type=int, help="Max files para auto-pin do FTP")
    ap.add_argument("--pin-depth", type=int, help="Profundidade de auto-pin do FTP")
    args = ap.parse_args()

    cfg = get_effective_config()
    ftp_cfg = dict(cfg.get("ftp", {}) or {})
    ftp_cfg["enable"] = True
    if args.bind:
        ftp_cfg["bind"] = args.bind
    if args.port:
        ftp_cfg["port"] = args.port
    if args.mount:
        ftp_cfg["mount"] = args.mount
    if args.export:
        ftp_cfg["exports"] = args.export
    if args.no_pin:
        ftp_cfg["auto_pin"] = False
    if args.pin_max_files is not None:
        ftp_cfg["pin_max_files"] = args.pin_max_files
    if args.pin_depth is not None:
        ftp_cfg["pin_depth"] = args.pin_depth

    port = int(ftp_cfg.get("port", 2121))
    if port <= 0 or port > 65535:
        raise SystemExit(f"porta invalida: {port}")
    ftp_cfg["port"] = port

    socket_path = args.socket or _default_socket_path()
    start_ftp_server({"ftp": ftp_cfg}, socket_path=socket_path)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
