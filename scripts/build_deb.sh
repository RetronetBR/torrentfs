#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pkg_name="torrentfs"

version="$(awk -F'=' '/^version[[:space:]]*=/{gsub(/[[:space:]]*"/,"",$2); gsub(/"/,"",$2); print $2}' "$root_dir/pyproject.toml")"
if [[ -z "${version}" ]]; then
  echo "erro: versao nao encontrada em pyproject.toml" >&2
  exit 1
fi

arch="all"
build_root="$root_dir/dist/deb/${pkg_name}_${version}_${arch}"
deb_root="$build_root/DEBIAN"
opt_root="$build_root/opt/$pkg_name"
bin_root="$build_root/usr/bin"
etc_root="$build_root/etc/torrentfs"

rm -rf "$build_root"
mkdir -p "$deb_root" "$opt_root" "$bin_root" "$etc_root"

rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude "cache" \
  --exclude "mnt" \
  --exclude "torrents" \
  --exclude "dist" \
  --exclude "build" \
  --exclude "*.egg-info" \
  "$root_dir/" "$opt_root/"

cp "$root_dir/config/torrentfsd.json" "$etc_root/torrentfsd.json"

cat >"$bin_root/torrentfs" <<'EOF'
#!/usr/bin/env sh
PYTHONPATH="/opt/torrentfs" exec python3 -m cli.main "$@"
EOF

cat >"$bin_root/torrentfsd" <<'EOF'
#!/usr/bin/env sh
PYTHONPATH="/opt/torrentfs" exec python3 -m daemon.main "$@"
EOF

cat >"$bin_root/torrentfs-fuse" <<'EOF'
#!/usr/bin/env sh
PYTHONPATH="/opt/torrentfs" exec python3 -m torrentfs_fuse.fs "$@"
EOF

chmod 0755 "$bin_root/torrentfs" "$bin_root/torrentfsd" "$bin_root/torrentfs-fuse"

cat >"$deb_root/control" <<EOF
Package: $pkg_name
Version: $version
Section: utils
Priority: optional
Architecture: $arch
Depends: python3 (>= 3.12), python3-libtorrent, libtorrent-rasterbar2.0, fuse3
Maintainer: TorrentFS <dev@torrentfs.local>
Description: Read-only P2P filesystem built on BitTorrent.
 TorrentFS provides a read-only filesystem backed by BitTorrent data.
EOF

dpkg-deb --build "$build_root"
echo "ok: criado ${build_root}.deb"
