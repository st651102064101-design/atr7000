#!/usr/bin/env python3
"""Scan lab: change what the reader scans WHILE the bridge keeps running (the page stays live),
and measure steady-state reads per tag for each configuration.

  python3 tools/scan_lab.py full ring30 ring30:s1 ring30:s0+flip full
config = <beams>[:<session>[+flip]]   beams: full | ring15 | ring30 | ring45 | ring60 | rings30-45
         session: s0 s1 s2 s3 (omit = the reader's default)
"""
import asyncio, base64, collections, json, os, struct, sys, time

RINGS = {"ring60": range(301, 325), "ring45": range(325, 349), "ring30": range(349, 373), "ring15": range(373, 397)}

def beams(name):
    if name == "full": return list(range(301, 398))
    if name == "rings30-45": return list(range(325, 373))
    return list(RINGS[name])

async def main(configs, warm=8, secs=15, dwell=None):
    r, w = await asyncio.open_connection("127.0.0.1", 8765)
    key = base64.b64encode(os.urandom(16)).decode()
    w.write(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
    await r.readuntil(b"\r\n\r\n")
    reads = []
    async def rd():
        while True:
            b = await r.readexactly(2); ln = b[1] & 0x7F
            if ln == 126: ln = struct.unpack("!H", await r.readexactly(2))[0]
            elif ln == 127: ln = struct.unpack("!Q", await r.readexactly(8))[0]
            p = await r.readexactly(ln)
            try:
                o = json.loads(p)
                for x in (o if isinstance(o, list) else [o]):
                    if x.get("type") == "tag_read": reads.append((time.time(), x["beam_id"], x["epc"], x["rssi"]))
            except Exception: pass
    asyncio.create_task(rd())
    def send(obj):
        data = json.dumps(obj).encode(); mask = os.urandom(4); n = len(data)
        head = bytes([0x81, 0x80 | n]) if n < 126 else bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
        w.write(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))
    for cfg in configs:
        b, _, sess = cfg.partition(":")
        msg = {"cmd": "scan", "ants": beams(b), "flip": "flip" in sess, "session": None}
        if sess[:2] in ("s0", "s1", "s2", "s3"): msg["session"] = int(sess[1])
        if dwell: msg["dwell"] = dwell
        send(msg); await w.drain()
        await asyncio.sleep(warm)
        t0 = time.time(); n0 = len(reads)
        await asyncio.sleep(secs)
        rs = [x for x in reads[n0:] if x[0] >= t0]
        by = collections.defaultdict(list)
        for t, bm, e, rssi in rs: by[e].append(t)
        top = sorted(by.items(), key=lambda kv: -len(kv[1]))[:4]
        out = []
        for e, ts in top:
            ts.sort(); g = sorted(ts[i] - ts[i-1] for i in range(1, len(ts)))
            out.append("%.1f/s gap~%.2fs" % (len(ts) / secs, g[len(g)//2] if g else -1))
        print("%-18s %5.1f reads/s  tags %2d | beams seen %2d | top tags: %s" % (cfg, len(rs) / secs, len(by), len(set(x[1] for x in rs)), " ; ".join(out)), flush=True)
    send({"cmd": "scan", "ants": beams("full"), "flip": False, "session": None}); await w.drain()
    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["full", "ring30", "full"]))
