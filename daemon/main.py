# daemon/main.py
import argparse
from daemon.engine import TorrentEngine
from daemon.server import run_server

def main():
    ap = argparse.ArgumentParser("torrentfsd")
    ap.add_argument("--torrent", required=True)
    ap.add_argument("--cache", default="./cache")
    ap.add_argument("--socket", default="/tmp/torrentfsd.sock")
    args = ap.parse_args()

    engine = TorrentEngine(
        torrent_path=args.torrent,
        cache_dir=args.cache,
    )

    run_server(engine, args.socket)

if __name__ == "__main__":
    main()
