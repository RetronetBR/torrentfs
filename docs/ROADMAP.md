## v0.1 (current)

- Daemon with multi-torrent support
- Directory watcher for .torrent files
- RPC with list/stat/read/pin/pinned
- CLI for testing
- Pins persistence

## v0.2

- FUSE read-only driver (implementação inicial disponível; seguir refinando conforme testes)
- Basic getattr/readdir/open/read mapping
- Stream-friendly read path

## v0.3

- Path index optimization for large torrents
- Improved error handling and metrics
- Optional cache eviction policies

## v0.4

- Advanced pin management (unpin, bulk pin, import/export)
- Access control for socket (optional)

## v1.0

- Stable API
- Documentation and packaging
