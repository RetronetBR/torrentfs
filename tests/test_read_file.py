import libtorrent as lt
import time
import os
import sys

TORRENT = "torrents/datassette-20250701-Livros.torrent"
CACHE   = "./cache"

TARGET_PATH = (
    "Datassette/Livros/Pense Bem/BR - Brasil/"
    "pense_bem_-_disney_-_rumo_as_estrelas.pdf"
)

os.makedirs(CACHE, exist_ok=True)

ses = lt.session()
ses.listen_on(6881, 6891)

info = lt.torrent_info(TORRENT)

h = ses.add_torrent({
    "ti": info,
    "save_path": CACHE,
    "storage_mode": lt.storage_mode_t.storage_mode_sparse
})

# Desprioriza tudo
for i in range(info.num_files()):
    h.file_priority(i, 0)

# Resolver índice pelo path
file_index = None
for i, f in enumerate(info.files()):
    if f.path == TARGET_PATH:
        file_index = i
        file_size  = f.size
        break

if file_index is None:
    raise SystemExit("Arquivo não encontrado no torrent")

print(f"Baixando sob demanda:\n{TARGET_PATH}", file=sys.stderr)

# Prioriza o arquivo
h.file_priority(file_index, 1)

# Aguarda download completo do arquivo
while h.file_progress()[file_index] < file_size:
    s = h.status()
    print(
        f"{h.file_progress()[file_index]}/{file_size} bytes | peers: {s.num_peers}",
        file=sys.stderr
    )
    time.sleep(1)

# Caminho real no cache
real_path = os.path.join(CACHE, TARGET_PATH)

print(f"\nArquivo disponível em cache:\n{real_path}", file=sys.stderr)

# CAT real (stdout)
with open(real_path, "rb") as f:
    while True:
        chunk = f.read(8192)
        if not chunk:
            break
        os.write(1, chunk)
