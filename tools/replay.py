#!/usr/bin/env python3
"""Replay recorded tag reads through the direction estimators and score them.

Record reads (the bridge must be running):   python3 tools/record.py out.jsonl 180
Score a stationary set:                       python3 tools/replay.py out.jsonl --algo current --algo lock
Score how fast a move is followed:            python3 tools/replay.py out.jsonl --teleport

Beam geometry (ATR7000 integration guide, table 4): LHCP beams 301-397,
base = id-300; rings of 24 beams at tilt 60/45/30/15 deg, azimuth every 15 deg,
beam 397 is the centre (tilt 0).
"""
import argparse, collections, json, math, sys

TILT = [60, 45, 30, 15]


def beam_info(b):
    base = b - 300
    if not 1 <= base <= 97:
        return None
    if base == 97:
        return (None, 0.0)
    return (((base - 1) % 24) * 15.0, float(TILT[(base - 1) // 24]))


def vec(bi):
    az, el = bi
    if az is None:
        return (0.0, 0.0, 1.0)
    e, a = math.radians(el), math.radians(az)
    return (math.sin(e) * math.sin(a), math.sin(e) * math.cos(a), math.cos(e))


def ang(u, v):
    d = max(-1.0, min(1.0, sum(p * q for p, q in zip(u, v))))
    return math.degrees(math.acos(d))


def angdiff(a, b):
    return ((a - b + 540) % 360) - 180


def lin(rssi):
    return 10 ** (rssi / 10.0)


def estimate(win, ref_t, tau=0.0, anchor=None, gate=None, thr=0.251, flat=False):
    """Same maths as estimate() in the page. win = [(t, beam, rssi)]."""
    pw, wt = {}, {}
    for t, b, r in win:
        k = math.exp(-(ref_t - t) / tau) if tau else 1.0
        pw[b] = pw.get(b, 0.0) + k * lin(r)
        wt[b] = wt.get(b, 0.0) + k
    if not pw:
        return None
    pm = {b: pw[b] / wt[b] for b in pw}
    bb = max(pm, key=pm.get)
    mx = pm[bb]
    ab = anchor if (anchor is not None and anchor in pm) else bb
    ref0 = pm[ab] if (anchor is not None and anchor in pm) else mx
    abi = beam_info(ab)
    av = vec(abi) if abi else None
    x = y = z = 0.0
    amb = 0
    for b, p in pm.items():
        if p < ref0 * min(thr, 0.4):
            continue
        bi = beam_info(b)
        if not bi:
            continue
        if av:
            a_ = ang(av, vec(bi))
            if p >= ref0 * 0.4 and a_ > 60:
                amb += 1
            if gate and a_ > gate:
                continue
        if p < ref0 * thr:
            continue
        w = p if flat else p * wt[b]
        if bi[0] is None:
            z += w
            continue
        e = math.radians(bi[1])
        x += w * math.sin(e) * math.sin(math.radians(bi[0]))
        y += w * math.cos(math.radians(bi[0])) * math.sin(e)
        z += w * math.cos(e)
    L = math.sqrt(x * x + y * y + z * z)
    az = math.degrees(math.atan2(x, y)) % 360 if (x or y) else None
    tilt = math.degrees(math.acos(max(-1, min(1, z / L)))) if L > 0 else None
    return {"az": az, "tilt": tilt, "beam": ab if (anchor is not None and anchor in pm) else bb, "pm": pm, "amb": amb}


# ---------------------------------------------------------------- algorithms
class Current:
    """The live-mode tracker as shipped (window >= one sweep, anchor hysteresis, dead band)."""
    SNAP, TAU, HYS, HOLD, GATE, THR, DEAD, MINN, MAXSPAN = 2.6, 0.8, 2.5, 0.15, 35, 0.4, 9.0, 14, 3.2

    def __init__(self):
        self.az = self.el = self.anchor = None
        self.ch = None

    def update(self, reads, now):
        recent = [r for r in reads if now - r[0] < 60]
        if not recent:
            return None
        span = min(self.MAXSPAN, max(self.SNAP, (now - recent[-self.MINN][0]) if len(recent) >= self.MINN else self.MAXSPAN))
        win = [r for r in recent if now - r[0] < span]
        if len(win) < 3:
            win = recent[-10:]
        s0 = estimate(win, now, self.TAU)
        anc = self.anchor
        if anc is None or anc not in s0["pm"]:
            anc = s0["beam"]
        elif s0["beam"] != anc and s0["pm"][s0["beam"]] / s0["pm"][anc] >= self.HYS:
            if not self.ch or self.ch[0] != s0["beam"]:
                self.ch = (s0["beam"], now)
            elif now - self.ch[1] >= self.HOLD:
                anc, self.ch = s0["beam"], None
        else:
            self.ch = None
        self.anchor = anc
        s = estimate(win, now, self.TAU, anchor=anc, gate=self.GATE, thr=self.THR, flat=True)
        if s["az"] is None:
            return (self.az, self.el)
        if self.az is None:
            self.az, self.el = s["az"], s["tilt"]
        elif math.hypot(angdiff(s["az"], self.az), s["tilt"] - self.el) > self.DEAD:
            self.az, self.el = s["az"], s["tilt"]
        return (self.az, self.el)


class Lock:
    """Candidate: a slow reference built from a long memory of every beam, held while the
    short-term picture still matches it (the tag is stationary); released on a real change.
    - signature: median |dB| difference between the last 2.6 s and the long memory
    - a move must persist for MOVE_HOLD s before the reference is dropped
    - the reference winner beam changes only with a 3 dB lead held for ANCHOR_HOLD s
    - the dead band grows when the tag is read rarely (sparse evidence)"""
    LONG_TAU, SHORT = 40.0, 2.6
    SIG_DB, SIG_MIN, MOVE_HOLD = 6.0, 6, 1.0
    ANCHOR_RATIO, ANCHOR_HOLD = 2.0, 2.0
    NEW_WITHIN, NEW_LEAD = 4.0, 6.0

    def __init__(self):
        self.az = self.el = None
        self.anchor = None
        self.ch = None
        self.move_since = None
        self.epoch = -1e9      # reads older than this belong to the previous position and are forgotten

    @staticmethod
    def _means(win, now, tau):
        pw, wt = {}, {}
        for t, b, r in win:
            k = math.exp(-(now - t) / tau) if tau else 1.0
            pw[b] = pw.get(b, 0.0) + k * lin(r)
            wt[b] = wt.get(b, 0.0) + k
        return pw, wt

    def update(self, reads, now):
        recent = [r for r in reads if now - r[0] < 120 and r[0] >= self.epoch]
        if len(recent) < 3:
            return (self.az, self.el)
        sp = self.SHORT
        short = [r for r in recent if now - r[0] < sp] or recent[-8:]
        spw, swt = self._means(short, now, 0.0)
        lpw, lwt = self._means(recent, now, self.LONG_TAU)
        s_db = {b: 10 * math.log10(spw[b] / swt[b]) for b in spw}
        # the picture to compare against must NOT contain the window being judged
        older = [r for r in recent if now - r[0] >= sp]
        opw, owt = self._means(older, now, self.LONG_TAU) if older else ({}, {})
        l_db = {b: 10 * math.log10(opw[b] / owt[b]) for b in opw} if older else {b: 10 * math.log10(lpw[b] / lwt[b]) for b in lpw}
        ref_wt = owt if older else lwt
        common = [b for b in s_db if b in l_db and ref_wt[b] > 0.4]
        moved = False
        if len(common) >= self.SIG_MIN:
            diffs = sorted(abs(s_db[b] - l_db[b]) for b in common)
            moved = diffs[len(diffs) // 2] > self.SIG_DB
        # new strong beams: beams as strong as the old winner that the memory had as weak or unknown
        lbest_db = max(l_db.values()) if l_db else -200.0
        cnt = {}
        for t_, b_, r_ in short:
            cnt[b_] = cnt.get(b_, 0) + 1
        fresh = [b for b in s_db if s_db[b] >= lbest_db - self.NEW_WITHIN and l_db.get(b, -200.0) <= s_db[b] - self.NEW_LEAD]
        if len(fresh) >= 2 or any(cnt[b] >= 2 for b in fresh):
            moved = True
        # long-term reference position, winner beam with hysteresis
        lbest = max(lpw, key=lambda b: lpw[b] / lwt[b])
        if self.anchor is None or self.anchor not in lpw:
            self.anchor, self.ch = lbest, None
        elif lbest != self.anchor and (lpw[lbest] / lwt[lbest]) / (lpw[self.anchor] / lwt[self.anchor]) >= self.ANCHOR_RATIO:
            if not self.ch or self.ch[0] != lbest:
                self.ch = (lbest, now)
            elif now - self.ch[1] >= self.ANCHOR_HOLD:
                self.anchor, self.ch = lbest, None
        else:
            self.ch = None
        long_est = estimate(recent, now, self.LONG_TAU, anchor=self.anchor, gate=35, thr=0.4, flat=True)
        short_est = estimate(short, now, 0.0, gate=35, thr=0.4, flat=True)
        if long_est is None or long_est["az"] is None:
            return (self.az, self.el)
        if self.az is None:
            self.az, self.el = long_est["az"], long_est["tilt"]
            return (self.az, self.el)
        rate = len([r for r in recent if now - r[0] < 30]) / 30.0
        dead = 8.0 + max(0.0, 6.0 - rate) * 2.0          # 8 deg for strong tags, up to 20 deg for very sparse ones
        if moved:
            self.move_since = self.move_since or now
            if now - self.move_since >= self.MOVE_HOLD and short_est and short_est["az"] is not None:
                self.az, self.el = short_est["az"], short_est["tilt"]
                self.anchor = short_est["beam"]
                self.epoch = now - sp      # forget the old position's memory
                self.move_since = None
        else:
            self.move_since = None
            if math.hypot(angdiff(long_est["az"], self.az), long_est["tilt"] - self.el) > dead:
                self.az, self.el = long_est["az"], long_est["tilt"]
        return (self.az, self.el)


class LockFast(Lock):
    """Same memory, but decides 'it moved' from a 1 s window with fewer beams and a shorter hold."""
    SHORT, SIG_MIN, SIG_DB, MOVE_HOLD, NEW_WITHIN, NEW_LEAD = 1.0, 4, 6.5, 0.3, 5.0, 8.0


ALGOS = {"current": Current, "lock": Lock, "fast": LockFast}


# ---------------------------------------------------------------- scoring
def load(path):
    by = collections.defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        by[r["epc"]].append((r["t"], r["beam"], r["rssi"]))
    for v in by.values():
        v.sort()
    return by


def run(algo_cls, reads, t0, t1, step=0.3):
    a = algo_cls()
    out = []
    t = t0
    idx = 0
    while t <= t1:
        while idx < len(reads) and reads[idx][0] <= t:
            idx += 1
        o = a.update(reads[:idx], t) if idx else None
        if o and o[0] is not None:
            out.append((t, o[0], o[1]))
        t += step
    return out


def metrics(series):
    if len(series) < 3:
        return None
    jumps = path = 0.0
    for (t0, a0, e0), (t1, a1, e1) in zip(series, series[1:]):
        da, de = abs(angdiff(a1, a0)), abs(e1 - e0)
        if da > 20 or de > 15:
            jumps += 1
        path += math.hypot(da, de)
    az = [s[1] for s in series]
    el = [s[2] for s in series]
    # circular spread of azimuth around its circular mean
    cx = sum(math.sin(math.radians(a)) for a in az); cy = sum(math.cos(math.radians(a)) for a in az)
    m = math.degrees(math.atan2(cx, cy))
    azspan = max(abs(angdiff(a, m)) for a in az) * 2
    return {"jumps": int(jumps), "path": round(path), "azspan": round(azspan), "tiltspan": round(max(el) - min(el)), "n": len(series)}


def teleport(by, algos, a_win=(15, 75), b_win=(15, 75), pairs=6, min_reads=150):
    """Synthetic move with real noise: tag X shows the reads of tag A for 60 s, then the reads of
    tag B. How long until the output reaches B's steady position?"""
    good = [(e, r) for e, r in by.items() if len(r) >= min_reads]
    res = {a: [] for a in algos}
    n = 0
    for i, (ea, ra) in enumerate(good):
        for eb, rb in good[i + 1:]:
            if n >= pairs:
                break
            A = [r for r in ra if a_win[0] <= r[0] < a_win[1]]
            B = [r for r in rb if b_win[0] <= r[0] < b_win[1]]
            if len(A) < 60 or len(B) < 60:
                continue
            shift = (a_win[1] - a_win[0]) - (b_win[0] - a_win[0])
            X = A + [(t + shift, b, r) for t, b, r in B]
            switch = a_win[1]
            line = "%s -> %s:" % (ea[:8], eb[:8])
            ok = True
            for name in algos:
                cls = ALGOS[name]
                steadyB = run(cls, [(t, b, r) for t, b, r in B], b_win[0] + 30, b_win[1])
                steadyA = run(cls, A, a_win[0] + 30, a_win[1])
                if not steadyB or not steadyA:
                    ok = False
                    break
                sb = steadyB[-1]
                sa = steadyA[-1]
                if math.hypot(angdiff(sb[1], sa[1]), sb[2] - sa[2]) < 40:
                    ok = False
                    break
                ser = run(cls, X, a_win[0] + 30, switch + 25)
                t_reach = None
                for k, (t, az, el) in enumerate(ser):
                    if t < switch:
                        continue
                    close = lambda q: math.hypot(angdiff(q[1], sb[1]), q[2] - sb[2]) < 20
                    if close((t, az, el)) and all(close(q) for q in ser[k:k + 7]):
                        t_reach = round(t - switch, 1)
                        break
                line += " %s %s s |" % (name, t_reach)
                res[name].append(t_reach)
            if ok:
                print(line)
                n += 1
    for name in algos:
        v = [x for x in res[name] if x is not None]
        miss = len(res[name]) - len(v)
        print("  %-8s moves %d  followed %d  median %s s  worst %s s" % (name, len(res[name]), len(v), sorted(v)[len(v) // 2] if v else None, max(v) if v else None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teleport", action="store_true", help="score how fast a move is followed (synthetic, from real reads)")
    ap.add_argument("file")
    ap.add_argument("--algo", action="append", choices=list(ALGOS), help="repeatable (default: all)")
    ap.add_argument("--warmup", type=float, default=15.0, help="seconds ignored at the start")
    ap.add_argument("--min-reads", type=int, default=30)
    args = ap.parse_args()
    by = load(args.file)
    span = max(r[-1][0] for r in by.values())
    algos = args.algo or list(ALGOS)
    if args.teleport:
        teleport(by, algos)
        return
    print("%s: %d tags, %.0f s; scoring from t=%.0f s (stationary set: lower is better)" % (args.file, len(by), span, args.warmup))
    tot = {a: collections.Counter() for a in algos}
    rows = []
    for epc, reads in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(reads) < args.min_reads:
            continue
        line = "%-14s reads %5d (%.1f/s) |" % (epc[:12], len(reads), len(reads) / span)
        for a in algos:
            m = metrics(run(ALGOS[a], reads, args.warmup, span))
            if m:
                tot[a].update({"jumps": m["jumps"], "path": m["path"], "tags": 1, "wild": 1 if m["jumps"] > 1 or m["azspan"] > 25 else 0})
                line += " %s jumps %2d path %5d az±%3d tilt %2d |" % (a, m["jumps"], m["path"], m["azspan"] // 2, m["tiltspan"])
        rows.append(line)
    print("\n".join(rows))
    print("TOTAL")
    for a in algos:
        t = tot[a]
        print("  %-8s tags %d  jumps %d  path %d deg  tags that wander (>1 jump or az span >25): %d" % (a, t["tags"], t["jumps"], t["path"], t["wild"]))


if __name__ == "__main__":
    main()
