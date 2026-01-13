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
