# Repository Guidelines

## Project Structure & Module Organization

- `daemon/`: core service (torrent manager, watcher, RPC server, engine).
- `cli/`: command-line client used for testing and debugging.
- `fuse/`: FUSE client (currently placeholder).
- `common/`: shared RPC framing helpers.
- `docs/`: architecture, protocol, and roadmap notes.
- `tests/`: integration-style scripts (libtorrent required).
- `config/`: runtime configuration (e.g., metadata limits).
- `torrents/` and `cache/`: local data, ignored by git.

## Build, Test, and Development Commands

- Start daemon (single torrent):
  `python3 -m daemon.main --torrent /path/file.torrent --cache cache --socket /tmp/torrentfsd.sock`
- Start daemon (multi-torrent directory):
  `python3 -m daemon.main --torrent-dir torrents --cache cache --socket /tmp/torrentfsd.sock`
- List torrents:
  `python3 -m cli.main --socket /tmp/torrentfsd.sock torrents`
- List files in a torrent:
  `python3 -m cli.main --torrent <id> ls [path]`
- Read bytes:
  `python3 -m cli.main --torrent <id> cat <path> --offset 0 --size 65536`
- Pin and list pins:
  `python3 -m cli.main --torrent <id> pin <path>`
  `python3 -m cli.main --torrent <id> pinned`

## Coding Style & Naming Conventions

- Python 3.12, 4-space indentation.
- Modules and functions use `snake_case`; classes use `CamelCase`.
- Prefer small helpers in `daemon/engine.py` for libtorrent behavior.
- Keep RPC request/response keys short and consistent.

## Testing Guidelines

- Tests are Python scripts in `tests/` (libtorrent required).
- Run list test: `python3 tests/test_list_files.py`
- `tests/test_read_file.py` downloads data and may block; use only when needed.
- No formal coverage target yet.

## Commit & Pull Request Guidelines

- Commit messages are short, imperative, and scoped (e.g., “Add multi-torrent support and config”).
- PRs should include: purpose summary, key changes, and test results or reason not run.

## Configuration Notes

- Config file: `config/torrentfsd.json`
- Example:
  ```json
  { "max_metadata_mb": 100 }
  ```
- Override path with `TORRENTFSD_CONFIG`.
