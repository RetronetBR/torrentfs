# cli/client.py
import asyncio
import uuid
from common.rpc import send_json, recv_json, recv_bytes


async def _open_socket(sock):
    return await asyncio.open_unix_connection(sock)


async def rpc_call(sock, payload, want_bytes=False):
    sockets = sock if isinstance(sock, (list, tuple)) else [sock]
    last_err = None
    reader = writer = None
    for candidate in sockets:
        try:
            reader, writer = await _open_socket(candidate)
            last_err = None
            break
        except (FileNotFoundError, ConnectionRefusedError) as e:
            last_err = e
            continue
    if last_err is not None:
        raise last_err
    if reader is None or writer is None:
        raise ConnectionError("SocketUnavailable")
    payload["id"] = payload.get("id", uuid.uuid4().hex)

    await send_json(writer, payload)
    resp = await recv_json(reader)

    data = b""
    if want_bytes and resp.get("ok") and resp.get("data_len", 0) > 0:
        data = await recv_bytes(reader, resp["data_len"])

    writer.close()
    await writer.wait_closed()
    return resp, data
