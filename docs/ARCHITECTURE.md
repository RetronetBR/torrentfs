Architecture is daemon-centric. The daemon owns libtorrent sessions and exposes a local RPC
interface. The CLI and (future) FUSE driver are thin clients.

## Components

- daemon: multi-torrent manager, watcher, and RPC server.
- engine: wraps libtorrent session + torrent handle, exposes list/stat/read/pin.
- watcher: monitors a directory for .torrent files and loads them.
- rpc: JSON framing over Unix Domain Socket.
- cli: testing tool for status/list/read/pin/pinned.
- fuse: read-only filesystem client (placeholder).

## Data flow

1) Client issues RPC over Unix Domain Socket.
2) Server resolves torrent ID/name -> engine.
3) Engine loads torrent info, builds path index, and serves list/stat/read.
4) Reads prioritize needed pieces and block until available.
5) Optional pins are persisted in cache per torrent.

## Storage

- cache root: set by `--cache` (default: `./cache`)
- per torrent cache: `cache/<torrent_id>/`
- pins persistence: `cache/<torrent_id>/.pinned.json`

## Concurrency

- RPC server is async; blocking reads are executed in a thread.
- TorrentManager uses an internal lock for thread safety with watcher.

## Boundaries

- Daemon is authoritative for torrent state.
- CLI and FUSE should never touch libtorrent directly.
