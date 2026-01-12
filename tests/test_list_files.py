import libtorrent as lt

ti = lt.torrent_info("torrents/datassette-20250701-Livros.torrent")

for f in ti.files():
    print(f"{f.path} ({f.size} bytes)")
