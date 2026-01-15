# Instalacao

Requisitos:
- Python 3.12
- libtorrent (biblioteca do sistema)
- FUSE/fuse3 (para montar)

Instalacao (modo desenvolvimento):

```bash
python3 -m pip install -e .
```

Comandos instalados no PATH:
- `torrentfs` (CLI)
- `torrentfsd` (daemon)
- `torrentfs-fuse` (FUSE)

Uso rapido:

```bash
torrentfsd --torrent /path/file.torrent --cache ./cache --socket /tmp/torrentfsd.sock
torrentfs torrents
torrentfs-fuse --torrent <id|name> --mount /mnt/torrentfs --mode auto --foreground
```
