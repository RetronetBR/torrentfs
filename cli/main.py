# cli/main.py
import argparse
import asyncio
import os
import json
import sys

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


def main():
    ap = argparse.ArgumentParser("torrentfs")
    ap.add_argument("--socket", default="/tmp/torrentfsd.sock")
    ap.add_argument("--torrent", help="Nome ou ID do torrent")

    sub = ap.add_subparsers(dest="cmd", required=True)

    # -----------------------------
    # torrents
    # -----------------------------
    sub.add_parser("torrents", help="Listar torrents carregados")

    # -----------------------------
    # status
    # -----------------------------
    p_status = sub.add_parser("status")
    p_status.add_argument("--unit", choices=["bytes", "kb", "mb", "gb"], default="bytes")

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
    # pinned
    # -----------------------------
    sub.add_parser("pinned", help="Listar arquivos pinados")

    args = ap.parse_args()

    async def run():
        # -----------------------------
        # torrents
        # -----------------------------
        if args.cmd == "torrents":
            resp, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            print(json.dumps(resp, indent=2, ensure_ascii=False))
            return

        # -----------------------------
        # comandos que exigem torrent
        # -----------------------------
        torrent = await get_default_torrent(args.socket, args.torrent)

        if args.cmd == "status":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "status", "torrent": torrent},
            )
            if resp.get("ok") and args.unit != "bytes":
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
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "ls":
            resp, _ = await rpc_call(
                args.socket,
                {
                    "cmd": "list",
                    "torrent": torrent,
                    "path": args.path,
                },
            )
            print(json.dumps(resp, indent=2, ensure_ascii=False))

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
                print(json.dumps(resp, indent=2, ensure_ascii=False))
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
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "pinned":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "pinned", "torrent": torrent},
            )
            print(json.dumps(resp, indent=2, ensure_ascii=False))

    asyncio.run(run())


if __name__ == "__main__":
    main()
