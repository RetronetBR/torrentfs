RPC uses JSON frames over a Unix Domain Socket.

## Framing

Each JSON message is prefixed with a 4-byte big-endian length.

## Request

All requests support an optional `id` field. The server echoes `id` in responses.

Common fields:
- `cmd`: command name
- `torrent`: torrent ID or name (required for per-torrent commands)

## Response

All responses include:
- `id`
- `ok`: boolean
- `error`: present when `ok` is false

## Commands

### hello
Request:
```json
{"cmd":"hello"}
```
Response:
```json
{"ok":true,"torrents":[...]}
```

### torrents
List loaded torrents.

Request:
```json
{"cmd":"torrents"}
```
Response:
```json
{"ok":true,"torrents":[{"id":"...","name":"...","torrent_name":"...","cache":"..."}]}
```

### status
Request:
```json
{"cmd":"status","torrent":"<id|name>"}
```

### list
Request:
```json
{"cmd":"list","torrent":"<id|name>","path":""}
```
Response:
```json
{"ok":true,"entries":[{"name":"...","type":"dir|file","size":123}]}
```

### stat
Request:
```json
{"cmd":"stat","torrent":"<id|name>","path":"..."}
```

### read
Request:
```json
{"cmd":"read","torrent":"<id|name>","path":"...","offset":0,"size":65536,"mode":"auto","timeout_s":null}
```
Response header:
```json
{"ok":true,"data_len":1234}
```
Followed by `data_len` raw bytes.

### pin
Request:
```json
{"cmd":"pin","torrent":"<id|name>","path":"..."}
```

### pinned
Request:
```json
{"cmd":"pinned","torrent":"<id|name>"}
```
Response:
```json
{"ok":true,"pins":[{"path":"...","file_name":"...","torrent_name":"...","size":123}]}
```

## Errors

Typical errors:
- `TorrentRequired`
- `TorrentNotFound:<id>`
- `TorrentNameAmbiguous:<name>`
- `ReadSizeInvalid`
- `FileNotFound`
- `NotADirectory`
- `IsADirectory`
