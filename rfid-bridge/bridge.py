"""
ATR7000 RFID Bridge (zero dependencies, Python 3.8+)

ATR7000 --HTTP POST--> :8080/rfid  --WebSocket--> :8765 --> Floor 360 Test Lab HTML

Endpoints (HTTP, port 8080):
  POST /rfid       receive Zebra IoT Connector payload (any JSON)
  POST /simulate   inject a fake tag read: {"epc":"...","beam_id":10,"rssi":-50}
  GET  /health     status + counters
  GET  /last       last 20 raw payloads (to inspect the real format)

Every raw payload is appended to raw_payloads.jsonl so the parser can be fixed
against the real format the reader sends.
"""
import asyncio
import base64
import hashlib
import json
import socket
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import llrp

HTTP_PORT = 8080
WS_PORT = 8765
HOST = "0.0.0.0"
RAW_LOG = Path(__file__).with_name("raw_payloads.jsonl")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

clients = set()
llrp_client = None   # set in main(); the page can ask it for a focus scan
recent = []
stats = {"posts": 0, "tags": 0, "dropped_no_beam": 0, "started": time.time()}

EPC_KEYS = ("idHex", "epc", "EPC", "tagId", "tagID", "tag_id", "tagIdHex", "id")
BEAM_KEYS = ("beam_id", "beamId", "antenna", "antennaID", "antennaId", "antenna_id", "antennaPort", "beam")
RSSI_KEYS = ("peakRssi", "peak_rssi", "rssi", "RSSI", "PeakRSSI")
TIME_KEYS = ("timestamp", "firstSeenTimestamp", "lastSeenTimestamp", "ts", "time", "eventTime")


def first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def extract(node, out, ctx=None):
    """Walk any JSON and collect dicts that look like a tag read."""
    ctx = ctx or {}
    if isinstance(node, list):
        for x in node:
            extract(x, out, ctx)
    elif isinstance(node, dict):
        merged = dict(ctx)
        for k in BEAM_KEYS + RSSI_KEYS + TIME_KEYS:
            if k in node and not isinstance(node[k], (dict, list)):
                merged[k] = node[k]
        epc = first(node, EPC_KEYS)
        if epc is not None and not isinstance(epc, (dict, list)):
            beam = first(merged, BEAM_KEYS)
            rssi = first(merged, RSSI_KEYS)
            ts = first(merged, TIME_KEYS)
            out.append({"epc": str(epc).strip(), "beam": beam, "rssi": rssi, "ts": ts})
        for v in node.values():
            if isinstance(v, (dict, list)):
                extract(v, out, merged)


def iso(ts):
    if isinstance(ts, (int, float)):
        if ts > 1e14:
            ts /= 1e6   # microseconds
        elif ts > 1e11:
            ts /= 1e3   # milliseconds
        return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat()
    if isinstance(ts, str) and ts:
        try:  # reader sends e.g. 2026-11-19T07:09:29.034+0000
            return datetime.fromisoformat(ts).astimezone().isoformat()
        except ValueError:
            return ts
    return datetime.now().astimezone().isoformat()


def to_events(payload):
    found = []
    extract(payload, found)
    events = []
    for f in found:
        try:
            beam = int(float(f["beam"]))
            rssi = float(f["rssi"])
        except (TypeError, ValueError):
            stats["dropped_no_beam"] += 1
            print("  ! tag %s skipped: beam/rssi missing (beam=%r rssi=%r)" % (f["epc"], f["beam"], f["rssi"]))
            continue
        events.append({"type": "tag_read", "epc": f["epc"], "beam_id": beam,
                       "rssi": rssi, "timestamp": iso(f["ts"]), "bridge_ts": round(time.time() * 1000, 3)})
    return events


# ---------------- WebSocket (minimal server, text frames out) ----------------
def ws_frame(text):
    data = text.encode("utf-8")
    n = len(data)
    if n < 126:
        head = struct.pack("!BB", 0x81, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x81, 126, n)
    else:
        head = struct.pack("!BBQ", 0x81, 127, n)
    return head + data


async def broadcast(obj):
    frame = ws_frame(json.dumps(obj))
    for w in list(clients):
        try:
            w.write(frame)
            await w.drain()
        except Exception:
            clients.discard(w)


async def ws_handler(reader, writer):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin1").split("\r\n")
        hdr = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                hdr[k.strip().lower()] = v.strip()
        key = hdr.get("sec-websocket-key")
        if not key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return
        acc = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
        await writer.drain()
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        clients.add(writer)
        print("[ws] client connected (%d total)" % len(clients))
        while True:
            b = await reader.readexactly(2)
            op = b[0] & 0x0F
            ln = b[1] & 0x7F
            if ln == 126:
                ln = struct.unpack("!H", await reader.readexactly(2))[0]
            elif ln == 127:
                ln = struct.unpack("!Q", await reader.readexactly(8))[0]
            mask = await reader.readexactly(4) if b[1] & 0x80 else b""
            payload = await reader.readexactly(ln) if ln else b""
            if op == 0x1:  # text frame from the page: {"cmd":"focus","beams":[...]}
                if mask:
                    payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
                handle_page_command(payload)
            if op == 0x8:
                break
            if op == 0x9:  # ping -> pong
                writer.write(bytes([0x8A, len(payload)]) + payload)
                await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
        pass
    finally:
        clients.discard(writer)
        print("[ws] client disconnected (%d left)" % len(clients))
        if not clients and llrp_client is not None:
            llrp_client.set_focus([], [])   # nobody is watching: back to the plain full sweep
        writer.close()


def handle_page_command(payload):
    """Only one command is accepted: focus a few beams. Input is validated strictly."""
    try:
        msg = json.loads(payload.decode("utf-8"))
        if isinstance(msg, dict) and msg.get("cmd") == "scan":
            # run-time scan lab: {"cmd":"scan","ants":[301..397],"session":null|0..3,"flip":bool,"dwell":10..50}
            ants = msg.get("ants")
            if ants is not None:
                if not isinstance(ants, list) or not (1 <= len(ants) <= 97) or not all(isinstance(b, int) and not isinstance(b, bool) and 301 <= b <= 397 for b in ants):
                    return
            sess = msg.get("session", "keep")
            if sess != "keep" and sess is not None and (not isinstance(sess, int) or isinstance(sess, bool) or not 0 <= sess <= 3):
                return
            flip = msg.get("flip")
            if flip is not None and not isinstance(flip, bool):
                return
            dwell = msg.get("dwell")
            if dwell is not None and (not isinstance(dwell, int) or isinstance(dwell, bool) or not 10 <= dwell <= 50):
                return
            if llrp_client is not None:
                llrp_client.set_scan(ants, sess, flip, dwell)
            return
        if not isinstance(msg, dict) or msg.get("cmd") != "focus":
            return
        beams = msg.get("beams", [])
        if not isinstance(beams, list) or len(beams) > 24:
            return
        clean = lambda xs: [b for b in xs if isinstance(b, int) and not isinstance(b, bool) and 1 <= b <= 9999]
        ring = msg.get("ring", [])
        if not isinstance(ring, list) or len(ring) > 48:
            ring = []
        if llrp_client is not None:
            llrp_client.set_focus(clean(beams), clean(ring))
    except (ValueError, UnicodeDecodeError):
        pass


# ---------------- HTTP receiver ----------------
async def read_body(reader, hdr):
    if hdr.get("transfer-encoding", "").lower() == "chunked":
        body = b""
        while True:
            size = int((await reader.readline()).strip().split(b";")[0] or b"0", 16)
            if size == 0:
                await reader.readline()
                break
            body += await reader.readexactly(size)
            await reader.readline()
        return body
    n = int(hdr.get("content-length", "0") or 0)
    return await reader.readexactly(n) if n else b""


def respond(writer, code, obj):
    body = json.dumps(obj).encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(code, "OK")
    writer.write(("HTTP/1.1 %d %s\r\nContent-Type: application/json\r\n"
                  "Access-Control-Allow-Origin: *\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                  % (code, reason, len(body))).encode() + body)


async def http_handler(reader, writer):
    peer = writer.get_extra_info("peername")
    _s = writer.get_extra_info("socket")
    if _s is not None:
        _s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        hdr = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                hdr[k.strip().lower()] = v.strip()
        body = await read_body(reader, hdr)
        path = path.split("?")[0]

        if method == "OPTIONS":
            writer.write(b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                         b"Access-Control-Allow-Headers: *\r\nAccess-Control-Allow-Methods: *\r\nContent-Length: 0\r\n\r\n")
        elif method == "GET" and path == "/health":
            respond(writer, 200, {"ok": True, "ws_clients": len(clients), "uptime_s": int(time.time() - stats["started"]), **stats})
        elif method == "GET" and path == "/last":
            respond(writer, 200, recent[-20:])
        elif method == "POST" and path in ("/rfid", "/simulate", "/"):
            try:
                payload = json.loads(body.decode("utf-8-sig") or "null")
            except ValueError:
                # keep non-JSON payloads too, so the real format can be inspected
                payload = {"_non_json": body.decode("latin1")[:4000]}
            stats["posts"] += 1
            rec = {"t": datetime.now().astimezone().isoformat(), "from": peer[0] if peer else None,
                   "path": path, "payload": payload}
            recent.append(rec)
            del recent[:-200]
            with RAW_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            events = to_events(payload)
            stats["tags"] += len(events)
            print("[http] POST %s from %s: %d bytes -> %d tag(s) -> %d ws client(s)"
                  % (path, rec["from"], len(body), len(events), len(clients)))
            for e in events:
                print("   EPC %s beam %s rssi %s" % (e["epc"], e["beam_id"], e["rssi"]))
                await broadcast(e)
            respond(writer, 200, {"ok": True, "tags": len(events)})
        else:
            respond(writer, 404, {"ok": False, "error": "not found"})
        await writer.drain()
    except Exception as ex:
        print("[http] error:", ex)
    finally:
        writer.close()


def emit_sync(obj):
    """Push one event to every WebSocket client right now (used by the LLRP stream)."""
    stats["tags"] += 1
    frame = ws_frame(json.dumps(obj))
    for w in list(clients):
        try:
            w.write(frame)
        except Exception:
            clients.discard(w)


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


async def main():
    http = await asyncio.start_server(http_handler, HOST, HTTP_PORT)
    ws = await asyncio.start_server(ws_handler, HOST, WS_PORT)
    print("ATR7000 RFID Bridge")
    print("  HTTP receiver : http://<this-pc-ip>:%d/rfid   (set this in ATR7000 IoT Connector)" % HTTP_PORT)
    print("  WebSocket     : ws://localhost:%d              (set this in the HTML page)" % WS_PORT)
    print("  Raw log       : %s" % RAW_LOG)
    print("  Ctrl+C to stop\n")
    tasks = [http.serve_forever(), ws.serve_forever()]
    if "--no-llrp" not in sys.argv:
        a, b = arg("--ants", "301-397").split("-")
        ants = list(range(int(a), int(b) + 1))
        host = arg("--llrp-host", "192.168.1.10")
        port = int(arg("--llrp-port", "5085"))
        tls = "--llrp-plain" not in sys.argv
        print("  LLRP          : %s:%d (%s) antennas %s-%s  [--no-llrp to disable]" % (host, port, "TLS" if tls else "plain", a, b))
        dwell = int(arg("--dwell", "20"))
        print("  LLRP dwell    : %d ms per beam (--dwell N, 0 = reader default)" % dwell)
        sess = arg("--session")
        if sess is not None:
            llrp.SESSION = int(sess)
            print("  LLRP session  : S%d (--session N, default = the reader's own setting)" % llrp.SESSION)
        if "--flip" in sys.argv:
            llrp.QUERY_FLIP = bytes([0, 1])
            print("  LLRP A/B flip : on (--flip)")
        client = llrp.LlrpClient(host, port, tls, ants, emit_sync, dwell_ms=dwell or None)
        global llrp_client
        llrp_client = client
        tasks.append(client.run_forever())
    async with http, ws:
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
