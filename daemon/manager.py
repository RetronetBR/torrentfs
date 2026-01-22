# daemon/manager.py
import os
import hashlib
import threading
import time
import shutil
from typing import Dict, List, Optional, Iterable

from .engine import TorrentEngine, get_effective_config


def torrent_id_from_path(path: str) -> str:
    h = hashlib.sha1(os.path.abspath(path).encode())
    return h.hexdigest()[:12]


class TorrentManager:
    def __init__(
        self,
        cache_root: str,
        prefetch_on_start: bool = False,
        prefetch_max_files: int = 0,
        prefetch_sleep_ms: int = 25,
        prefetch_batch_size: int = 10,
        prefetch_batch_sleep_ms: int = 200,
        prefetch_on_start_mode: str = "media",
        prefetch_scan_sleep_ms: int = 5,
        prefetch_max_dirs: int = 0,
        prefetch_max_bytes: int = 0,
        skip_check: bool = False,
        checking_max_active: int = 0,
    ):
        self.cache_root = os.path.abspath(cache_root)
        os.makedirs(self.cache_root, exist_ok=True)

        self._lock = threading.RLock()
        self.engines: Dict[str, TorrentEngine] = {}
        self.by_name: Dict[str, List[str]] = {}
        self.by_infohash: Dict[str, str] = {}
        self._pending_pins: Dict[str, dict] = {}
        self.prefetch_on_start = prefetch_on_start
        self.prefetch_max_files = max(0, int(prefetch_max_files))
        self.prefetch_sleep_s = max(0.0, float(prefetch_sleep_ms)) / 1000.0
        self.prefetch_batch_size = max(1, int(prefetch_batch_size))
        self.prefetch_batch_sleep_s = max(0.0, float(prefetch_batch_sleep_ms)) / 1000.0
        self.prefetch_on_start_mode = str(prefetch_on_start_mode or "media")
        self.prefetch_scan_sleep_s = max(0.0, float(prefetch_scan_sleep_ms)) / 1000.0
        self.prefetch_max_dirs = max(0, int(prefetch_max_dirs))
        self.prefetch_max_bytes = max(0, int(prefetch_max_bytes))
        self.skip_check = bool(skip_check)
        self.checking_max_active = max(0, int(checking_max_active))

    def add_torrent(self, torrent_path: str) -> str:
        tid = torrent_id_from_path(torrent_path)
        with self._lock:
            if tid in self.engines:
                return tid

        cache_dir = os.path.join(self.cache_root, tid)
        engine = TorrentEngine(
            torrent_path=torrent_path,
            cache_dir=cache_dir,
            skip_check=self.skip_check,
        )

        infohash = ""
        try:
            infohash = engine.infohash().get("v1_hex", "")
        except Exception:
            infohash = ""

        with self._lock:
            if infohash and infohash in self.by_infohash:
                existing = self.by_infohash[infohash]
                try:
                    engine.shutdown()
                except Exception:
                    pass
                try:
                    shutil.rmtree(engine.cache_dir, ignore_errors=True)
                except Exception:
                    pass
                try:
                    os.remove(torrent_path)
                except Exception:
                    pass
                print(
                    f"[torrentfs] torrent duplicado ignorado: {os.path.basename(torrent_path)} (id {existing})"
                )
                return existing

            name = engine.info.name()
            self.engines[tid] = engine
            self.by_name.setdefault(name, []).append(tid)
            if infohash:
                self.by_infohash[infohash] = tid
            self._apply_pending_pin(torrent_path, engine)

            if self.prefetch_on_start:
                threading.Thread(
                    target=self._prefetch_engine,
                    args=(engine,),
                    daemon=True,
                ).start()

            return tid

    def wait_for_check_slot(self, pending_name: str | None = None) -> None:
        if not self.checking_max_active:
            return
        last_log = 0.0
        while self._count_checking() >= self.checking_max_active:
            now = time.time()
            if now - last_log >= 2.0:
                checking = self._checking_info(limit=3)
                suffix = f" para {pending_name}" if pending_name else ""
                msg = (
                    f"[torrentfs] aguardando slot checking ({self._count_checking()}/"
                    f"{self.checking_max_active}){suffix}"
                )
                if checking:
                    msg += f" | checking: {', '.join(checking)}"
                print(msg)
                last_log = now
            time.sleep(0.5)

    def _count_checking(self) -> int:
        with self._lock:
            items = list(self.engines.values())
        count = 0
        for eng in items:
            try:
                st = eng.status()
            except Exception:
                continue
            if st.get("checking"):
                count += 1
        return count

    def _checking_info(self, limit: int = 3) -> List[str]:
        with self._lock:
            items = list(self.engines.items())
        out = []
        for tid, eng in items:
            try:
                st = eng.status()
            except Exception:
                continue
            if st.get("checking"):
                name = st.get("name") or "unknown"
                tfile = os.path.basename(eng.torrent_path)
                pct = st.get("checking_progress")
                files = eng.files_completion()
                if files:
                    done, total = files
                    out.append(f"{name} [{tid}] ({tfile}) ({pct:.2f}) files {done}/{total}")
                else:
                    out.append(f"{name} [{tid}] ({tfile}) ({pct:.2f})")
                if limit and len(out) >= limit:
                    break
        return out

    def get_engine(self, torrent: str) -> TorrentEngine:
        with self._lock:
            if torrent in self.engines:
                return self.engines[torrent]

            if torrent in self.by_name:
                ids = self.by_name[torrent]
                if len(ids) == 1:
                    return self.engines[ids[0]]
                raise ValueError(f"TorrentNameAmbiguous:{torrent}")

            raise KeyError(f"TorrentNotFound:{torrent}")

    def list_torrents(self):
        with self._lock:
            items = list(self.engines.items())
        return [
            {
                "id": tid,
                "name": eng.info.name(),
                "torrent_name": os.path.basename(eng.torrent_path),
                "cache": eng.cache_dir,
            }
            for tid, eng in items
        ]

    def list_pins_all(self) -> List[dict]:
        with self._lock:
            items = list(self.engines.items())
        out: List[dict] = []
        for tid, eng in items:
            try:
                pins = eng.list_pins()
            except Exception:
                pins = []
            for p in pins:
                p["id"] = tid
                out.append(p)
        return out

    def get_config(self) -> dict:
        return get_effective_config()

    def remove_torrent(self, torrent_path: str) -> bool:
        tid = torrent_id_from_path(torrent_path)
        with self._lock:
            engine = self.engines.pop(tid, None)
            if engine is None:
                return False
            name = engine.info.name()
            ids = self.by_name.get(name, [])
            if tid in ids:
                ids.remove(tid)
                if not ids:
                    self.by_name.pop(name, None)
                else:
                    self.by_name[name] = ids
            try:
                infohash = engine.infohash().get("v1_hex", "")
            except Exception:
                infohash = ""
            if infohash and self.by_infohash.get(infohash) == tid:
                self.by_infohash.pop(infohash, None)
        try:
            engine.shutdown()
        except Exception:
            pass
        try:
            shutil.rmtree(engine.cache_dir, ignore_errors=True)
        except Exception:
            pass
        return True

    def remove_torrent_by_id(self, tid: str) -> bool:
        with self._lock:
            engine = self.engines.pop(tid, None)
            if engine is None:
                return False
            name = engine.info.name()
            ids = self.by_name.get(name, [])
            if tid in ids:
                ids.remove(tid)
                if not ids:
                    self.by_name.pop(name, None)
                else:
                    self.by_name[name] = ids
            try:
                infohash = engine.infohash().get("v1_hex", "")
            except Exception:
                infohash = ""
            if infohash and self.by_infohash.get(infohash) == tid:
                self.by_infohash.pop(infohash, None)
        try:
            engine.shutdown()
        except Exception:
            pass
        try:
            shutil.rmtree(engine.cache_dir, ignore_errors=True)
        except Exception:
            pass
        return True

    def enqueue_pin(self, torrent_name: str, max_files: int = 0, max_depth: int = -1) -> None:
        if not torrent_name:
            return
        key = os.path.basename(torrent_name)
        payload = {"max_files": int(max_files), "max_depth": int(max_depth)}
        with self._lock:
            self._pending_pins[key] = payload
            for tid, eng in self.engines.items():
                if os.path.basename(eng.torrent_path) == key:
                    self._start_pin_thread(eng, payload)
                    self._pending_pins.pop(key, None)
                    break

    def _apply_pending_pin(self, torrent_path: str, engine: TorrentEngine) -> None:
        key = os.path.basename(torrent_path)
        payload = None
        with self._lock:
            payload = self._pending_pins.pop(key, None)
        if payload:
            self._start_pin_thread(engine, payload)

    def _start_pin_thread(self, engine: TorrentEngine, payload: dict) -> None:
        max_files = int(payload.get("max_files", 0))
        max_depth = int(payload.get("max_depth", -1))
        threading.Thread(
            target=self._pin_all_engine,
            args=(engine, max_files, max_depth),
            daemon=True,
        ).start()

    def _pin_all_engine(self, engine: TorrentEngine, max_files: int, max_depth: int) -> None:
        pinned = 0
        errors = 0

        def _walk(path: str, depth: int) -> None:
            nonlocal pinned, errors
            if max_files > 0 and pinned >= max_files:
                return
            entries = engine.index.list_dir(path)
            for entry in entries:
                if max_files > 0 and pinned >= max_files:
                    return
                name = entry.get("name", "")
                if not name:
                    continue
                etype = entry.get("type", "")
                child = os.path.join(path, name) if path else name
                if etype == "dir":
                    if max_depth >= 0 and depth >= max_depth:
                        continue
                    _walk(child, depth + 1)
                else:
                    try:
                        engine.pin(child)
                        pinned += 1
                    except Exception:
                        errors += 1

        _walk("", 0)
        print(
            f"[torrentfs] pin agendado concluido: {engine.info.name()} pinned={pinned} errors={errors}"
        )

    def _walk_files(self, engine: TorrentEngine, path: str = "", dir_count: Optional[List[int]] = None) -> Iterable[str]:
        if dir_count is None:
            dir_count = [0]
        if self.prefetch_max_dirs and dir_count[0] >= self.prefetch_max_dirs:
            return
        entries = engine.index.list_dir(path)
        dir_count[0] += 1
        if self.prefetch_scan_sleep_s > 0:
            time.sleep(self.prefetch_scan_sleep_s)
        for e in entries:
            name = e.get("name", "")
            if not name:
                continue
            child = f"{path}/{name}" if path else name
            if e.get("type") == "dir":
                yield from self._walk_files(engine, child, dir_count)
            else:
                yield child

    def _prefetch_engine(self, engine: TorrentEngine) -> None:
        try:
            count = 0
            batch_count = 0
            bytes_budget = self.prefetch_max_bytes
            bytes_used = 0
            for path in self._walk_files(engine):
                if self.prefetch_max_files and count >= self.prefetch_max_files:
                    break
                if self.prefetch_on_start_mode == "media" and not engine.is_media_path(path):
                    continue
                if bytes_budget:
                    try:
                        planned = engine.prefetch_bytes(path)
                    except Exception:
                        planned = 0
                    if bytes_used + planned > bytes_budget:
                        break
                try:
                    engine.prefetch(path)
                except Exception:
                    pass
                count += 1
                if bytes_budget:
                    bytes_used += planned
                batch_count += 1
                if self.prefetch_sleep_s > 0:
                    time.sleep(self.prefetch_sleep_s)
                if self.prefetch_batch_size and batch_count >= self.prefetch_batch_size:
                    batch_count = 0
                    if self.prefetch_batch_sleep_s > 0:
                        time.sleep(self.prefetch_batch_sleep_s)
        except Exception:
            return

    def status_all(self) -> dict:
        with self._lock:
            items = list(self.engines.items())
        torrents = []
        total_downloaded = 0
        total_uploaded = 0
        total_download_rate = 0
        total_upload_rate = 0
        total_peers = 0
        total_seeds = 0
        for tid, eng in items:
            st = eng.status()
            torrents.append({"id": tid, "status": st})
            total_downloaded += int(st.get("downloaded", 0))
            total_uploaded += int(st.get("uploaded", 0))
            total_download_rate += int(st.get("download_rate", 0))
            total_upload_rate += int(st.get("upload_rate", 0))
            total_peers += int(st.get("peers", 0))
            total_seeds += int(st.get("seeds", 0))
        return {
            "totals": {
                "downloaded": total_downloaded,
                "uploaded": total_uploaded,
                "download_rate": total_download_rate,
                "upload_rate": total_upload_rate,
                "peers": total_peers,
                "seeds": total_seeds,
            },
            "torrents": torrents,
        }

    def downloads(self, max_files: Optional[int] = None) -> dict:
        with self._lock:
            items = list(self.engines.items())
        torrents = []
        for tid, eng in items:
            st = eng.status()
            if float(st.get("progress", 0)) >= 1.0:
                continue
            files = eng.downloading_files(max_files=max_files)
            torrents.append(
                {
                    "id": tid,
                    "status": st,
                    "files": files,
                }
            )
        return {"torrents": torrents}

    def peers_all(self) -> dict:
        with self._lock:
            items = list(self.engines.items())
        torrents = []
        for tid, eng in items:
            torrents.append(
                {
                    "id": tid,
                    "status": eng.status(),
                    "peers": eng.peers(),
                }
            )
        return {"torrents": torrents}

    def reannounce_all(self) -> None:
        with self._lock:
            items = list(self.engines.values())
        for eng in items:
            eng.reannounce()

    def cache_size(self) -> dict:
        logical_total = 0
        disk_total = 0
        for root, _, files in os.walk(self.cache_root):
            for name in files:
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                    logical_total += int(st.st_size)
                    # st_blocks is in 512-byte units on Linux
                    disk_total += int(st.st_blocks) * 512
                except OSError:
                    continue
        return {"logical": logical_total, "disk": disk_total}

    def prune_cache(self, dry_run: bool = False) -> dict:
        with self._lock:
            active_ids = set(self.engines.keys())

        removed = []
        skipped = 0
        try:
            entries = os.listdir(self.cache_root)
        except FileNotFoundError:
            return {"removed": removed, "skipped": skipped}

        for name in entries:
            path = os.path.join(self.cache_root, name)
            if not os.path.isdir(path):
                continue
            if name in active_ids:
                continue
            if len(name) != 12 or any(c not in "0123456789abcdef" for c in name):
                skipped += 1
                continue
            if not dry_run:
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    skipped += 1
                    continue
            removed.append(name)

        return {"removed": removed, "skipped": skipped}
