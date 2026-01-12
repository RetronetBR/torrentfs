import asyncio, struct, json

async def recv_frame(reader):
    hdr = await reader.readexactly(4)
    (n,) = struct.unpack(">I", hdr)
    return await reader.readexactly(n)

async def send_frame(writer, payload):
    writer.write(struct.pack(">I", len(payload)))
    writer.write(payload)
    await writer.drain()

async def recv_json(reader):
    return json.loads((await recv_frame(reader)).decode())

async def send_json(writer, obj):
    await send_frame(writer, json.dumps(obj).encode())

async def send_bytes(writer, data):
    writer.write(data)
    await writer.drain()
    
async def recv_bytes(reader, size: int) -> bytes:
    """
    Recebe exatamente `size` bytes do stream.
    Usado após um read() RPC que retorna data_len.
    """
    return await reader.readexactly(size)
