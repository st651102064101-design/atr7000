"""
Minimal LLRP (EPCglobal Low Level Reader Protocol) client, standard library only.

Connects to the ATR7000 LLRP server (default TLS port 5085, or plain 5084 when
"Enable Secure mode" is off), installs one inventory ROSpec over the chosen
antenna (= beam) ids, and emits one event per tag report with the antenna id,
peak RSSI and timestamp. Tag reports are pushed by the reader per read
(ROReportSpec trigger "upon N tags", N=1), so there is no batching timer.

Message / parameter type numbers were checked against the sllurp source
(llrp_proto.py); TV parameter numbers/lengths are from the LLRP standard and
are verified at runtime (unknown TV parameters are logged, never guessed).
"""
import asyncio
import ssl
import struct
import time
from datetime import datetime, timezone

# message types
GET_READER_CAPABILITIES, GET_READER_CAPABILITIES_RESPONSE = 1, 11
SET_READER_CONFIG, SET_READER_CONFIG_RESPONSE = 3, 13
ADD_ROSPEC, ADD_ROSPEC_RESPONSE = 20, 30
DELETE_ROSPEC, DELETE_ROSPEC_RESPONSE = 21, 31
START_ROSPEC, START_ROSPEC_RESPONSE = 22, 32
STOP_ROSPEC = 23
ENABLE_ROSPEC, ENABLE_ROSPEC_RESPONSE = 24, 34
RO_ACCESS_REPORT = 61
KEEPALIVE, KEEPALIVE_ACK = 62, 72
READER_EVENT_NOTIFICATION = 63
ENABLE_EVENTS_AND_REPORTS = 64
CLOSE_CONNECTION = 14
ERROR_MESSAGE = 100

# TLV parameter types
P_ROSPEC, P_ROBOUNDARY, P_START_TRIG, P_STOP_TRIG = 177, 178, 179, 182
P_AISPEC, P_AISTOP, P_INVENTORY = 183, 184, 186
P_ROREPORT, P_TAGSELECTOR, P_TAGREPORT, P_EPCDATA = 237, 238, 240, 241
P_LLRPSTATUS = 287
P_ANT_CONFIG, P_C1G2_INV_CMD, P_CUSTOM = 222, 330, 1023
# Zebra (vendor 161) custom parameters, ATR/FX LLRP Custom Extensions guide:
# MotoAntennaConfig subtype 703 contains MotoAntennaStopCondition subtype 704
# (AntennaStopTrigger u8: 0 = Dwell_Time ms, 1 = number of inventory cycles; value u16)
VENDOR_ZEBRA, SUB_ANT_CONFIG, SUB_ANT_STOP = 161, 703, 704

# TV parameter types -> value length (bytes)
TV_LEN = {1: 2, 2: 8, 3: 8, 4: 8, 5: 8, 6: 1, 7: 2, 8: 2, 9: 4, 10: 2,
          11: 2, 12: 2, 13: 12, 14: 2, 15: 2, 16: 4, 17: 2, 18: 4}
TV_ANTENNA, TV_FIRST_UTC, TV_PEAK_RSSI, TV_EPC96, TV_CHANNEL = 1, 2, 6, 13, 7

ROSPEC_ID = 1


def tlv(ptype, body=b""):
    return struct.pack("!HH", ptype, 4 + len(body)) + body


def message(mtype, mid, body=b""):
    return struct.pack("!HII", (1 << 10) | mtype, 10 + len(body), mid) + body


def custom(vendor, subtype, body=b""):
    return tlv(P_CUSTOM, struct.pack("!II", vendor, subtype) + body)


def antenna_config(ant, dwell_ms):
    stop = custom(VENDOR_ZEBRA, SUB_ANT_STOP, struct.pack("!BH", 0, dwell_ms))
    cfg = custom(VENDOR_ZEBRA, SUB_ANT_CONFIG, stop)
    inv_cmd = tlv(P_C1G2_INV_CMD, struct.pack("!B", 0) + cfg)
    return tlv(P_ANT_CONFIG, struct.pack("!H", ant) + inv_cmd)


def build_rospec(antennas, rospec_id=ROSPEC_ID, dwell_ms=None):
    start = tlv(P_START_TRIG, struct.pack("!B", 1))               # immediate
    stop = tlv(P_STOP_TRIG, struct.pack("!BI", 0, 0))             # null
    boundary = tlv(P_ROBOUNDARY, start + stop)
    ai_stop = tlv(P_AISTOP, struct.pack("!BI", 0, 0))             # null: run until ROSpec stops
    per_ant = b"".join(antenna_config(a, dwell_ms) for a in dict.fromkeys(antennas)) if dwell_ms else b""
    inv = tlv(P_INVENTORY, struct.pack("!HB", 1, 1) + per_ant)    # id 1, EPC Gen2 (+ per-antenna dwell)
    ai = tlv(P_AISPEC, struct.pack("!H", len(antennas))
             + b"".join(struct.pack("!H", a) for a in antennas) + ai_stop + inv)
    flags = (1 << 12) | (1 << 10) | (1 << 9)                      # AntennaID, PeakRSSI, FirstSeenUTC
    sel = tlv(P_TAGSELECTOR, struct.pack("!H", flags))
    rep = tlv(P_ROREPORT, struct.pack("!BH", 1, 1) + sel)         # upon N=1 tags
    return tlv(P_ROSPEC, struct.pack("!IBB", rospec_id, 0, 0) + boundary + ai + rep)


SWAP_MIN_S = 6.0   # a reinstall restarts the sweep; swapping faster than ~2 sweeps starves the late beams


def build_plan(base, focus, ring=(), extra_cap=48, ring_rep=2):
    """Antenna order for one sweep. `focus` beams (nearest to the tracked tag) are repeated
    several times through the sweep and `ring` beams (the wider neighbourhood, where the tag
    goes next) a couple of times. The reader visits antennas in list order and accepts repeats,
    so the tag is revisited every ~0.3 s and its neighbourhood every ~1 s, while every other
    beam is still visited once per sweep."""
    bs = set(base)
    focus = [a for a in dict.fromkeys(focus) if a in bs]
    ring = [a for a in dict.fromkeys(ring) if a in bs and a not in focus]
    if not focus and not ring:
        return list(base)
    m = max(2, min(10, extra_cap // max(1, len(focus)))) if focus else ring_rep
    chunks = [base[i * len(base) // m:(i + 1) * len(base) // m] for i in range(m)]
    plan = []
    for i, ch in enumerate(chunks):
        plan += ch + focus
        if ring and i % max(1, m // ring_rep) == 0 and sum(1 for j in range(i + 1) if j % max(1, m // ring_rep) == 0) <= ring_rep:
            plan += ring
    return plan


def parse_params(buf):
    """Yield (ptype, is_tv, value_bytes) for a parameter sequence; stops on unknown TV."""
    i = 0
    n = len(buf)
    while i < n:
        b0 = buf[i]
        if b0 & 0x80:
            t = b0 & 0x7F
            ln = TV_LEN.get(t)
            if ln is None:
                yield (t, True, None)
                return
            yield (t, True, buf[i + 1:i + 1 + ln])
            i += 1 + ln
        else:
            if i + 4 > n:
                return
            t = ((b0 & 0x03) << 8) | buf[i + 1]
            ln = struct.unpack_from("!H", buf, i + 2)[0]
            if ln < 4:
                return
            yield (t, False, buf[i + 4:i + ln])
            i += ln


def parse_report(body, on_unknown=None):
    """RO_ACCESS_REPORT body -> list of dicts (epc, antenna, rssi, ts_us)."""
    out = []
    for t, is_tv, val in parse_params(body):
        if is_tv or t != P_TAGREPORT:
            continue
        rec = {"epc": None, "antenna": None, "rssi": None, "ts": None}
        for st, stv, sv in parse_params(val):
            if sv is None:
                if on_unknown:
                    on_unknown(st)
                break
            if stv:
                if st == TV_EPC96:
                    rec["epc"] = sv.hex()
                elif st == TV_ANTENNA:
                    rec["antenna"] = struct.unpack("!H", sv)[0]
                elif st == TV_PEAK_RSSI:
                    rec["rssi"] = struct.unpack("!b", sv)[0]
                elif st == TV_FIRST_UTC:
                    rec["ts"] = struct.unpack("!Q", sv)[0]
            elif st == P_EPCDATA and len(sv) >= 2:
                bits = struct.unpack_from("!H", sv)[0]
                rec["epc"] = sv[2:2 + (bits + 7) // 8].hex()
        if rec["epc"] is not None:
            out.append(rec)
    return out


def _clean(txt):
    return txt.rstrip(chr(0))


def status_of(body):
    for t, is_tv, val in parse_params(body):
        if not is_tv and t == P_LLRPSTATUS and len(val) >= 4:
            code = struct.unpack_from("!H", val)[0]
            dl = struct.unpack_from("!H", val, 2)[0]
            return code, _clean(val[4:4 + dl].decode("utf-8", "replace"))
    return None, ""


class LlrpClient:
    def __init__(self, host, port, tls, antennas, emit, log=print, dwell_ms=None):
        self.dwell_ms = dwell_ms
        self.host, self.port, self.tls = host, port, tls
        self.antennas, self.emit, self.log = antennas, emit, log
        self.mid = 0
        self.reader = self.writer = None
        self.reports = 0
        self.last_report = 0.0
        self.base = list(antennas)
        self.focus = ((), ())     # (focus beams, ring beams) currently emphasised in the sweep
        self.want = ((), ())      # requested by the page
        self.last_swap = 0.0
        self.swaps = 0

    def set_focus(self, beams, ring=()):
        """Ask for these beams (and a wider ring) to be scanned more often. Applied by the read loop."""
        ok = set(self.base)
        b = tuple(sorted(a for a in set(beams) if a in ok))[:12]
        r = tuple(sorted(a for a in set(ring) if a in ok and a not in b))[:24]
        self.want = (b, r)

    async def _install(self, antennas):
        b = await self._rpc(DELETE_ROSPEC, struct.pack("!I", 0), DELETE_ROSPEC_RESPONSE)
        b = await self._rpc(ADD_ROSPEC, build_rospec(antennas, dwell_ms=self.dwell_ms), ADD_ROSPEC_RESPONSE)
        code, desc = status_of(b)
        if code != 0:
            return False, "%r %s" % (code, desc)
        await self._rpc(ENABLE_ROSPEC, struct.pack("!I", ROSPEC_ID), ENABLE_ROSPEC_RESPONSE)
        await self._rpc(START_ROSPEC, struct.pack("!I", ROSPEC_ID), START_ROSPEC_RESPONSE)
        return True, ""

    async def _maybe_swap(self):
        if self.want == self.focus or time.time() - self.last_swap < SWAP_MIN_S:
            return
        plan = build_plan(self.base, list(self.want[0]), list(self.want[1]))
        ok, why = await self._install(plan)
        self.last_swap = time.time()
        if ok:
            self.focus = self.want
            self.swaps += 1
            self.log("[llrp] focus scan on %s ring %d (sweep %d entries)" % (list(self.focus[0]) or "none", len(self.focus[1]), len(plan)))
        else:
            self.log("[llrp] focus plan rejected (%s); back to the full sweep" % why)
            self.want = ((), ())
            ok2, why2 = await self._install(list(self.base))
            self.focus = ((), ())
            if not ok2:
                raise RuntimeError("could not restore the full sweep: %s" % why2)

    def _next(self):
        self.mid += 1
        return self.mid

    async def _recv(self):
        h = await self.reader.readexactly(10)
        w0, ln, mid = struct.unpack("!HII", h)
        body = await self.reader.readexactly(ln - 10) if ln > 10 else b""
        return w0 & 0x3FF, mid, body

    def _handle(self, mtype, mid, body):
        if mtype == RO_ACCESS_REPORT:
            now = time.time()
            for r in parse_report(body, on_unknown=lambda t: self.log("[llrp] unknown TV parameter %d in tag report" % t)):
                if r["antenna"] is None or r["rssi"] is None:
                    continue
                ts = r["ts"]
                iso = (datetime.fromtimestamp(ts / 1e6, timezone.utc).astimezone().isoformat()
                       if ts else datetime.now().astimezone().isoformat())
                self.reports += 1
                self.last_report = now
                self.emit({"type": "tag_read", "epc": r["epc"], "beam_id": r["antenna"],
                           "rssi": float(r["rssi"]), "timestamp": iso,
                           "bridge_ts": round(now * 1000, 3), "via": "llrp"})
        elif mtype == KEEPALIVE:
            self.writer.write(message(KEEPALIVE_ACK, mid))
        elif mtype == ERROR_MESSAGE:
            self.log("[llrp] ERROR_MESSAGE: %r" % (status_of(body),))

    async def _rpc(self, mtype, body, expect):
        mid = self._next()
        self.writer.write(message(mtype, mid, body))
        await self.writer.drain()
        while True:
            t, rid, b = await asyncio.wait_for(self._recv(), 15)
            if t == expect:
                return b
            self._handle(t, rid, b)

    async def session(self):
        ctx = None
        if self.tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
            except ssl.SSLError:
                pass
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ctx), 5)
        self.log("[llrp] connected to %s:%d (%s)" % (self.host, self.port, "TLS" if self.tls else "plain"))
        try:
            # the reader announces itself with READER_EVENT_NOTIFICATION
            t, mid, b = await asyncio.wait_for(self._recv(), 10)
            self.log("[llrp] first message type %d" % t)
            b = await self._rpc(DELETE_ROSPEC, struct.pack("!I", 0), DELETE_ROSPEC_RESPONSE)
            self.log("[llrp] DELETE_ROSPEC status %r" % (status_of(b),))
            b = await self._rpc(ADD_ROSPEC, build_rospec(self.antennas, dwell_ms=self.dwell_ms), ADD_ROSPEC_RESPONSE)
            code, desc = status_of(b)
            self.log("[llrp] ADD_ROSPEC status %r %s" % (code, desc))
            if code != 0:
                raise RuntimeError("ADD_ROSPEC rejected: %r %s" % (code, desc))
            b = await self._rpc(ENABLE_ROSPEC, struct.pack("!I", ROSPEC_ID), ENABLE_ROSPEC_RESPONSE)
            self.log("[llrp] ENABLE_ROSPEC status %r" % (status_of(b),))
            b = await self._rpc(START_ROSPEC, struct.pack("!I", ROSPEC_ID), START_ROSPEC_RESPONSE)
            self.log("[llrp] START_ROSPEC status %r" % (status_of(b),))
            self.writer.write(message(ENABLE_EVENTS_AND_REPORTS, self._next()))
            await self.writer.drain()
            self.focus = ((), ())
            self.last_swap = time.time()
            self.log("[llrp] streaming tag reports")
            while True:
                t, mid, b = await self._recv()
                self._handle(t, mid, b)
                await self.writer.drain()
                if self.want != self.focus:
                    await self._maybe_swap()
        finally:
            try:
                self.writer.close()
            except Exception:
                pass

    async def run_forever(self):
        quiet = False
        while True:
            try:
                await self.session()
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ssl.SSLError, RuntimeError) as e:
                if not quiet:
                    self.log("[llrp] not connected (%s: %s); retrying every 3 s" % (type(e).__name__, e))
                    quiet = True
            else:
                quiet = False
            await asyncio.sleep(3)
