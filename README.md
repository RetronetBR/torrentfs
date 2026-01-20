# TorrentFS

TorrentFS e um filesystem read-only sobre BitTorrent. O daemon gerencia os torrents
via libtorrent e expoe um RPC local; a CLI e o cliente FUSE usam esse RPC para
navegar, ler e fazer prefetch de arquivos enquanto o download acontece.

Use `INSTALL.en.md` ou `INSTALL.pt.md` para instrucoes de instalacao.

## Plataformas suportadas

- Linux (Debian/Ubuntu, Arch/Manjaro, Fedora)
- ARM64 (Raspberry Pi, Debian/Ubuntu): testes realizados em ARM64
- Outras distros Linux devem funcionar se houver libtorrent e FUSE

Em caso de problemas, reporte para `torrentfs@retronet.com.br`.

## Instalacao

Via pipx (recomendado):

```bash
pipx install .
```

Via pacote .deb:

```bash
sudo apt install ./torrentfs_0.1.30_all.deb
```

### systemd (usuario) apos instalar o .deb

```bash
mkdir -p ~/torrentfs/torrents ~/torrentfs/cache
mkdir -p ~/.config/systemd/user
cp /opt/torrentfs/scripts/systemd/torrentfs.service ~/.config/systemd/user/torrentfs.service
systemctl --user daemon-reload
systemctl --user enable --now torrentfs.service
```

Para parar o servico:

```bash
systemctl --user stop torrentfs.service
```

Socket padrao do servico:

```bash
${XDG_RUNTIME_DIR}/torrentfsd.sock
```

## Uso rapido

### 1) Coloque seus arquivos .torrent

Modo multi-torrent (diretorio monitorado):

```bash
mkdir -p ~/torrentfs/torrents ~/torrentfs/cache
cp /caminho/para/*.torrent ~/torrentfs/torrents/
```

Modo single-torrent (um arquivo .torrent):

```bash
cp /caminho/para/arquivo.torrent ~/torrentfs/
```

### 2) Inicie o daemon

Multi-torrent (monitorando diretorio):

```bash
torrentfsd --torrent-dir ~/torrentfs/torrents --cache ~/torrentfs/cache --socket /tmp/torrentfsd.sock
```

Single-torrent:

```bash
torrentfsd --torrent ~/torrentfs/arquivo.torrent --cache ~/torrentfs/cache --socket /tmp/torrentfsd.sock
```

### 3) Use a CLI

```bash
torrentfs torrents
torrentfs --torrent <id|name> ls
torrentfs --torrent <id|name> cat <path> --offset 0 --size 65536 --mode auto
```

### 4) Monte via FUSE (opcional)

```bash
torrentfs-fuse --torrent <id|name> --mount /mnt/torrentfs --mode auto
```

## Config

Arquivo (ordem de prioridade):
- `$TORRENTFSD_CONFIG`
- `$HOME/.config/torrentfs/torrentfsd.json`
- `/etc/torrentfs/torrentfsd.json`
- `config/torrentfsd.json` (fallback no repo)

```json
{
  "max_metadata_mb": 100,
  "skip_check": false,
  "checking": {
    "max_active": 1
  },
  "resume": {
    "save_interval_s": 300
  },
  "trackers": {
    "aliases": {
      "torrentfs://bootstrap": [
        "udp://tracker.retronet.org:6969/announce"
      ]
    }
  },
  "prefetch": {
    "on_start": false,
    "on_start_mode": "media",
    "max_mb": 0,
    "max_files": 0,
    "sleep_ms": 25,
    "batch_size": 10,
    "batch_sleep_ms": 200,
    "scan_sleep_ms": 5,
    "max_dirs": 0,
    "media": {
      "extensions": [
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".m4v",
        ".webm",
        ".mp3",
        ".flac",
        ".aac",
        ".ogg",
        ".wav",
        ".pdf",
        ".epub",
        ".cbz",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif"
      ],
      "start_pct": 0.10,
      "end_pct": 0.02,
      "start_min_mb": 4,
      "start_max_mb": 64,
      "end_min_mb": 1,
      "end_max_mb": 16
    },
    "other": {
      "start_pct": 0.10,
      "end_pct": 0.05,
      "start_min_mb": 1,
      "start_max_mb": 32,
      "end_min_mb": 1,
      "end_max_mb": 16
    }
  }
}
```

Tambem pode apontar outro arquivo via `TORRENTFSD_CONFIG`.

## Instalacao (pipx)

Recomendado para uso local com isolamento de dependencias:
```bash
pipx install .
```

## Daemon

Single torrent:

```bash
torrentfsd --torrent /path/file.torrent --cache ./cache --socket /tmp/torrentfsd.sock --prefetch --skip-check
```

Monitor a directory of torrents:

```bash
torrentfsd --torrent-dir /path/torrents --cache ./cache --socket /tmp/torrentfsd.sock --prefetch --skip-check
```

Observacao: ao remover um arquivo .torrent do diretorio monitorado, o daemon remove o torrent e limpa o cache correspondente.

## CLI

Comandos no PATH quando instalado via pipx:
- `torrentfs` (CLI)
- `torrentfsd` (daemon)
- `torrentfs-fuse` (FUSE)

Opcional: use `--socket` para apontar outro socket quando houver mais de um daemon.
Socket padrao: `$TORRENTFSD_SOCKET`, ou `$XDG_RUNTIME_DIR/torrentfsd.sock` (se existir), com fallback para `/tmp/torrentfsd.sock` se o socket nao responder.
Opcional: use `--mount` para permitir paths do filesystem (ex.: `/mnt/torrentfs/...`) em comandos com `path`.
Opcional: use `--json` para forcar saida em JSON.
Opcional: em modo desenvolvimento, use `python -m cli.main` no lugar de `torrentfs`.

List loaded torrents:

```bash
torrentfs torrents
```

Add magnet (salva .torrent em `torrents/`):

```bash
torrentfs add-magnet "<magnet:...>"
```

Adicionar fonte via plugin (ex.: magnet):

```bash
torrentfs source-add "magnet:?xt=urn:btih:..."
```

Adicionar fonte do archive.org (ID ou URL):

```bash
torrentfs source-add "archive:revistasabereletronica089fev1980"
torrentfs source-add "https://archive.org/details/revistasabereletronica089fev1980"
```

Show daemon config (effective values):

```bash
torrentfs config
```

Cache size:

```bash
torrentfs cache-size
```

Prune cache (remove torrents sem referencia ativa):

```bash
torrentfs prune-cache
```

Dry-run:

```bash
torrentfs prune-cache --dry-run
```

Status:

```bash
torrentfs --torrent <id|name> status
```

Status (todos os torrents):

```bash
torrentfs status-all
```

Downloads em execucao:

```bash
torrentfs downloads --max-files 20
```

Uploads em execucao (peers com transferencia):

```bash
torrentfs --torrent <id|name> uploads
```

Todos os peers (sem filtro de transferencia):

```bash
torrentfs --torrent <id|name> uploads --all
```

Todos os torrents:

```bash
torrentfs uploads --all-torrents
```

Forcar announce (um torrent):

```bash
torrentfs --torrent <id|name> reannounce
```

Forcar announce (todos os torrents):

```bash
torrentfs reannounce-all
```

Info de arquivo (pieces):

```bash
torrentfs --torrent <id|name> file-info <path>
```

Info de prefetch (bytes/pieces):

```bash
torrentfs --torrent <id|name> prefetch-info <path>
```

List directory:

```bash
torrentfs --torrent <id|name> ls [path]
```

Read file bytes:

```bash
torrentfs --torrent <id|name> cat <path> --offset 0 --size 65536 --mode auto
```

Cat aguardando download:

```bash
torrentfs --torrent <id|name> cat <path> --offset 0 --size 65536 --mode auto --wait
```

Copy from mount to local disk:

```bash
torrentfs --torrent <id|name> cp <src> <dest> --chunk-size 1048576 --progress --read-timeout 1
```

Disk usage (soma dos arquivos):

```bash
torrentfs --torrent <id|name> du [path] --depth 2
```

Pin file:

```bash
torrentfs --torrent <id|name> pin <path>
```

Pin directory (recursive):

```bash
torrentfs --torrent <id|name> pin-dir <path> --max-files 100 --depth 2
```

Unpin file:

```bash
torrentfs --torrent <id|name> unpin <path>
```

Unpin directory (recursive):

```bash
torrentfs --torrent <id|name> unpin-dir <path> --max-files 100 --depth 2
```

Prefetch file or directory (recursive):

```bash
torrentfs --torrent <id|name> prefetch <path> --max-files 100 --depth 2
```

List pinned files:

```bash
torrentfs --torrent <id|name> pinned
```
## FUSE (read-only)

Pré-requisitos: fuse/fuse3 instalado no sistema, usuário com permissão para montar e `fusepy` instalado (já em `requirements.txt`).

Montar:
```bash
torrentfs-fuse --torrent <id|name> --mount /mnt/torrentfs --mode auto
```

Opções úteis:
- `--allow-other`: permite que outros usuários leiam (requer `user_allow_other` em `/etc/fuse.conf`).
- `--uid/--gid`: força UID/GID apresentados nos arquivos (default: do usuário que executa ou SUDO_UID/SUDO_GID).
- `--stat-ttl`: TTL do cache de `stat` (segundos).
- `--list-ttl`: TTL do cache de `list` (segundos).
- `--readdir-prefetch`: prefetch de N arquivos ao listar diretórios.
- `--readdir-prefetch-mode`: `media` ou `all`.
- `--timeout`: timeout por leitura (segundos). Em falta de peers, retorna EAGAIN para evitar travas no file manager.

Modo multi-torrent:
- Se `--torrent` não for informado, o root do mount lista um diretório por torrent carregado.
- Diretórios usam o nome do arquivo `.torrent` (sem extensão) quando único; se houver duplicados, o formato vira `nome__<id>`.

Pre-cache:
- Ao abrir um arquivo via FUSE ou usar o comando `prefetch`, o daemon prioriza o inicio e o final do arquivo para acelerar streaming e visualizacao.
- Arquivos pinados continuam com prioridade total e tendem a baixar por completo.
- Percentuais aceitam valores em `0-1` (ex.: `0.10`) ou `0-100` (ex.: `10`).
- A lista `prefetch.media.extensions` define quais extensoes usam o perfil de midia (ex.: `.pdf`, `.epub`).
- `prefetch.on_start` ativa o prefetch automatico ao carregar torrents (tambem pode ser forçado com `--prefetch`).
- `prefetch.on_start_mode` pode ser `media` (somente extensoes configuradas) ou `all`.
- `prefetch.max_mb` limita o total de bytes prefetchados por torrent (0 = ilimitado).
- `prefetch.max_files` limita quantos arquivos sao prefetchados por torrent (0 = ilimitado).
- `prefetch.sleep_ms` adiciona pausa entre arquivos para reduzir uso de CPU.
- `prefetch.batch_size` define quantos arquivos sao processados por lote.
- `prefetch.batch_sleep_ms` adiciona pausa entre lotes.
- `prefetch.scan_sleep_ms` adiciona pausa entre diretorios durante a varredura.
- `prefetch.max_dirs` limita quantos diretorios sao varridos por torrent (0 = ilimitado).
- `skip_check` pula verificacao de hash ao carregar torrents (mais rapido, menos seguro).
- `checking.max_active` limita quantos torrents ficam em `checking_files` ao mesmo tempo (0 = default do libtorrent).
- `resume.save_interval_s` salva resume data periodicamente para reduzir `checking_files` no restart (0 = desliga).

Desmontar:
- Linux: `fusermount -u /mnt/torrentfs`
- macOS/FreeBSD: `umount /mnt/torrentfs`
