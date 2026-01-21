# cli/main.py
import argparse
import asyncio
import math
import os
import json
import sys
import time
import re
import binascii
import urllib.parse
import urllib.request
import random

try:
    import libtorrent as lt
except Exception:
    lt = None

from cli.client import rpc_call
from plugins import get_plugin_for_uri
from plugins.base import SourceError

DEFAULT_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "config", "torrentfsd.json")
)
SYSTEM_CONFIG_PATH = "/etc/torrentfs/torrentfsd.json"


def _find_config_path() -> str:
    env = os.environ.get("TORRENTFSD_CONFIG")
    if env:
        return env
    user_path = os.path.join(os.path.expanduser("~"), ".config", "torrentfs", "torrentfsd.json")
    if os.path.exists(user_path):
        return user_path
    if os.path.exists(SYSTEM_CONFIG_PATH):
        return SYSTEM_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def _load_trackers_from_config() -> list[str]:
    path = _find_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    trackers = data.get("trackers", {}) if isinstance(data, dict) else {}
    add = trackers.get("add", [])
    if isinstance(add, str):
        return [add]
    if isinstance(add, list):
        return [x for x in add if isinstance(x, str)]
    return []


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


def _build_torrent_dir_map(torrents):
    name_counts = {}
    for t in torrents:
        tname = str(t.get("torrent_name", ""))
        base = os.path.splitext(tname)[0] if tname else str(t.get("name", ""))
        name_counts[base] = name_counts.get(base, 0) + 1
    out = {}
    for t in torrents:
        tid = str(t.get("id", ""))
        name = str(t.get("name", tid))
        tname = str(t.get("torrent_name", ""))
        base = os.path.splitext(tname)[0] if tname else name
        if name_counts.get(base, 0) <= 1:
            dir_name = base
        else:
            dir_name = f"{base}__{tid}"
        out[dir_name] = tid
    return out


def _normalize_path(path: str) -> str:
    if path in ("", "."):
        return ""
    return path.replace(os.sep, "/")

def _default_socket_path() -> str:
    env = os.environ.get("TORRENTFSD_SOCKET")
    if env:
        return env
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        candidate = os.path.join(runtime_dir, "torrentfsd.sock")
        if os.path.exists(candidate):
            return candidate
    return "/tmp/torrentfsd.sock"


def main():
    ap = argparse.ArgumentParser("torrentfs")
    ap.add_argument("--socket", default=_default_socket_path())
    ap.add_argument("--torrent", help="Nome ou ID do torrent")
    ap.add_argument(
        "--mount",
        help="Mountpoint do FUSE para resolver paths do filesystem",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Saida em JSON (default: texto simples)",
    )

    sub = ap.add_subparsers(dest="cmd")

    # -----------------------------
    # torrents
    # -----------------------------
    p_torrents = sub.add_parser("torrents", help="Torrent: listar torrents carregados")
    p_torrents.add_argument(
        "--verbose",
        action="store_true",
        help="Inclui caminho do cache na listagem",
    )

    # -----------------------------
    # config
    # -----------------------------
    sub.add_parser("config", help="Daemon: mostrar configuracao efetiva")

    # -----------------------------
    # cache-size
    # -----------------------------
    sub.add_parser("cache-size", help="Cache: tamanho total do cache")

    # -----------------------------
    # prune-cache
    # -----------------------------
    p_prune = sub.add_parser("prune-cache", help="Cache: limpar cache sem referencia ativa")
    p_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria removido sem apagar",
    )

    # -----------------------------
    # remove-torrent
    # -----------------------------
    sub.add_parser("remove-torrent", help="Torrent: remover torrent pelo ID")

    # -----------------------------
    # add-magnet
    # -----------------------------
    p_add_magnet = sub.add_parser("add-magnet", help="Fonte: adicionar magnet e salvar .torrent")
    p_add_magnet.add_argument("magnet")
    p_add_magnet.add_argument(
        "--dir",
        default="torrents",
        help="Diretorio onde salvar o .torrent (default: torrents)",
    )
    p_add_magnet.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout para baixar metadata (segundos)",
    )

    # -----------------------------
    # source-add
    # -----------------------------
    p_source = sub.add_parser("source-add", help="Fonte: adicionar via plugin")
    p_source.add_argument("uri")
    p_source.add_argument(
        "--dir",
        default="torrents",
        help="Diretorio onde salvar o .torrent (default: torrents)",
    )
    p_source.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout para baixar metadata (segundos)",
    )

    # -----------------------------
    # add-url
    # -----------------------------
    p_add_url = sub.add_parser("add-url", help="Fonte: baixar .torrent via URL")
    p_add_url.add_argument("url")
    p_add_url.add_argument(
        "--dir",
        default="torrents",
        help="Diretorio onde salvar o .torrent (default: torrents)",
    )
    p_add_url.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout para baixar .torrent (segundos)",
    )

    # -----------------------------
    # alias
    # -----------------------------
    p_alias = sub.add_parser("alias", help="Alias: gerenciar nomes de torrents")
    p_alias_sub = p_alias.add_subparsers(dest="alias_cmd")
    p_alias_set = p_alias_sub.add_parser("set", help="Definir alias para um torrent")
    p_alias_set.add_argument("id")
    p_alias_set.add_argument("name")
    p_alias_rm = p_alias_sub.add_parser("rm", help="Remover alias de um torrent")
    p_alias_rm.add_argument("id")
    p_alias_sub.add_parser("list", help="Listar aliases configurados")

    # -----------------------------
    # add-tracker
    # -----------------------------
    p_add_tracker = sub.add_parser("add-tracker", help="Tracker: adicionar ao torrent")
    p_add_tracker.add_argument(
        "--tracker",
        action="append",
        default=[],
        help="URL do tracker (pode repetir)",
    )

    # -----------------------------
    # publish-tracker
    # -----------------------------
    p_publish = sub.add_parser("publish-tracker", help="Tracker: forcar anuncio no tracker")
    p_publish.add_argument(
        "--tracker",
        action="append",
        default=[],
        help="URL do tracker (pode repetir)",
    )

    # -----------------------------
    # trackers
    # -----------------------------
    sub.add_parser("trackers", help="Tracker: listar trackers efetivos do torrent")

    # -----------------------------
    # status
    # -----------------------------
    p_status = sub.add_parser("status", help="Torrent: status do torrent selecionado")
    p_status.add_argument("--unit", choices=["bytes", "kb", "mb", "gb"], default="bytes")
    p_status.add_argument(
        "--human",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibe tamanhos e taxas em formato legivel",
    )

    # -----------------------------
    # status-all
    # -----------------------------
    p_status_all = sub.add_parser("status-all", help="Torrent: resumo global de todos os torrents")
    p_status_all.add_argument("--unit", choices=["bytes", "kb", "mb", "gb"], default="bytes")
    p_status_all.add_argument(
        "--human",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibe tamanhos e taxas em formato legivel",
    )

    # -----------------------------
    # downloads
    # -----------------------------
    p_downloads = sub.add_parser("downloads", help="Torrent: listar downloads em execucao")
    p_downloads.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos por torrent (0 = sem limite)",
    )

    # -----------------------------
    # uploads
    # -----------------------------
    p_uploads = sub.add_parser("uploads", help="Rede: listar peers com transferencia ativa")
    p_uploads.add_argument(
        "--all",
        action="store_true",
        help="Inclui peers sem transferencia ativa",
    )
    p_uploads.add_argument(
        "--all-torrents",
        action="store_true",
        help="Lista peers de todos os torrents (ignora --torrent)",
    )

    # -----------------------------
    # reannounce
    # -----------------------------
    sub.add_parser("reannounce", help="Rede: forcar announce do tracker/DHT")

    # -----------------------------
    # reannounce-all
    # -----------------------------
    sub.add_parser("reannounce-all", help="Rede: forcar announce em todos os torrents")

    # -----------------------------
    # file-info
    # -----------------------------
    p_file_info = sub.add_parser("file-info", help="Arquivo: info de pieces de um arquivo")
    p_file_info.add_argument("path")

    # -----------------------------
    # prefetch-info
    # -----------------------------
    p_prefetch_info = sub.add_parser("prefetch-info", help="Prefetch: info de um arquivo")
    p_prefetch_info.add_argument("path")

    # -----------------------------
    # torrent-info
    # -----------------------------
    sub.add_parser("torrent-info", help="Torrent: mostrar metadados do .torrent")

    # -----------------------------
    # infohash
    # -----------------------------
    sub.add_parser("infohash", help="Torrent: mostrar infohash (v1/v2)")

    # -----------------------------
    # tracker-scrape
    # -----------------------------
    p_scrape = sub.add_parser("tracker-scrape", help="Tracker: consultar scrape por infohash")
    p_scrape.add_argument("infohash", nargs="?")
    p_scrape.add_argument(
        "--tracker",
        help="URL do tracker (default: trackers.add[0] no config)",
    )

    # -----------------------------
    # tracker-status
    # -----------------------------
    sub.add_parser("tracker-status", help="Tracker: status dos trackers do torrent")

    # -----------------------------
    # tracker-announce
    # -----------------------------
    p_announce = sub.add_parser(
        "tracker-announce", help="Tracker: teste de announce via HTTP"
    )
    p_announce.add_argument(
        "--tracker",
        help="URL do tracker (default: trackers.add[0] no config)",
    )
    p_announce.add_argument("--port", type=int, default=6881)

    # -----------------------------
    # ls
    # -----------------------------
    p_ls = sub.add_parser("ls", help="Arquivo: listar arquivos e diretorios")
    p_ls.add_argument("path", nargs="?", default="")

    # -----------------------------
    # cat
    # -----------------------------
    p_cat = sub.add_parser("cat", help="Arquivo: ler bytes de um arquivo")
    p_cat.add_argument("path")
    p_cat.add_argument("--size", type=int, default=65536)
    p_cat.add_argument("--offset", type=int, default=0)
    p_cat.add_argument("--mode", default="auto")

    # -----------------------------
    # cat (wait)
    # -----------------------------
    p_cat.add_argument(
        "--wait",
        action="store_true",
        help="Aguarda download das pieces (retry em timeout)",
    )
    p_cat.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Timeout por tentativa (segundos)",
    )
    p_cat.add_argument(
        "--retry-sleep",
        type=float,
        default=0.2,
        help="Espera entre tentativas (segundos)",
    )
    # -----------------------------
    # pin
    # -----------------------------
    p_pin = sub.add_parser("pin", help="Pin: pinar arquivo")
    p_pin.add_argument("path")

    # -----------------------------
    # cp
    # -----------------------------
    p_cp = sub.add_parser("cp", help="Arquivo: copiar do mount para o disco local")
    p_cp.add_argument("src")
    p_cp.add_argument("dest")
    p_cp.add_argument(
        "--chunk-size",
        type=int,
        default=1024 * 1024,
        help="Tamanho do bloco de leitura (bytes)",
    )
    p_cp.add_argument(
        "--read-timeout",
        type=float,
        default=1.0,
        help="Timeout de leitura por bloco (segundos). Use 0 para esperar indefinidamente.",
    )
    p_cp.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibe progresso e ETA no stderr",
    )
    p_cp.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_cp.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # du
    # -----------------------------
    p_du = sub.add_parser("du", help="Arquivo: somar tamanho dos arquivos por path")
    p_du.add_argument("path", nargs="?", default="")
    p_du.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # pin-dir
    # -----------------------------
    p_pin_dir = sub.add_parser("pin-dir", help="Pin: pinar todos os arquivos de um diretorio")
    p_pin_dir.add_argument("path")
    p_pin_dir.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_pin_dir.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # pin-all
    # -----------------------------
    p_pin_all = sub.add_parser("pin-all", help="Pin: pinar todos os arquivos do torrent")
    p_pin_all.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_pin_all.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o root, -1 = ilimitado)",
    )

    # -----------------------------
    # unpin
    # -----------------------------
    p_unpin = sub.add_parser("unpin", help="Pin: despinar arquivo")
    p_unpin.add_argument("path")

    # -----------------------------
    # unpin-dir
    # -----------------------------
    p_unpin_dir = sub.add_parser("unpin-dir", help="Pin: despinar todos os arquivos de um diretorio")
    p_unpin_dir.add_argument("path")
    p_unpin_dir.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos (0 = sem limite)",
    )
    p_unpin_dir.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    # -----------------------------
    # pinned
    # -----------------------------
    sub.add_parser("pinned", help="Pin: listar arquivos pinados")

    # -----------------------------
    # prefetch
    # -----------------------------
    p_prefetch = sub.add_parser("prefetch", help="Prefetch: pre-cache de arquivo ou diretorio")
    p_prefetch.add_argument("path")
    p_prefetch.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limite maximo de arquivos a prefetchar (0 = sem limite)",
    )
    p_prefetch.add_argument(
        "--depth",
        type=int,
        default=-1,
        help="Profundidade maxima de diretórios (0 = só o path, -1 = ilimitado)",
    )

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return
    explicit_socket = "--socket" in sys.argv
    if not explicit_socket:
        fallback = "/tmp/torrentfsd.sock"
        if isinstance(args.socket, str) and args.socket != fallback:
            args.socket = [args.socket, fallback]

    async def run():
        def _print_json(obj):
            print(json.dumps(obj, indent=2, ensure_ascii=False))

        def _print_error(msg):
            print(f"erro: {msg}", file=sys.stderr)

        def _print_ok(msg: str):
            print(msg)

        def _fmt_bytes(value: float) -> str:
            units = ["B", "KiB", "MiB", "GiB", "TiB"]
            v = float(value)
            idx = 0
            while v >= 1024.0 and idx < len(units) - 1:
                v /= 1024.0
                idx += 1
            return f"{v:.2f} {units[idx]}"

        def _fmt_rate(value: float) -> str:
            return f"{_fmt_bytes(value)}/s"

        def _sanitize_name(name: str) -> str:
            base = name.strip()
            base = base.replace(os.sep, "_")
            base = re.sub(r"[\\/:*?\"<>|]+", "_", base)
            base = re.sub(r"\s+", " ", base).strip()
            return base or "torrent"

        def _peer_host(p: dict) -> str:
            ip = str(p.get("ip", "")).strip()
            port = int(p.get("port", 0) or 0)
            if ip and port:
                return f"{ip}:{port}"
            return ip or "-"

        def _print_peers_summary(tid: str, name: str, peers: list):
            active = 0
            up_rate = 0
            down_rate = 0
            for p in peers:
                up = int(p.get("upload_rate", 0))
                down = int(p.get("download_rate", 0))
                if up > 0 or down > 0:
                    active += 1
                up_rate += up
                down_rate += down
            label = name if name else tid
            print(
                f"{label}\tpeers={len(peers)}\tactive={active}\t"
                f"up={_fmt_rate(up_rate)}\tdown={_fmt_rate(down_rate)}"
            )

        def _print_peer_line(p: dict):
            host = _peer_host(p)
            up = int(p.get("upload_rate", 0))
            down = int(p.get("download_rate", 0))
            uploaded = int(p.get("uploaded", 0))
            downloaded = int(p.get("downloaded", 0))
            client = p.get("client", "")
            msg = f"  {host}\tup={_fmt_rate(up)}\tdown={_fmt_rate(down)}"
            if uploaded > 0 or downloaded > 0:
                msg += f"\tsent={_fmt_bytes(uploaded)}\trecv={_fmt_bytes(downloaded)}"
            if client:
                msg += f"\t{client}"
            print(msg)

        def _aliases_path() -> str:
            env = os.environ.get("TORRENTFS_ALIASES")
            if env:
                return env
            home = os.path.expanduser("~")
            return os.path.join(home, ".config", "torrentfs", "aliases.json")

        def _load_aliases() -> dict:
            path = _aliases_path()
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError:
                return {}
            except Exception:
                return {}
            if not isinstance(data, dict):
                return {}
            out = {}
            for key, val in data.items():
                if not isinstance(key, str) or not isinstance(val, str):
                    continue
                label = val.strip()
                if label:
                    out[key] = label
            return out

        def _save_aliases(data: dict) -> None:
            path = _aliases_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        def _normalize_infohash(value: str) -> tuple[str, str]:
            val = value.strip()
            if not val:
                return "", ""
            if "%" in val:
                try:
                    raw = urllib.parse.unquote_to_bytes(val)
                    return raw.hex(), "".join(f"%{b:02x}" for b in raw)
                except Exception:
                    return "", ""
            if len(val) == 40 and re.fullmatch(r"[0-9a-fA-F]{40}", val):
                try:
                    raw = binascii.unhexlify(val)
                    return val.lower(), "".join(f"%{b:02x}" for b in raw)
                except Exception:
                    return "", ""
            return "", ""

        def _torrent_label_map(torrents: list) -> dict:
            dir_map = _build_torrent_dir_map(torrents)
            return {tid: name for name, tid in dir_map.items()}

        def _infohash_hex_from_ti(ti) -> str:
            try:
                ih = ti.info_hashes()
                if getattr(ih, "has_v1", False) and ih.v1:
                    return str(ih.v1)
                if getattr(ih, "has_v2", False) and ih.v2:
                    return str(ih.v2)
            except Exception:
                pass
            try:
                return str(ti.info_hash())
            except Exception:
                return ""

        def _existing_infohashes(torrent_dir: str):
            out = {}
            try:
                names = [n for n in os.listdir(torrent_dir) if n.endswith(".torrent")]
            except FileNotFoundError:
                return out
            for name in names:
                path = os.path.join(torrent_dir, name)
                try:
                    ti = lt.torrent_info(path)
                except Exception:
                    continue
                ih = _infohash_hex_from_ti(ti)
                if ih:
                    out[ih] = path
            return out

        def _resolve_torrent_dir(dir_hint: str) -> str:
            base = os.path.abspath(dir_hint)
            cwd_base = os.path.abspath(os.getcwd())
            if os.path.basename(cwd_base) == "torrents" and os.path.basename(base) == "torrents":
                return cwd_base
            return base

        def _save_torrent_bytes(payload: bytes, out_dir: str, name_hint: str | None = None):
            torrent_dir = os.path.abspath(out_dir)
            os.makedirs(torrent_dir, exist_ok=True)

            base_name = _sanitize_name(name_hint or "arquivo")
            if base_name.endswith(".torrent"):
                out_name = base_name
            else:
                out_name = base_name + ".torrent"
            out_path = os.path.join(torrent_dir, out_name)
            if os.path.exists(out_path):
                suffix = str(int(time.time()))
                base_name = _sanitize_name(name_hint or "arquivo")
                if base_name.endswith(".torrent"):
                    base_name = base_name[:-8]
                out_name = f"{base_name}__{suffix}.torrent"
                out_path = os.path.join(torrent_dir, out_name)

            with open(out_path, "wb") as f:
                f.write(payload)

            _print_ok(f"salvo: {out_path}")
            return out_path

        def _save_torrent_url(url: str, out_dir: str, timeout: int, name_hint: str | None = None):
            try:
                import urllib.request
            except Exception as e:
                _print_error(f"urllib indisponivel: {e}")
                return None
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    data = resp.read()
            except Exception as e:
                _print_error(f"falha ao baixar .torrent: {e}")
                return None
            hint = name_hint
            if not hint:
                try:
                    hint = os.path.basename(urllib.parse.urlparse(url).path) or None
                except Exception:
                    hint = None
            return _save_torrent_bytes(data, out_dir, hint)

        def _save_magnet(magnet: str, out_dir: str, timeout: int):
            if lt is None:
                _print_error("libtorrent nao disponivel no ambiente")
                return None
            torrent_dir = os.path.abspath(out_dir)
            os.makedirs(torrent_dir, exist_ok=True)

            try:
                params = lt.parse_magnet_uri(magnet)
            except Exception as e:
                _print_error(f"magnet invalido: {e}")
                return None

            existing = _existing_infohashes(torrent_dir)
            infohash = ""
            try:
                ih = params.info_hashes
                if getattr(ih, "has_v1", False) and ih.v1:
                    infohash = str(ih.v1)
                elif getattr(ih, "has_v2", False) and ih.v2:
                    infohash = str(ih.v2)
            except Exception:
                pass
            if infohash and infohash in existing:
                _print_ok(f"ja existe: {existing[infohash]}")
                return existing[infohash]

            ses = lt.session()
            ses.listen_on(6881, 6891)
            handle = ses.add_torrent(params)

            start = time.time()
            while not handle.has_metadata():
                if (time.time() - start) > timeout:
                    _print_error("timeout aguardando metadata")
                    return None
                time.sleep(0.2)

            ti = handle.torrent_file()
            infohash = _infohash_hex_from_ti(ti)
            if infohash and infohash in existing:
                _print_ok(f"ja existe: {existing[infohash]}")
                return existing[infohash]

            name = getattr(params, "name", "") or ti.name()
            base = _sanitize_name(name)
            out_name = f"{base}.torrent"
            out_path = os.path.join(torrent_dir, out_name)
            if os.path.exists(out_path):
                suffix = infohash[:12] if infohash else str(int(time.time()))
                out_name = f"{base}__{suffix}.torrent"
                out_path = os.path.join(torrent_dir, out_name)

            try:
                data = lt.bencode(ti.generate())
            except Exception as e:
                _print_error(f"falha ao gerar .torrent: {e}")
                return None

            with open(out_path, "wb") as f:
                f.write(data)

            _print_ok(f"salvo: {out_path}")
            return out_path

        def _handle_source_add(uri: str, out_dir: str, timeout: int):
            plugin = get_plugin_for_uri(uri)
            if not plugin:
                _print_error("nenhum plugin encontrado para a origem")
                return
            try:
                items = plugin.resolve(uri)
            except SourceError as e:
                _print_error(str(e))
                return
            except Exception as e:
                _print_error(f"falha ao resolver origem: {e}")
                return
            if not items:
                _print_error("origem sem resultados")
                return
            for item in items:
                if item.kind == "magnet":
                    _save_magnet(item.value, out_dir, timeout)
                elif item.kind == "torrent_url":
                    _save_torrent_url(item.value, out_dir, timeout, item.name)
                else:
                    _print_error(f"tipo nao suportado: {item.kind}")

        def _print_status_all(resp):
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter status global"))
                return
            totals = resp.get("totals", {})
            torrents = resp.get("torrents", [])
            if not args.human and args.unit != "bytes":
                divisors = {
                    "kb": 1024,
                    "mb": 1024 * 1024,
                    "gb": 1024 * 1024 * 1024,
                }
                d = divisors[args.unit]
                for key in ("downloaded", "uploaded", "download_rate", "upload_rate"):
                    totals[key] = totals.get(key, 0) / d
            if args.human:
                totals["downloaded"] = _fmt_bytes(totals.get("downloaded", 0))
                totals["uploaded"] = _fmt_bytes(totals.get("uploaded", 0))
                totals["download_rate"] = _fmt_rate(totals.get("download_rate", 0))
                totals["upload_rate"] = _fmt_rate(totals.get("upload_rate", 0))
            print(
                "totals: "
                f"downloaded={totals.get('downloaded')} "
                f"uploaded={totals.get('uploaded')} "
                f"download_rate={totals.get('download_rate')} "
                f"upload_rate={totals.get('upload_rate')} "
                f"peers={totals.get('peers')} "
                f"seeds={totals.get('seeds')}"
            )
            for item in torrents:
                tid = item.get("id", "")
                st = item.get("status", {})
                name = st.get("name", "")
                peers = st.get("peers", 0)
                seeds = st.get("seeds", 0)
                progress = st.get("progress", 0)
                if st.get("checking"):
                    chk = st.get("checking_progress")
                    print(f"{tid}\t{name}\tchecking={chk}\tpeers={peers}\tseeds={seeds}\tprogress={progress}")
                else:
                    print(f"{tid}\t{name}\tpeers={peers}\tseeds={seeds}\tprogress={progress}")

        # -----------------------------
        # torrents
        # -----------------------------
        if args.cmd == "alias":
            aliases = _load_aliases()
            if not args.alias_cmd or args.alias_cmd == "list":
                if args.json:
                    _print_json({"ok": True, "aliases": aliases})
                else:
                    for tid, name in aliases.items():
                        print(f"{tid}\t{name}")
                return
            if args.alias_cmd == "set":
                aliases[str(args.id)] = str(args.name).strip()
                _save_aliases(aliases)
                _print_ok(f"alias ok: {args.id}")
            elif args.alias_cmd == "rm":
                aliases.pop(str(args.id), None)
                _save_aliases(aliases)
                _print_ok(f"alias removido: {args.id}")
            return

        if args.cmd == "torrents":
            resp, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar torrents"))
                return
            rows = []
            for t in resp.get("torrents", []):
                tid = str(t.get("id", ""))
                name = str(t.get("name", ""))
                tname = str(t.get("torrent_name", ""))
                cache = str(t.get("cache", ""))
                if args.verbose:
                    rows.append([tid, name, tname, cache])
                else:
                    rows.append([tid, name, tname])
            if not rows:
                return
            widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
            for row in rows:
                line = "  ".join(row[i].ljust(widths[i]) for i in range(len(row)))
                print(line)
            return

        if args.cmd == "config":
            resp, _ = await rpc_call(args.socket, {"cmd": "config"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao ler config"))
                return
            cfg = resp.get("config", {})
            print(f"config_path: {cfg.get('config_path', '')}")
            print(f"max_metadata_bytes: {cfg.get('max_metadata_bytes', '')}")
            trackers = cfg.get("trackers", {})
            if trackers:
                print("trackers:")
                print(f"  enable: {trackers.get('enable')}")
                add_list = trackers.get("add") or []
                if add_list:
                    print("  add:")
                    for item in add_list:
                        print(f"    - {item}")
                aliases = trackers.get("aliases") or {}
                if aliases:
                    print("  aliases:")
                    for key, values in aliases.items():
                        print(f"    {key}: {values}")
            pf = cfg.get("prefetch", {})
            print("prefetch.media:")
            for k, v in pf.get("media", {}).items():
                print(f"  {k}: {v}")
            print("prefetch.other:")
            for k, v in pf.get("other", {}).items():
                print(f"  {k}: {v}")
            return

        if args.cmd == "cache-size":
            resp, _ = await rpc_call(args.socket, {"cmd": "cache-size"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter cache"))
                return
            logical = resp.get("logical_bytes", 0)
            disk = resp.get("disk_bytes", 0)
            print(f"cache_logical: {_fmt_bytes(logical)}")
            print(f"cache_disk: {_fmt_bytes(disk)}")
            return

        if args.cmd == "remove-torrent":
            if not args.torrent:
                _print_error("use --torrent <id>")
                return
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "remove-torrent", "torrent": args.torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if resp.get("ok"):
                _print_ok("removido")
            else:
                _print_error(resp.get("error", "nao removido"))
            return

        if args.cmd == "prune-cache":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "prune-cache", "dry_run": bool(args.dry_run)},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao limpar cache"))
                return
            removed = resp.get("removed", [])
            skipped = resp.get("skipped", 0)
            action = "removidos" if not args.dry_run else "candidatos"
            print(f"{action}: {len(removed)} skipped: {skipped}")
            for tid in removed:
                print(f"  {tid}")
            return

        if args.cmd == "add-magnet":
            _save_magnet(args.magnet, _resolve_torrent_dir(args.dir), args.timeout)
            return

        if args.cmd == "source-add":
            _handle_source_add(args.uri, _resolve_torrent_dir(args.dir), args.timeout)
            return

        if args.cmd == "add-url":
            _save_torrent_url(args.url, _resolve_torrent_dir(args.dir), args.timeout, None)
            return

        if args.cmd == "add-tracker":
            torrent = args.torrent or await get_default_torrent(args.socket, None)
            payload = {"cmd": "add-tracker", "torrent": torrent}
            if args.tracker:
                payload["trackers"] = args.tracker
            resp, _ = await rpc_call(args.socket, payload)
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao adicionar tracker"))
                return
            added = resp.get("added", [])
            skipped = resp.get("skipped", [])
            if added:
                print("adicionados:")
                for url in added:
                    print(f"  {url}")
            if skipped:
                print("ignorados:")
                for url in skipped:
                    print(f"  {url}")
            return

        if args.cmd == "publish-tracker":
            torrent = args.torrent or await get_default_torrent(args.socket, None)
            payload = {"cmd": "publish-tracker", "torrent": torrent}
            if args.tracker:
                payload["trackers"] = args.tracker
            resp, _ = await rpc_call(args.socket, payload)
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao publicar"))
                return
            added = resp.get("added", [])
            skipped = resp.get("skipped", [])
            if added:
                print("adicionados:")
                for url in added:
                    print(f"  {url}")
            if skipped:
                print("ignorados:")
                for url in skipped:
                    print(f"  {url}")
            _print_ok("reannounce ok")
            return

        if args.cmd == "trackers":
            torrent = args.torrent or await get_default_torrent(args.socket, None)
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "trackers", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar trackers"))
                return
            trackers = resp.get("trackers", {}) or {}
            handle = trackers.get("handle", [])
            torrent_list = trackers.get("torrent", [])
            if handle:
                print("handle:")
                for url in handle:
                    print(f"  {url}")
            if torrent_list:
                print("torrent:")
                for url in torrent_list:
                    print(f"  {url}")
            if not handle and not torrent_list:
                print("(nenhum tracker)")
            return

        if args.cmd == "tracker-status":
            torrent = args.torrent or await get_default_torrent(args.socket, None)
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "tracker-status", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar trackers"))
                return
            trackers = resp.get("trackers", []) or []
            if not trackers:
                print("(nenhum tracker)")
                return
            print("url\ttier\tfails\tupdating\tverified\tnext\tmin\tlast\tlast_error")
            for entry in trackers:
                url = entry.get("url", "")
                tier = entry.get("tier", 0)
                fails = entry.get("fails", 0)
                updating = "1" if entry.get("updating") else "0"
                verified = "1" if entry.get("verified") else "0"
                next_a = entry.get("next_announce", "")
                min_a = entry.get("min_announce", "")
                last_a = entry.get("last_announce", "")
                last_err = entry.get("last_error", "")
                print(
                    f"{url}\t{tier}\t{fails}\t{updating}\t{verified}\t{next_a}\t{min_a}\t{last_a}\t{last_err}"
                )
            return

        if args.cmd == "tracker-announce":
            torrent = args.torrent or await get_default_torrent(args.socket, None)
            if not torrent:
                _print_error("use --torrent")
                return
            resp_info, _ = await rpc_call(
                args.socket,
                {"cmd": "torrent-info", "torrent": torrent},
            )
            if not resp_info.get("ok"):
                _print_error(resp_info.get("error", "falha ao obter info do torrent"))
                return
            info = resp_info.get("info", {}) or {}
            infohash = info.get("infohash", "")
            total_size = int(info.get("total_size", 0) or 0)
            if not infohash:
                _print_error("infohash indisponivel")
                return
            ih_url = urllib.parse.quote_from_bytes(bytes.fromhex(infohash))
            tracker = args.tracker
            if not tracker:
                add_list = _load_trackers_from_config()
                tracker = add_list[0] if add_list else None
            if not tracker:
                _print_error("tracker nao configurado (use --tracker ou trackers.add)")
                return
            if tracker.startswith("udp://"):
                tracker = "http://" + tracker[len("udp://"):]
            if not tracker.startswith("http"):
                _print_error("announce suporta apenas trackers HTTP/HTTPS")
                return
            if "/announce" not in tracker:
                tracker = tracker.rstrip("/") + "/announce"
            peer_id = "-TF0001-" + "".join(random.choice("0123456789abcdef") for _ in range(12))
            params = {
                "info_hash": ih_url,
                "peer_id": peer_id,
                "port": str(int(args.port)),
                "uploaded": "0",
                "downloaded": "0",
                "left": str(total_size),
                "compact": "1",
                "event": "started",
                "numwant": "0",
            }
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{tracker}?{query}"
            try:
                import bencodepy
            except Exception as e:
                _print_error(f"bencodepy nao disponivel: {e}")
                return
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    payload = resp.read()
            except Exception as e:
                _print_error(f"falha ao consultar tracker: {e}")
                return
            try:
                data = bencodepy.decode(payload)
            except Exception as e:
                _print_error(f"falha ao decodificar resposta: {e}")
                return
            failure = ""
            if isinstance(data, dict):
                failure = data.get(b"failure reason", b"") or data.get(b"failure_reason", b"")
                if isinstance(failure, bytes):
                    failure = failure.decode("utf-8", "ignore")
            if failure:
                _print_error(f"tracker falhou: {failure}")
                return
            interval = 0
            complete = 0
            incomplete = 0
            if isinstance(data, dict):
                interval = int(data.get(b"interval", 0) or 0)
                complete = int(data.get(b"complete", 0) or 0)
                incomplete = int(data.get(b"incomplete", 0) or 0)
            if args.json:
                _print_json(
                    {
                        "ok": True,
                        "tracker": tracker,
                        "infohash": infohash,
                        "interval": interval,
                        "seeders": complete,
                        "leechers": incomplete,
                    }
                )
                return
            print(f"tracker: {tracker}")
            print(f"infohash: {infohash}")
            print(f"interval: {interval}")
            print(f"seeders: {complete}")
            print(f"leechers: {incomplete}")
            return

        if args.cmd == "status-all":
            resp, _ = await rpc_call(args.socket, {"cmd": "status-all"})
            _print_status_all(resp)
            return

        if args.cmd == "downloads":
            max_files = int(args.max_files)
            payload = {"cmd": "downloads"}
            if max_files > 0:
                payload["max_files"] = max_files
            resp, _ = await rpc_call(args.socket, payload)
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter downloads"))
                return
            torrents = resp.get("torrents", [])
            for item in torrents:
                tid = item.get("id", "")
                st = item.get("status", {})
                name = st.get("name", "")
                peers = st.get("peers", 0)
                seeds = st.get("seeds", 0)
                pieces_done = st.get("pieces_done", 0)
                pieces_total = st.get("pieces_total", 0)
                pieces_missing = st.get("pieces_missing", 0)
                progress = st.get("progress", 0)
                rate = st.get("download_rate", 0)
                print(
                    f"{tid}\t{name}\tpieces={pieces_done}/{pieces_total}\tmissing={pieces_missing}\t"
                    f"rate={rate}\tpeers={peers}\tseeds={seeds}\tprogress={progress}"
                )
                for f in item.get("files", []):
                    fpath = f.get("path", "")
                    pct = f.get("progress_pct", 0.0)
                    remaining = f.get("remaining", 0)
                    size = f.get("size", 0)
                    print(f"  file\t{pct:.2f}%\t{remaining}/{size}\t{fpath}")
            return

        if args.cmd == "uploads" and args.all_torrents:
            label_map = {}
            resp_names, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            if resp_names.get("ok"):
                label_map = _torrent_label_map(resp_names.get("torrents", []))
            resp, _ = await rpc_call(args.socket, {"cmd": "peers-all"})
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter peers"))
                return
            torrents = resp.get("torrents", [])
            for item in torrents:
                tid = item.get("id", "")
                st = item.get("status", {})
                name = label_map.get(tid, st.get("name", ""))
                peers = item.get("peers", [])
                active = sum(
                    1
                    for p in peers
                    if int(p.get("upload_rate", 0)) > 0 or int(p.get("download_rate", 0)) > 0
                )
                if not args.all and active == 0:
                    continue
                _print_peers_summary(tid, name, peers)
                peers_sorted = sorted(
                    peers,
                    key=lambda p: int(p.get("upload_rate", 0)) + int(p.get("download_rate", 0)),
                    reverse=True,
                )
                for p in peers_sorted:
                    up = int(p.get("upload_rate", 0))
                    down = int(p.get("download_rate", 0))
                    if not args.all and up <= 0 and down <= 0:
                        continue
                    _print_peer_line(p)
            return

        if args.cmd == "reannounce-all":
            resp, _ = await rpc_call(args.socket, {"cmd": "reannounce-all"})
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("reannounce-all ok")
                else:
                    _print_error(resp.get("error", "falha ao reannounce-all"))
            return

        async def _resolve_mount_path(path: str, torrent_hint: str | None):
            if not path:
                return torrent_hint, path

            abs_mount = os.path.abspath(args.mount) if args.mount else None
            abs_path = path
            if not os.path.isabs(abs_path):
                abs_path = os.path.abspath(os.path.join(os.getcwd(), path))

            if abs_mount:
                mount_prefix = abs_mount.rstrip(os.sep) + os.sep
                if abs_path != abs_mount and not abs_path.startswith(mount_prefix):
                    return torrent_hint, path

            resp, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            if not resp.get("ok"):
                return torrent_hint, path
            dir_map = _build_torrent_dir_map(resp.get("torrents", []))

            if abs_mount:
                rel = os.path.relpath(abs_path, abs_mount)
                if rel == ".":
                    rel = ""
                if torrent_hint:
                    return torrent_hint, _normalize_path(rel)

                parts = rel.split(os.sep) if rel else []
                if parts and parts[0] in dir_map:
                    tid = dir_map[parts[0]]
                    inner = os.path.join(*parts[1:]) if len(parts) > 1 else ""
                    return tid, _normalize_path(inner)
                return None, _normalize_path(rel)

            # Sem --mount: tenta inferir pelo nome do torrent no caminho absoluto.
            parts = abs_path.split(os.sep)
            for idx, part in enumerate(parts):
                if part in dir_map:
                    tid = dir_map[part]
                    inner = os.path.join(*parts[idx + 1 :]) if idx + 1 < len(parts) else ""
                    return tid, _normalize_path(inner)

            return torrent_hint, path

        if args.cmd == "status" and not args.torrent:
            resp, _ = await rpc_call(args.socket, {"cmd": "status-all"})
            _print_status_all(resp)
            return

        # -----------------------------
        # comandos que exigem torrent
        # -----------------------------
        path_cmds = {"ls", "cat", "pin", "pin-dir", "unpin", "unpin-dir", "prefetch", "du", "file-info", "prefetch-info"}
        src_cmds = {"cp"}
        torrent = args.torrent
        if args.cmd in path_cmds:
            torrent, args.path = await _resolve_mount_path(args.path, torrent)
        if args.cmd in src_cmds:
            torrent, args.src = await _resolve_mount_path(args.src, torrent)

        torrent = await get_default_torrent(args.socket, torrent)

        async def _walk_and_apply(path: str, max_files: int, max_depth: int, apply_fn):
            applied = 0
            errors = []

            def _join_path(parent: str, name: str) -> str:
                if parent in ("", "/"):
                    return name
                if parent.endswith("/"):
                    return f"{parent}{name}"
                return f"{parent}/{name}"

            async def _apply_file(path: str) -> None:
                nonlocal applied
                if max_files > 0 and applied >= max_files:
                    return
                resp, _ = await apply_fn(path)
                if resp.get("ok"):
                    applied += 1
                else:
                    errors.append({"path": path, "error": resp.get("error")})

            async def _walk(path: str, depth: int) -> None:
                if max_files > 0 and applied >= max_files:
                    return
                resp, _ = await rpc_call(
                    args.socket,
                    {"cmd": "stat", "torrent": torrent, "path": path},
                )
                if not resp.get("ok"):
                    errors.append({"path": path, "error": resp.get("error")})
                    return

                st = resp.get("stat", {})
                if st.get("type") == "dir":
                    resp, _ = await rpc_call(
                        args.socket,
                        {"cmd": "list", "torrent": torrent, "path": path},
                    )
                    if not resp.get("ok"):
                        errors.append({"path": path, "error": resp.get("error")})
                        return
                    entries = resp.get("entries", [])
                    for e in entries:
                        if max_files > 0 and applied >= max_files:
                            return
                        child = _join_path(path, e.get("name", ""))
                        if e.get("type") == "dir":
                            if max_depth >= 0 and depth >= max_depth:
                                continue
                            await _walk(child, depth + 1)
                        else:
                            await _apply_file(child)
                    return

                await _apply_file(path)

            await _walk(path, 0)
            return applied, errors

        async def _walk_files(path: str, max_files: int, max_depth: int):
            files = []
            errors = []

            def _join_path(parent: str, name: str) -> str:
                if parent in ("", "/"):
                    return name
                if parent.endswith("/"):
                    return f"{parent}{name}"
                return f"{parent}/{name}"

            async def _walk(path: str, depth: int) -> None:
                if max_files > 0 and len(files) >= max_files:
                    return
                resp, _ = await rpc_call(
                    args.socket,
                    {"cmd": "stat", "torrent": torrent, "path": path},
                )
                if not resp.get("ok"):
                    errors.append({"path": path, "error": resp.get("error")})
                    return

                st = resp.get("stat", {})
                if st.get("type") == "dir":
                    if max_depth >= 0 and depth >= max_depth:
                        return
                    resp, _ = await rpc_call(
                        args.socket,
                        {"cmd": "list", "torrent": torrent, "path": path},
                    )
                    if not resp.get("ok"):
                        errors.append({"path": path, "error": resp.get("error")})
                        return
                    entries = resp.get("entries", [])
                    for e in entries:
                        if max_files > 0 and len(files) >= max_files:
                            return
                        child = _join_path(path, e.get("name", ""))
                        if e.get("type") == "dir":
                            await _walk(child, depth + 1)
                        else:
                            files.append(
                                {
                                    "path": child,
                                    "size": int(e.get("size", 0)),
                                }
                            )
                    return

                files.append({"path": path, "size": int(st.get("size", 0))})

            await _walk(path, 0)
            return files, errors

        if args.cmd == "status":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "status", "torrent": torrent},
            )
            if resp.get("ok") and not args.human and args.unit != "bytes":
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
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter status"))
                return
            st = resp.get("status", {})
            if args.human:
                st["downloaded"] = _fmt_bytes(st.get("downloaded", 0))
                st["uploaded"] = _fmt_bytes(st.get("uploaded", 0))
                st["download_rate"] = _fmt_rate(st.get("download_rate", 0))
                st["upload_rate"] = _fmt_rate(st.get("upload_rate", 0))
            for key in (
                "name",
                "state",
                "progress",
                "peers",
                "seeds",
                "downloaded",
                "uploaded",
                "download_rate",
                "upload_rate",
            ):
                print(f"{key}: {st.get(key)}")
            if st.get("checking"):
                print(f"checking_progress: {st.get('checking_progress')}")

        elif args.cmd == "reannounce":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "reannounce", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("reannounce ok")
                else:
                    _print_error(resp.get("error", "falha ao reannounce"))

        elif args.cmd == "file-info":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "file-info", "torrent": torrent, "path": args.path},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter info"))
                return
            info = resp.get("info", {})
            print(f"path: {info.get('path')}")
            print(f"size: {info.get('size')}")
            print(f"file_index: {info.get('file_index')}")
            print(f"pieces_total: {info.get('pieces_total')}")
            print(f"pieces_done: {info.get('pieces_done')}")
            print(f"pieces_missing: {info.get('pieces_missing')}")

        elif args.cmd == "prefetch-info":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "prefetch-info", "torrent": torrent, "path": args.path},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter prefetch info"))
                return
            info = resp.get("info", {})
            print(f"path: {info.get('path')}")
            print(f"size: {info.get('size')}")
            print(f"prefetch_bytes: {info.get('prefetch_bytes')}")
            print(f"prefetch_pieces: {info.get('prefetch_pieces')}")
            print(f"prefetch_pct: {info.get('prefetch_pct')}")
            ranges = info.get("ranges", [])
            if ranges:
                print("ranges:")
                for r in ranges:
                    print(f"  offset={r.get('offset')} length={r.get('length')}")

        elif args.cmd == "torrent-info":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "torrent-info", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter torrent info"))
                return
            info = resp.get("info", {})
            print("*** BitTorrent File Information ***")
            if info.get("comment"):
                print(f"Comment: {info.get('comment')}")
            if info.get("creation_date_str"):
                print(f"Dated: {info.get('creation_date_str')}")
            if info.get("created_by"):
                print(f"Created by {info.get('created_by')}")
            if info.get("creation_date_str"):
                print(f"Creation Date: {info.get('creation_date_str')}")
            print(f"Mode: {info.get('mode')}")
            trackers = info.get("trackers", [])
            if trackers:
                print("Announce:")
                for tr in trackers:
                    print(f" {tr}")
            if info.get("infohash"):
                print(f"Info Hash: {info.get('infohash')}")
            print(f"Piece Length: {_fmt_bytes(int(info.get('piece_length') or 0))}")
            print(f"The Number of Pieces: {info.get('num_pieces')}")
            print(f"Total Length: {_fmt_bytes(int(info.get('total_size') or 0))}")
            print(f"Name: {info.get('name')}")
            if info.get("magnet"):
                print(f"Magnet URI: {info.get('magnet')}")
            return

        elif args.cmd == "infohash":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "infohash", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter infohash"))
                return
            info = resp.get("info", {})
            v1_hex = info.get("v1_hex", "")
            v1_url = info.get("v1_urlencoded", "")
            v2_hex = info.get("v2_hex", "")
            if not v1_hex and not v2_hex:
                _print_error("infohash indisponivel")
                return
            if v1_hex:
                print(f"v1_hex: {v1_hex}")
            if v1_url:
                print(f"v1_urlencoded: {v1_url}")
            if v2_hex:
                print(f"v2_hex: {v2_hex}")
            return

        elif args.cmd == "tracker-scrape":
            ih_value = args.infohash
            if not ih_value:
                if not args.torrent:
                    _print_error("use --torrent ou informe o infohash")
                    return
                resp_hash, _ = await rpc_call(
                    args.socket,
                    {"cmd": "infohash", "torrent": torrent},
                )
                if not resp_hash.get("ok"):
                    _print_error(resp_hash.get("error", "falha ao obter infohash"))
                    return
                info = resp_hash.get("info", {})
                ih_value = info.get("v1_urlencoded") or info.get("v1_hex", "")
            ih_hex, ih_url = _normalize_infohash(ih_value)
            if not ih_url:
                _print_error("infohash invalido (use hex de 40 chars ou urlencoded)")
                return
            tracker = args.tracker
            if not tracker:
                add_list = _load_trackers_from_config()
                tracker = add_list[0] if add_list else None
            if not tracker:
                _print_error("tracker nao configurado (use --tracker ou trackers.add)")
                return
            if tracker.startswith("udp://"):
                tracker = "http://" + tracker[len("udp://"):]
            if not tracker.startswith("http"):
                _print_error("scrape suporta apenas trackers HTTP/HTTPS")
                return
            if "/announce" in tracker:
                scrape_url = tracker.replace("/announce", "/scrape")
            else:
                scrape_url = tracker.rstrip("/") + "/scrape"
            url = f"{scrape_url}?info_hash={ih_url}"
            try:
                import bencodepy
            except Exception as e:
                _print_error(f"bencodepy nao disponivel: {e}")
                return
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    payload = resp.read()
            except Exception as e:
                _print_error(f"falha ao consultar tracker: {e}")
                return
            try:
                data = bencodepy.decode(payload)
            except Exception as e:
                _print_error(f"falha ao decodificar resposta: {e}")
                return
            files = data.get(b"files", {}) if isinstance(data, dict) else {}
            entry = None
            if ih_url:
                try:
                    key = urllib.parse.unquote_to_bytes(ih_url)
                    entry = files.get(key)
                except Exception:
                    entry = None
            if entry is None and files:
                entry = next(iter(files.values()))
            if entry is None:
                _print_error("scrape sem dados para o infohash")
                return
            out = {
                "complete": int(entry.get(b"complete", 0)),
                "incomplete": int(entry.get(b"incomplete", 0)),
                "downloaded": int(entry.get(b"downloaded", 0)),
                "tracker": tracker,
            }
            if args.json:
                _print_json({"ok": True, "infohash": ih_hex, **out})
                return
            print(f"tracker: {tracker}")
            print(f"infohash: {ih_hex}")
            print(f"seeders: {out['complete']}")
            print(f"leechers: {out['incomplete']}")
            print(f"downloaded: {out['downloaded']}")
            return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter infohash"))
                return
            info = resp.get("info", {})
            v1_hex = info.get("v1_hex", "")
            v1_url = info.get("v1_urlencoded", "")
            v2_hex = info.get("v2_hex", "")
            if v1_hex:
                print(f"v1_hex: {v1_hex}")
            if v1_url:
                print(f"v1_urlencoded: {v1_url}")
            if v2_hex:
                print(f"v2_hex: {v2_hex}")

        elif args.cmd == "ls":
            resp, _ = await rpc_call(
                args.socket,
                {
                    "cmd": "list",
                    "torrent": torrent,
                    "path": args.path,
                },
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar"))
                return
            for e in resp.get("entries", []):
                etype = e.get("type", "")
                size = e.get("size", 0)
                name = e.get("name", "")
                print(f"{etype}\t{size}\t{name}")

        elif args.cmd == "cat":
            if args.wait:
                timeout_s = float(args.timeout)
                retry_sleep = float(args.retry_sleep)
                while True:
                    resp, data = await rpc_call(
                        args.socket,
                        {
                            "cmd": "read",
                            "torrent": torrent,
                            "path": args.path,
                            "offset": args.offset,
                            "size": args.size,
                            "mode": args.mode,
                            "timeout_s": timeout_s,
                        },
                        want_bytes=True,
                    )
                    if resp.get("ok"):
                        os.write(1, data)
                        return
                    err = resp.get("error", "")
                    if "Timeout" in err:
                        await asyncio.sleep(retry_sleep)
                        continue
                    if args.json:
                        _print_json(resp)
                    else:
                        _print_error(resp.get("error", "falha ao ler arquivo"))
                    return
            else:
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
                    if args.json:
                        _print_json(resp)
                    else:
                        _print_error(resp.get("error", "falha ao ler arquivo"))
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
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("pin ok")
                else:
                    _print_error(resp.get("error", "falha ao pinar"))

        elif args.cmd == "pin-dir":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _pin(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "pin", "torrent": torrent, "path": path},
                )

            pinned, errors = await _walk_and_apply(args.path, max_files, max_depth, _pin)
            out = {"ok": len(errors) == 0, "pinned": pinned, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"pinned: {pinned} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "pin-all":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _pin(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "pin", "torrent": torrent, "path": path},
                )

            pinned, errors = await _walk_and_apply("", max_files, max_depth, _pin)
            out = {"ok": len(errors) == 0, "pinned": pinned, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"pinned: {pinned} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "unpin":
            resp, _ = await rpc_call(
                args.socket,
                {
                    "cmd": "unpin",
                    "torrent": torrent,
                    "path": args.path,
                },
            )
            if args.json:
                _print_json(resp)
            else:
                if resp.get("ok"):
                    _print_ok("unpin ok")
                else:
                    _print_error(resp.get("error", "falha ao despinar"))

        elif args.cmd == "unpin-dir":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _unpin(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "unpin", "torrent": torrent, "path": path},
                )

            unpinned, errors = await _walk_and_apply(args.path, max_files, max_depth, _unpin)
            out = {"ok": len(errors) == 0, "unpinned": unpinned, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"unpinned: {unpinned} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "pinned":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "pinned", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao listar pins"))
                return
            for p in resp.get("pins", []):
                status = p.get("status", "")
                pct = p.get("progress_pct", 0)
                size = p.get("size", 0)
                path = p.get("path", "")
                print(f"{status}\t{pct:.2f}%\t{size}\t{path}")

        elif args.cmd == "prefetch":
            max_files = int(args.max_files)
            max_depth = int(args.depth)

            async def _prefetch(path: str):
                return await rpc_call(
                    args.socket,
                    {"cmd": "prefetch", "torrent": torrent, "path": path},
                )

            prefetched, errors = await _walk_and_apply(args.path, max_files, max_depth, _prefetch)
            out = {"ok": len(errors) == 0, "prefetched": prefetched, "errors": errors}
            if args.json:
                _print_json(out)
            else:
                _print_ok(f"prefetched: {prefetched} errors: {len(errors)}")
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "uploads":
            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "peers", "torrent": torrent},
            )
            if args.json:
                _print_json(resp)
                return
            if not resp.get("ok"):
                _print_error(resp.get("error", "falha ao obter peers"))
                return
            peers = resp.get("peers", [])
            label_map = {}
            resp_names, _ = await rpc_call(args.socket, {"cmd": "torrents"})
            if resp_names.get("ok"):
                label_map = _torrent_label_map(resp_names.get("torrents", []))
            label = label_map.get(torrent, args.torrent or torrent)
            _print_peers_summary(torrent, label, peers)
            peers_sorted = sorted(
                peers,
                key=lambda p: int(p.get("upload_rate", 0)) + int(p.get("download_rate", 0)),
                reverse=True,
            )
            for p in peers_sorted:
                up = int(p.get("upload_rate", 0))
                down = int(p.get("download_rate", 0))
                if not args.all and up <= 0 and down <= 0:
                    continue
                _print_peer_line(p)

        elif args.cmd == "du":
            max_depth = int(args.depth)
            files, errors = await _walk_files(args.path, 0, max_depth)
            total = sum(f.get("size", 0) for f in files)
            out = {
                "ok": len(errors) == 0,
                "path": args.path,
                "total_bytes": total,
                "files": len(files),
                "errors": errors,
            }
            if args.json:
                _print_json(out)
            else:
                print(f"ok: {out['ok']}")
                print(f"path: {out['path']}")
                print(f"total_bytes: {out['total_bytes']}")
                print(f"files: {out['files']}")
                if errors:
                    print("errors:")
                    for err in errors:
                        _print_error(f"{err.get('path')}: {err.get('error')}")

        elif args.cmd == "cp":
            max_files = int(args.max_files)
            max_depth = int(args.depth)
            chunk_size = int(args.chunk_size)
            if chunk_size <= 0:
                if args.json:
                    _print_json({"ok": False, "error": "chunk-size invalido"})
                else:
                    _print_error("chunk-size invalido")
                return
            show_progress = bool(args.progress)
            read_timeout = float(args.read_timeout)
            if read_timeout <= 0:
                read_timeout = None

            def _format_eta(seconds: float) -> str:
                if seconds < 0 or seconds == float("inf"):
                    return "?"
                seconds = int(seconds)
                h = seconds // 3600
                m = (seconds % 3600) // 60
                s = seconds % 60
                if h > 0:
                    return f"{h:02d}:{m:02d}:{s:02d}"
                return f"{m:02d}:{s:02d}"

            resp, _ = await rpc_call(
                args.socket,
                {"cmd": "stat", "torrent": torrent, "path": args.src},
            )
            if not resp.get("ok"):
                if args.json:
                    _print_json(resp)
                else:
                    _print_error(resp.get("error", "falha ao ler origem"))
                return

            src_stat = resp.get("stat", {})
            src_is_dir = src_stat.get("type") == "dir"
            dest = args.dest
            copied_bytes = 0
            copied_blocks = 0
            total_bytes = 0
            total_blocks = 0
            start_ts = time.monotonic()
            last_report = start_ts

            def _maybe_report(done: bool = False) -> None:
                nonlocal last_report
                if not show_progress:
                    return
                now = time.monotonic()
                if not done and (now - last_report) < 0.5:
                    return
                last_report = now
                rate = copied_bytes / max(now - start_ts, 1e-6)
                remaining = max(total_bytes - copied_bytes, 0)
                eta = remaining / rate if rate > 0 else float("inf")
                pct = (copied_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
                msg = (
                    f"copiado {copied_bytes}/{total_bytes} bytes ({pct:.2f}%) "
                    f"blocos {copied_blocks}/{total_blocks} eta { _format_eta(eta) }"
                )
                if done:
                    sys.stderr.write("\r" + msg + "\n")
                else:
                    sys.stderr.write("\r" + msg)
                sys.stderr.flush()

            if src_is_dir:
                os.makedirs(dest, exist_ok=True)
                files, errors = await _walk_files(args.src, max_files, max_depth)
                total_bytes = sum(f.get("size", 0) for f in files)
                total_blocks = sum(
                    math.ceil(int(f.get("size", 0)) / chunk_size) for f in files if int(f.get("size", 0)) > 0
                )
                copied = 0
                for item in files:
                    rel = item["path"][len(args.src) :].lstrip("/")
                    target = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    offset = 0
                    size = int(item.get("size", 0))
                    with open(target, "wb") as f:
                        while offset < size:
                            to_read = min(chunk_size, size - offset)
                            resp, data = await rpc_call(
                                args.socket,
                                {
                                    "cmd": "read",
                                    "torrent": torrent,
                                    "path": item["path"],
                                    "offset": offset,
                                    "size": to_read,
                                    "timeout_s": read_timeout,
                                },
                                want_bytes=True,
                            )
                            if not resp.get("ok"):
                                err = resp.get("error", "")
                                if "Timeout" in err:
                                    _maybe_report()
                                    await asyncio.sleep(0.2)
                                    continue
                                errors.append({"path": item["path"], "error": err})
                                break
                            if not data:
                                break
                            f.write(data)
                            offset += len(data)
                            copied_bytes += len(data)
                            copied_blocks += 1
                            _maybe_report()
                    copied += 1
                _maybe_report(done=True)
                out = {
                    "ok": len(errors) == 0,
                    "copied": copied,
                    "copied_bytes": copied_bytes,
                    "total_bytes": total_bytes,
                    "copied_blocks": copied_blocks,
                    "total_blocks": total_blocks,
                    "errors": errors,
                }
                if args.json:
                    _print_json(out)
                else:
                    _print_ok(
                        f"copied: {copied} bytes: {copied_bytes}/{total_bytes} blocks: {copied_blocks}/{total_blocks} errors: {len(errors)}"
                    )
                    for err in errors:
                        _print_error(f"{err.get('path')}: {err.get('error')}")
                return

            if os.path.isdir(dest):
                dest = os.path.join(dest, os.path.basename(args.src))
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            size = int(src_stat.get("size", 0))
            total_bytes = size
            total_blocks = math.ceil(size / chunk_size) if size > 0 else 0
            offset = 0
            errors = []
            with open(dest, "wb") as f:
                while offset < size:
                    to_read = min(chunk_size, size - offset)
                    resp, data = await rpc_call(
                        args.socket,
                        {
                            "cmd": "read",
                            "torrent": torrent,
                            "path": args.src,
                            "offset": offset,
                            "size": to_read,
                            "timeout_s": read_timeout,
                        },
                        want_bytes=True,
                    )
                    if not resp.get("ok"):
                        err = resp.get("error", "")
                        if "Timeout" in err:
                            _maybe_report()
                            await asyncio.sleep(0.2)
                            continue
                        errors.append({"path": args.src, "error": err})
                        break
                    if not data:
                        break
                    f.write(data)
                    offset += len(data)
                    copied_bytes += len(data)
                    copied_blocks += 1
                    _maybe_report()
            _maybe_report(done=True)
            out = {
                "ok": len(errors) == 0,
                "copied": 1 if not errors else 0,
                "copied_bytes": copied_bytes,
                "total_bytes": total_bytes,
                "copied_blocks": copied_blocks,
                "total_blocks": total_blocks,
                "errors": errors,
            }
            if args.json:
                _print_json(out)
            else:
                _print_ok(
                    f"copied: {out['copied']} bytes: {copied_bytes}/{total_bytes} blocks: {copied_blocks}/{total_blocks} errors: {len(errors)}"
                )
                for err in errors:
                    _print_error(f"{err.get('path')}: {err.get('error')}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
