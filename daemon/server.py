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


async def _safe_send_json(writer: asyncio.StreamWriter, obj: dict) -> bool:
    try:
        await send_json(writer, obj)
        return True
    except (ConnectionResetError, BrokenPipeError):
        return False


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
                        await _safe_send_json(writer, resp)

                    elif cmd == "torrents":
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "torrents": self.manager.list_torrents(),
                            },
                        )
                    elif cmd == "config":
                        cfg = self.manager.get_config()
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "config": cfg,
                            },
                        )
                    elif cmd == "status-all":
                        data = self.manager.status_all()
                        await _safe_send_json(
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
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True},
                        )
                    elif cmd == "cache-size":
                        sizes = self.manager.cache_size()
                        await _safe_send_json(
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
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": bool(removed)},
                        )
                    elif cmd == "prune-cache":
                        dry_run = bool(req.get("dry_run", False))
                        data = self.manager.prune_cache(dry_run=dry_run)
                        await _safe_send_json(
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
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "torrents": data["torrents"],
                            },
                        )
                    elif cmd == "peers-all":
                        data = self.manager.peers_all()
                        await _safe_send_json(
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
                        await _safe_send_json(writer, resp)

                    elif cmd == "reannounce":
                        engine = self._get_engine_from_req(req)
                        engine.reannounce()
                        await _safe_send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "pin-on-load":
                        name = str(req.get("torrent_name") or "")
                        max_files = int(req.get("max_files", 0) or 0)
                        max_depth = int(req.get("max_depth", -1) or -1)
                        self.manager.enqueue_pin(name, max_files, max_depth)
                        await _safe_send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "stop":
                        engine = self._get_engine_from_req(req)
                        data = engine.stop()
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": data.get("ok", False),
                                "error": data.get("error"),
                            },
                        )

                    elif cmd == "resume":
                        engine = self._get_engine_from_req(req)
                        data = engine.resume()
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": data.get("ok", False),
                                "error": data.get("error"),
                            },
                        )

                    elif cmd == "prune-torrent":
                        engine = self._get_engine_from_req(req)
                        keep_pins = bool(req.get("keep_pins", True))
                        data = engine.prune_data(keep_pins=keep_pins)
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": data.get("ok", False),
                                "removed_files": data.get("removed_files", 0),
                                "removed_dirs": data.get("removed_dirs", 0),
                            },
                        )

                    elif cmd == "file-info":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        info = engine.file_info(path)
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "prefetch-info":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        info = engine.prefetch_info(path)
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "infohash":
                        engine = self._get_engine_from_req(req)
                        info = engine.infohash()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "torrent-info":
                        engine = self._get_engine_from_req(req)
                        info = engine.torrent_info_summary()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "info": info},
                        )

                    elif cmd == "list":
                        engine = self._get_engine_from_req(req)
                        path = req.get("path", "")
                        entries = engine.list_dir(path)
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "entries": entries},
                        )

                    elif cmd == "stat":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        st = engine.stat(path)
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "stat": st},
                        )

                    elif cmd == "pin":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        engine.pin(path)
                        await _safe_send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "unpin":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        engine.unpin(path)
                        await _safe_send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "pinned":
                        engine = self._get_engine_from_req(req)
                        pins = engine.list_pins()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "pins": pins},
                        )

                    elif cmd == "pinned-all":
                        pins = self.manager.list_pins_all()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "pins": pins},
                        )
                    elif cmd == "peers":
                        engine = self._get_engine_from_req(req)
                        peers = engine.peers()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "peers": peers},
                        )

                    elif cmd == "prefetch":
                        engine = self._get_engine_from_req(req)
                        path = req["path"]
                        engine.prefetch(path)
                        await _safe_send_json(writer, {"id": req_id, "ok": True})

                    elif cmd == "add-tracker":
                        engine = self._get_engine_from_req(req)
                        trackers = req.get("trackers")
                        data = engine.add_trackers(trackers)
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "added": data.get("added", []),
                                "skipped": data.get("skipped", []),
                            },
                        )

                    elif cmd == "publish-tracker":
                        engine = self._get_engine_from_req(req)
                        trackers = req.get("trackers")
                        data = engine.publish_tracker(trackers)
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": True,
                                "added": data.get("added", []),
                                "skipped": data.get("skipped", []),
                            },
                        )

                    elif cmd == "recheck":
                        engine = self._get_engine_from_req(req)
                        data = engine.force_recheck()
                        await _safe_send_json(
                            writer,
                            {
                                "id": req_id,
                                "ok": data.get("ok", False),
                                "error": data.get("error"),
                            },
                        )

                    elif cmd == "trackers":
                        engine = self._get_engine_from_req(req)
                        trackers = engine.trackers_list()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "trackers": trackers},
                        )

                    elif cmd == "tracker-status":
                        engine = self._get_engine_from_req(req)
                        status = engine.trackers_status()
                        await _safe_send_json(
                            writer,
                            {"id": req_id, "ok": True, "trackers": status},
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
                        await _safe_send_json(
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
                        await _safe_send_json(
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
                    await _safe_send_json(
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
                    await _safe_send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": msg},
                    )
                except FileNotFoundError:
                    await _safe_send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": "FileNotFound"},
                    )
                except NotADirectoryError:
                    await _safe_send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": "NotADirectory"},
                    )
                except IsADirectoryError:
                    await _safe_send_json(
                        writer,
                        {"id": req_id, "ok": False, "error": "IsADirectory"},
                    )
                except Exception as e:
                    await _safe_send_json(
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
