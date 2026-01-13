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

        os.makedirs(self.torrent_dir, exist_ok=True)

    def _is_stable(self, path: str) -> bool:
        try:
            s1 = os.stat(path).st_size
            time.sleep(0.5)
            s2 = os.stat(path).st_size
            return s1 > 0 and s1 == s2
        except OSError:
            return False

    def run(self):
        print(f"[torrentfs] monitorando: {self.torrent_dir}")
        while True:
            try:
                for name in os.listdir(self.torrent_dir):
                    if not name.endswith(".torrent"):
                        continue

                    path = os.path.join(self.torrent_dir, name)

                    if path in self.seen:
                        continue

                    now = time.time()
                    pend = self.pending.get(path)
                    if pend and now < pend.get("next_try", 0):
                        continue

                    # espera estabilizar
                    if not self._is_stable(path):
                        continue

                    try:
                        self.manager.add_torrent(path)
                        self.seen.add(path)
                        self.pending.pop(path, None)
                        print(f"[torrentfs] carregado torrent: {name}")
                    except Exception as e:
                        # mantém em pending para tentar depois (com backoff)
                        err = str(e)
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

            except Exception as e:
                print(f"[torrentfs] watcher fatal error: {e}")

            time.sleep(self.interval)
