# cli/main.py
import argparse
import asyncio
import os
import json
from cli.client import rpc_call

def main():
    ap = argparse.ArgumentParser("torrentfs")
    ap.add_argument("--socket", default="/tmp/torrentfsd.sock")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("ls").add_argument("path", nargs="?", default="")

    p_cat = sub.add_parser("cat")
    p_cat.add_argument("path")
    p_cat.add_argument("--size", type=int, default=65536)
    p_cat.add_argument("--offset", type=int, default=0)
    p_cat.add_argument("--mode", default="auto")

    sub.add_parser("pin").add_argument("path")

    args = ap.parse_args()

    async def run():
        if args.cmd == "status":
            resp, _ = await rpc_call(args.socket, {"cmd": "status"})
            print(json.dumps(resp, indent=2))

        elif args.cmd == "ls":
            resp, _ = await rpc_call(
                args.socket, {"cmd": "list", "path": args.path}
            )
            print(json.dumps(resp, indent=2))

        elif args.cmd == "cat":
            resp, data = await rpc_call(
                args.socket,
                {
                    "cmd": "read",
                    "path": args.path,
                    "offset": args.offset,
                    "size": args.size,
                    "mode": args.mode,
                },
                want_bytes=True,
            )
            if not resp.get("ok"):
                print(json.dumps(resp, indent=2))
                return
            os.write(1, data)

        elif args.cmd == "pin":
            resp, _ = await rpc_call(
                args.socket, {"cmd": "pin", "path": args.path}
            )
            print(json.dumps(resp, indent=2))

    asyncio.run(run())

if __name__ == "__main__":
    main()
