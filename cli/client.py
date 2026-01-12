# cli/client.py
import asyncio
import uuid
from common.rpc import send_json, recv_json, recv_bytes

async def rpc_call(sock, payload, want_bytes=False):
    reader, writer = await asyncio.open_unix_connection(sock)
    payload["id"] = payload.get("id", uuid.uuid4().hex)

    await send_json(writer, payload)
    resp = await recv_json(reader)

    data = b""
    if want_bytes and resp.get("ok") and resp.get("data_len", 0) > 0:
        data = await recv_bytes(reader, resp["data_len"])

    writer.close()
    await writer.wait_closed()
    return resp, data
