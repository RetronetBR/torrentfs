import asyncio
import os
import signal
from typing import Dict, Any

from common.rpc import (
    recv_json,
    send_json,
    send_bytes,
)

from .engine import TorrentEngine


class TorrentFSServer:
    def __init__(self, socket_path: str, engine: TorrentEngine):
        self.socket_path = socket_path
        self.engine = engine

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        try:
            while True:
                req = await recv_json(reader)
                req_id = req.get("id")
                cmd = req.get("cmd")

                try:
                    if cmd == "hello":
                        resp = {
                            "id": req_id,
                            "ok": True,
                            "name": self.engine.info.name(),
                            "cache": self.engine.cache_dir,
                        }
                        await send_json(writer, resp)

                    elif cmd == "status":
                        resp = {
                            "id": req_id,
                            "ok": True,
                            "status": self.engine.status(),
                        }
                        await send_json(writer, resp)

                    elif cmd == "list":
                        path = req.get("path", "")
                        entries = self.engine.list_dir(path)
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "entries": entries},
                        )

                    elif cmd == "stat":
                        path = req["path"]
                        st = self.engine.stat(path)
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "stat": st},
                        )

                    elif cmd == "pin":
                        path = req["path"]
                        self.engine.pin(path)
                        await send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "read":
                        path = req["path"]
                        offset = int(req.get("offset", 0))
                        size = int(req.get("size", 0))
                        mode = req.get("mode", "auto")

                        data = self.engine.read(path, offset, size, mode=mode)

                        # Envia cabeçalho JSON
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "data_len": len(data),
                            },
                        )
                        # Envia bytes crus
                        if data:
                            await send_bytes(writer, data)

                    else:
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": False,
                                "error": f"UnknownCommand:{cmd}",
                            },
                        )

                except FileNotFoundError:
                    await send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": "FileNotFound"},
                    )
                except NotADirectoryError:
                    await send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": "NotADirectory"},
                    )
                except IsADirectoryError:
                    await send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": "IsADirectory"},
                    )
                except Exception as e:
                    await send_json(
                        writer,
                        {
                            "id": req_id,
                            "ok": False,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def run(self):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self.socket_path,
        )

        # Permissão padrão (grupo pode acessar)
        os.chmod(self.socket_path, 0o660)

        async with server:
            await server.serve_forever()


def run_server(engine, socket_path):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    srv = TorrentFSServer(socket_path, engine)

    async def runner():
        await srv.run()

    try:
        loop.run_until_complete(runner())
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        loop.stop()
        loop.close()
