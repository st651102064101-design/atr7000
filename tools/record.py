"""Record the tag reads the bridge broadcasts (ws://localhost:8765) to a JSONL file.
Usage: python3 tools/record.py out.jsonl 180   (seconds)"""
import asyncio, base64, os, json, struct, time, sys
OUT = sys.argv[1]; SECS = float(sys.argv[2])

async def main():
    r, w = await asyncio.open_connection("127.0.0.1", 8765)
    key = base64.b64encode(os.urandom(16)).decode()
    w.write(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
    await r.readuntil(b"\r\n\r\n")
    t0 = time.time(); n = 0
    with open(OUT, "w") as f:
        while time.time() - t0 < SECS:
            try:
                b = await asyncio.wait_for(r.readexactly(2), 1.0)
            except asyncio.TimeoutError:
                continue
            ln = b[1] & 0x7F
            if ln == 126: ln = struct.unpack("!H", await r.readexactly(2))[0]
            elif ln == 127: ln = struct.unpack("!Q", await r.readexactly(8))[0]
            p = await r.readexactly(ln)
            now = time.time() - t0
            try:
                o = json.loads(p)
                for x in (o if isinstance(o, list) else [o]):
                    if x.get("type") == "tag_read":
                        f.write(json.dumps({"t": round(now, 4), "epc": x["epc"], "beam": x["beam_id"], "rssi": x["rssi"], **({"phase": x["phase"]} if "phase" in x else {})}) + "\n"); n += 1
            except Exception:
                pass
    w.close()
    print("recorded", n, "reads in", SECS, "s ->", OUT)
asyncio.run(main())
