# daemon/server.py
import asyncio
import os

from common.rpc import (
    recv_json,
    send_json,
    send_bytes,
)

from .manager import TorrentManager

MAX_READ_BYTES = 4 * 1024 * 1024


class TorrentFSServer:
    def __init__(self, socket_path: str, manager: TorrentManager):
        self.socket_path = socket_path
        self.manager = manager

    def _get_engine_from_req(self, req: dict):
        torrent = req.get("torrent")
        if not torrent:
            raise ValueError("TorrentRequired")
        return self.manager.get_engine(str(torrent))

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
                    # -----------------------------
                    # Meta / controle
                    # -----------------------------
                    if cmd == "hello":
                        # Mantém compatibilidade: retorna info do daemon + torrents disponíveis
                        resp = {
                            "id": req_id,
                            "ok": True,
                            "name": "torrentfsd",
                            "torrents": self.manager.list_torrents(),
                        }
                        await send_json(writer, resp)

                    elif cmd == "torrents":
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "torrents": self.manager.list_torrents(),
                            },
                        )
                    elif cmd == "config":
                        cfg = self.manager.get_config()
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "config": cfg,
                            },
                        )
                    elif cmd == "status-all":
                        data = self.manager.status_all()
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "totals": data["totals"],
                                "torrents": data["torrents"],
                            },
                        )
                    elif cmd == "reannounce-all":
                        self.manager.reannounce_all()
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True},
                        )
                    elif cmd == "cache-size":
                        sizes = self.manager.cache_size()
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "logical_bytes": sizes["logical"],
                                "disk_bytes": sizes["disk"],
                            },
                        )
                    elif cmd == "remove-torrent":
                        tid = req.get("torrent", "")
                        removed = self.manager.remove_torrent_by_id(str(tid))
                        await send_json(
                            writer,
                            {"id": req_id, "ok": bool(removed)},
                        )
                    elif cmd == "prune-cache":
                        dry_run = bool(req.get("dry_run", False))
                        data = self.manager.prune_cache(dry_run=dry_run)
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "removed": data["removed"],
                                "skipped": data["skipped"],
                            },
                        )
                    elif cmd == "downloads":
                        max_files = req.get("max_files")
                        if max_files is not None:
                            max_files = int(max_files)
                        data = self.manager.downloads(max_files=max_files)
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "torrents": data["torrents"],
                            },
                        )
                    elif cmd == "peers-all":
                        data = self.manager.peers_all()
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "torrents": data["torrents"],
                            },
                        )

                    # -----------------------------
                    # Operações por torrent (requer "torrent")
                    # -----------------------------
                    elif cmd == "status":
                        engine = self._get_engine_from_req(req)
                        resp = {
                            "id": req_id,
                            "ok": True,
                            "status": engine.status(),
                        }
                        await send_json(writer, resp)

                    elif cmd == "reannounce":
                        engine = self._get_engine_from_req(req)
                        engine.reannounce()
                        await send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "file-info":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        info = engine.file_info(path)
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "prefetch-info":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        info = engine.prefetch_info(path)
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "infohash":
                        engine = self._get_engine_from_req(req)
                        info = engine.infohash()
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "torrent-info":
                        engine = self._get_engine_from_req(req)
                        info = engine.torrent_info_summary()
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "list":
                        engine = self._get_engine_from_req(req)
                        path = req.get("path", "")
                        entries = engine.list_dir(path)
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "entries": entries},
                        )

                    elif cmd == "stat":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        st = engine.stat(path)
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "stat": st},
                        )

                    elif cmd == "pin":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        engine.pin(path)
                        await send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "unpin":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        engine.unpin(path)
                        await send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "pinned":
                        engine = self._get_engine_from_req(req)
                        pins = engine.list_pins()
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "pins": pins},
                        )
                    elif cmd == "peers":
                        engine = self._get_engine_from_req(req)
                        peers = engine.peers()
                        await send_json(
                            writer,
                            {"id": req_id, "ok": True, "peers": peers},
                        )

                    elif cmd == "prefetch":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        engine.prefetch(path)
                        await send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "add-tracker":
                        engine = self._get_engine_from_req(req)
                        trackers = req.get("trackers")
                        data = engine.add_trackers(trackers)
                        await send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "added": data.get("added", []),
                                "skipped": data.get("skipped", []),
                            },
                        )

                    elif cmd == "read":
                        engine = self._get_engine_from_req(req)

                        path = req["path"]
                        offset = int(req.get("offset", 0))
                        size = int(req.get("size", 0))
                        mode = req.get("mode", "auto")

                        timeout_s = req.get("timeout_s")
                        if timeout_s is not None:
                            timeout_s = float(timeout_s)

                        if size < 0 or size > MAX_READ_BYTES:
                            raise ValueError("ReadSizeInvalid")

                        data = await asyncio.to_thread(
                            engine.read,
                            path,
                            offset,
                            size,
                            mode,
                            timeout_s,
                        )

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

                except KeyError as e:
                    err = e.args[0] if e.args else "UnknownError"
                    if str(err).startswith("TorrentNotFound:"):
                        msg = "Torrent nao encontrado. Use 'torrents' para listar."
                    else:
                        msg = str(err)
                    await send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": msg},
                    )
                except ValueError as e:
                    err = str(e)
                    if err.startswith("TorrentNameAmbiguous:"):
                        name = err.split(":", 1)[1]
                        msg = (
                            f"Nome de torrent ambiguo: {name}. "
                            "Use --torrent com o ID."
                        )
                    elif err == "TorrentRequired":
                        msg = "Torrent obrigatorio. Use --torrent ou escolha um ID."
                    elif err == "ReadSizeInvalid":
                        msg = "Tamanho de leitura invalido."
                    else:
                        msg = err
                    await send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": msg},
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


def run_server(manager: TorrentManager, socket_path: str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    srv = TorrentFSServer(socket_path, manager)

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
