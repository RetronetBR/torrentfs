# Installation

Requirements:
- Python 3.10 to 3.12
- libtorrent (system library)
- FUSE/fuse3 (for mount)

## System dependencies

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3-full python3-venv libtorrent-rasterbar2.0 python3-libtorrent fuse3
```

Arch/Manjaro:

```bash
sudo pacman -Syu python libtorrent-rasterbar python-libtorrent fuse3
```

Fedora (system deps only; no RPM package yet):

```bash
sudo dnf install -y python3 python3-virtualenv libtorrent-rasterbar python3-libtorrent fuse3
```

macOS (Homebrew, untested):

```bash
brew install python libtorrent-rasterbar macfuse
```

Notes:
- On Linux, to allow `--allow-other`, enable `user_allow_other` in `/etc/fuse.conf`.
- Config is read in: `$TORRENTFSD_CONFIG`, `$HOME/.config/torrentfs/torrentfsd.json`, `/etc/torrentfs/torrentfsd.json`.
 - Raspberry Pi (ARM64) is compatible with Debian-based systems; tests ran on ARM64.
   If you hit issues, report to `torrentfs@retronet.com.br`.

Linux references:
- FUSE: https://github.com/libfuse/libfuse
- fusepy: https://github.com/fusepy/fusepy
- libtorrent: https://www.libtorrent.org/

## Windows (in development)

The current client uses FUSE (via fusepy), so on Windows you need a specific driver:
- WinFsp: https://winfsp.dev/
- Dokan (alternative): https://dokan-dev.github.io/

Libtorrent on Windows:
- https://www.libtorrent.org/

## Install via pipx (recommended)

```bash
pipx install .
```

If installing directly from a remote repo:

```bash
pipx install git+<repo-url>
```

## systemd (user)

Install the service for the current user:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/systemd/torrentfs.service ~/.config/systemd/user/torrentfs.service
systemctl --user daemon-reload
systemctl --user enable --now torrentfs.service
```

Default directories:
- `~/.local/share/torrentfs/torrents`
- `~/.local/share/torrentfs/cache`

Default socket:
- `$XDG_RUNTIME_DIR/torrentfsd.sock`

Example:

```bash
torrentfs --socket "$XDG_RUNTIME_DIR/torrentfsd.sock" torrents
```

## Install via .deb package (future)

```bash
sudo apt install ./torrentfs_0.1.0_all.deb
```

## Build .deb package

```bash
./scripts/build_deb.sh
```

RPM packages are not available yet.
macOS install has not been validated yet.

## Install (development mode):

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e .
```

Commands installed in PATH:
- `torrentfs` (CLI)
- `torrentfsd` (daemon)
- `torrentfs-fuse` (FUSE)

Quick usage:

```bash
torrentfsd --torrent /path/file.torrent --cache ./cache --socket /tmp/torrentfsd.sock
torrentfs torrents
torrentfs-fuse --torrent <id|name> --mount /mnt/torrentfs --mode auto --foreground
```
