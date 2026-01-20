# Instalacao

Requisitos:
- Python 3.10 a 3.12
- libtorrent (biblioteca do sistema)
- FUSE/fuse3 (para montar)

## Dependencias do sistema

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3-full python3-venv libtorrent-rasterbar2.0 python3-libtorrent fuse3
```

Arch/Manjaro:

```bash
sudo pacman -Syu python libtorrent-rasterbar python-libtorrent fuse3
```

Fedora (somente dependencias do sistema; sem pacote RPM por enquanto):

```bash
sudo dnf install -y python3 python3-virtualenv libtorrent-rasterbar python3-libtorrent fuse3
```

macOS (Homebrew, nao testado):

```bash
brew install python libtorrent-rasterbar macfuse
```

Observacao:
- Em Linux, para permitir `--allow-other`, habilite `user_allow_other` em `/etc/fuse.conf`.
- O arquivo de configuracao e lido em: `$TORRENTFSD_CONFIG`, `$HOME/.config/torrentfs/torrentfsd.json`, `/etc/torrentfs/torrentfsd.json`.
 - Raspberry Pi (ARM64) e compativel com sistemas Debian; testes foram feitos em ARM64.
   Em caso de problemas, reporte para `torrentfs@retronet.com.br`.

Referencias Linux:
- FUSE: https://github.com/libfuse/libfuse
- fusepy: https://github.com/fusepy/fusepy
- libtorrent: https://www.libtorrent.org/

## Windows (em desenvolvimento)

O cliente atual usa FUSE (via fusepy), entao no Windows seria necessario usar um driver especifico:
- WinFsp: https://winfsp.dev/
- Dokan (alternativa): https://dokan-dev.github.io/

Libtorrent no Windows:
- https://www.libtorrent.org/

## Instalacao via pipx (recomendado)

```bash
pipx install .
```

Se estiver instalando direto do repositorio remoto:

```bash
pipx install git+<url-do-repo>
```

## systemd (usuario)

Instala o servico para o usuario atual:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/systemd/torrentfs.service ~/.config/systemd/user/torrentfs.service
systemctl --user daemon-reload
systemctl --user enable --now torrentfs.service
```

Diretorios usados por padrao:
- `~/.local/share/torrentfs/torrents`
- `~/.local/share/torrentfs/cache`

Socket padrao do servico:
- `$XDG_RUNTIME_DIR/torrentfsd.sock`

Exemplo:

```bash
torrentfs --socket "$XDG_RUNTIME_DIR/torrentfsd.sock" torrents
```

## Instalacao via pacote .deb (futuro)

```bash
sudo apt install ./torrentfs_0.1.0_all.deb
```

## Build do pacote .deb

```bash
./scripts/build_deb.sh
```

Pacotes RPM ainda nao estao disponiveis.
Instalacao no macOS ainda nao foi validada.

## Instalacao (modo desenvolvimento):

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -U pip setuptools wheel
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
