# TorrentFS

Read-only P2P filesystem built on BitTorrent.

## Config

Arquivo: `config/torrentfsd.json`

```json
{
  "max_metadata_mb": 100
}
```

Tambem pode apontar outro arquivo via `TORRENTFSD_CONFIG`.

## Daemon

Single torrent:

```bash
python -m daemon.main --torrent /path/file.torrent --cache ./cache --socket /tmp/torrentfsd.sock
```

Monitor a directory of torrents:

```bash
python -m daemon.main --torrent-dir /path/torrents --cache ./cache --socket /tmp/torrentfsd.sock
```

## CLI

List loaded torrents:

```bash
python -m cli.main --socket /tmp/torrentfsd.sock torrents
```

Status:

```bash
python -m cli.main --socket /tmp/torrentfsd.sock --torrent <id|name> status
```

List directory:

```bash
python -m cli.main --socket /tmp/torrentfsd.sock --torrent <id|name> ls [path]
```

Read file bytes:

```bash
python -m cli.main --socket /tmp/torrentfsd.sock --torrent <id|name> cat <path> --offset 0 --size 65536 --mode auto
```

Pin file:

```bash
python -m cli.main --socket /tmp/torrentfsd.sock --torrent <id|name> pin <path>
```

List pinned files:

```bash
python -m cli.main --socket /tmp/torrentfsd.sock --torrent <id|name> pinned
```

## FUSE (read-only)

Pré-requisitos: fuse/fuse3 instalado no sistema, usuário com permissão para montar e `fusepy` instalado (já em `requirements.txt`).

Montar (modo foreground para debug):
```bash
python -m fuse.fs --socket /tmp/torrentfsd.sock --torrent <id|name> --mount /mnt/torrentfs --mode auto --foreground
```

Opções úteis:
- `--allow-other`: permite que outros usuários leiam (requer `user_allow_other` em `/etc/fuse.conf`).
- `--uid/--gid`: força UID/GID apresentados nos arquivos (default: do usuário que executa ou SUDO_UID/SUDO_GID).

Desmontar:
- Linux: `fusermount -u /mnt/torrentfs`
- macOS/FreeBSD: `umount /mnt/torrentfs`
