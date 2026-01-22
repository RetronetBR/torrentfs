# daemon/watcher.py
import os
import time
import threading

from .manager import TorrentManager


class TorrentDirWatcher(threading.Thread):
    def __init__(self, torrent_dir: str, manager: TorrentManager, interval=2.0):
        super().__init__(daemon=True)
        self.torrent_dir = os.path.abspath(torrent_dir)
        self.manager = manager
        self.interval = interval

        self.seen = set()
        self.pending = {}
        self.quarantine_dir = os.path.join(self.torrent_dir, "bad")

        os.makedirs(self.torrent_dir, exist_ok=True)
        os.makedirs(self.quarantine_dir, exist_ok=True)

    def _is_stable(self, path: str) -> bool:
        try:
            s1 = os.stat(path).st_size
            time.sleep(0.5)
            s2 = os.stat(path).st_size
            return s1 > 0 and s1 == s2
        except OSError:
            return False

    def _friendly_error(self, err: str) -> str:
        if "bdecode" in err or "bencoded" in err:
            return "arquivo .torrent invalido ou corrompido"
        return err or "erro desconhecido"

    def run(self):
        print(f"[torrentfs] monitorando: {self.torrent_dir}")
        while True:
            try:
                current = set()
                names = [n for n in os.listdir(self.torrent_dir) if n.endswith(".torrent")]
                names.sort()
                new_paths = []
                for name in names:
                    path = os.path.join(self.torrent_dir, name)
                    current.add(path)
                    if path not in self.seen:
                        new_paths.append(path)

                total_new = len(new_paths)
                for idx, path in enumerate(new_paths, start=1):
                    name = os.path.basename(path)

                    now = time.time()
                    pend = self.pending.get(path)
                    if pend and now < pend.get("next_try", 0):
                        continue

                    # espera estabilizar
                    if not self._is_stable(path):
                        continue

                    try:
                        self.manager.wait_for_check_slot(pending_name=name)
                        print(f"[torrentfs] carregando ({idx}/{total_new}): {name}")
                        self.manager.add_torrent(path)
                        self.seen.add(path)
                        self.pending.pop(path, None)
                        print(f"[torrentfs] carregado torrent: {name}")
                    except Exception as e:
                        # mantém em pending para tentar depois (com backoff)
                        err = str(e) or type(e).__name__
                        err = self._friendly_error(err)
                        attempts = 1 if not pend else pend.get("attempts", 0) + 1
                        delay = min(60.0, self.interval * (2 ** min(attempts - 1, 5)))
                        next_try = time.time() + delay

                        if not pend or pend.get("error") != err:
                            print(f"[torrentfs] erro ao carregar {name}: {err}")

                        self.pending[path] = {
                            "error": err,
                            "attempts": attempts,
                            "next_try": next_try,
                        }
                        if attempts >= 3:
                            bad_path = os.path.join(self.quarantine_dir, name)
                            try:
                                os.replace(path, bad_path)
                                print(f"[torrentfs] quarentena: {name} -> {bad_path}")
                                self.pending.pop(path, None)
                                self.seen.discard(path)
                            except Exception as move_err:
                                print(f"[torrentfs] falha ao mover para quarentena: {move_err}")

                removed = [p for p in self.seen if p not in current]
                for path in removed:
                    if self.manager.remove_torrent(path):
                        print(f"[torrentfs] removido torrent: {os.path.basename(path)}")
                    self.seen.discard(path)
                    self.pending.pop(path, None)

            except Exception as e:
                print(f"[torrentfs] watcher fatal error: {e}")

            time.sleep(self.interval)
