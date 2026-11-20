# -*- coding: utf-8 -*-
"""
Reader Management (RM) over HTTP/XML for the ATR7000 admin console (POST https://<reader>/control).

The wire format is the one the reader's own login/region/LLRP pages use. The RM interface allows ONE logged-in
session: logging in from here logs the browser admin console out (and fails with AdminLoggedIn while the console is
open, because this client never forces a login). Every call therefore does login -> commands -> logout.

Credentials come from rfid-bridge/reader.conf (git-ignored, JSON: host, user, password). Nothing here is ever
sent to the web page or printed. Only the read-only commands in READ_ONLY are exposed to the page.
"""
import json
import os
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reader.conf")
HEADER = ('<?xml version="1.0" encoding="UTF-8"?><rm:command epcglobal:creationDate="2001-12-17T09:30:47.0Z" '
          'epcglobal:schemaVersion="0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
          'xmlns:rm="urn:epcglobal:rm:xsd:1" xmlns:epcglobal="urn:epcglobal:xsd:1" xmlns:motorm="urn:motorfid:rm:xsd:1">')
READ_ONLY = ("getReaderVersionInfo", "getName", "getModel", "getLocalTime", "getCPUUsage", "getRAMUsage",
             "getActiveRegion", "isLLRPRunning", "getLLRPConfig")


class RMError(Exception):
    pass


def load_conf():
    try:
        with open(CONF, encoding="utf-8") as f:
            c = json.load(f)
    except FileNotFoundError:
        raise RMError("ไม่พบไฟล์ rfid-bridge/reader.conf (host, user, password)")
    except ValueError:
        raise RMError("reader.conf ไม่ใช่ JSON ที่ถูกต้อง")
    for k in ("host", "user", "password"):
        if not isinstance(c.get(k), str) or not c[k]:
            raise RMError("reader.conf ขาดค่า %s" % k)
    return c


def _flat(root):
    """tag (namespace stripped) -> list of leaf texts"""
    out = {}
    for el in root.iter():
        if len(el) == 0 and el.text and el.text.strip():
            out.setdefault(el.tag.split("}")[-1], []).append(el.text.strip())
    return out


class RM:
    def __init__(self, conf=None, timeout=8):
        self.c = conf or load_conf()
        self.timeout = timeout
        self.sid = "NotSet"
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        try:
            self.ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        except ssl.SSLError:
            pass

    def _post(self, inner):
        body = HEADER + "<rm:id>104</rm:id><rm:targetName></rm:targetName><motorm:readerDevice><motorm:sessionID>%s</motorm:sessionID>%s</motorm:readerDevice></rm:command>" % (self.sid, inner)
        req = urllib.request.Request("https://%s/control" % self.c["host"], body.encode("utf-8"), {"Content-Type": "text/xml"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                txt = r.read()
        except OSError as e:
            raise RMError("ต่อ reader ไม่ได้ (%s)" % type(e).__name__)
        try:
            root = ET.fromstring(txt)
        except ET.ParseError:
            raise RMError("คำตอบจาก reader อ่านไม่ได้")
        flat = _flat(root)
        code = (flat.get("resultCode") or ["0"])[0]
        if code != "0":
            desc = (flat.get("description") or flat.get("customErrorCode") or [code])[0]
            raise RMError("reader ปฏิเสธ: %s" % desc)
        return flat

    def login(self):
        inner = "<motorm:doLogin><motorm:username>%s</motorm:username><motorm:password>%s</motorm:password><motorm:enforceDefPwdChange>true</motorm:enforceDefPwdChange></motorm:doLogin>" % (
            escape(self.c["user"]), escape(self.c["password"]))
        flat = self._post(inner)
        sid = (flat.get("sessionID") or [None])[0]
        if not sid:
            raise RMError("reader ไม่ส่ง session กลับมา")
        self.sid = sid

    def logout(self):
        if self.sid != "NotSet":
            try:
                self._post("<motorm:doLogout><motorm:forceLogout>false</motorm:forceLogout></motorm:doLogout>")
            except RMError:
                pass
            self.sid = "NotSet"

    def read(self, name):
        if name not in READ_ONLY:
            raise RMError("คำสั่งนี้ไม่อนุญาต")
        return self._post("<motorm:%s/>" % name)


def read_info():
    """login, read the read-only facts, logout (always)"""
    rm = RM()
    try:
        rm.login()
    except RMError as e:
        msg = str(e)
        if "LoggedIn" in msg or "logged" in msg.lower():
            msg += " — ออกจากระบบหน้าแอดมินของ reader ในเบราว์เซอร์ก่อน (RM ใช้ได้ทีละ session)"
        raise RMError(msg)
    out = {}
    try:
        for name in READ_ONLY:
            try:
                out[name] = rm.read(name)
            except RMError as e:
                out[name] = {"error": [str(e)]}
    finally:
        rm.logout()
    return out


def summary():
    """read_info() reduced to what the page shows"""
    d = read_info()
    one = lambda cmd, key: (d.get(cmd, {}).get(key) or [None])[0]
    v = d.get("getReaderVersionInfo", {})
    vers = []
    for i, n in enumerate(v.get("VersionName", [])):
        try:
            vers.append([n, "%s.%s.%s.%s" % (v["MajorVersion"][i], v["MinorVersion"][i], v["BuildNumber"][i], v["MaintenanceNumber"][i])])
        except (KeyError, IndexError):
            pass
    ram = {k: one("getRAMUsage", k) for k in ("total", "used", "free")}
    return {
        "name": one("getName", "returnValue"), "model": one("getModel", "returnValue"), "time": one("getLocalTime", "returnValue"),
        "cpu": {"user": one("getCPUUsage", "user"), "system": one("getCPUUsage", "system")}, "ram": ram,
        "region": one("getActiveRegion", "regionName"),
        "llrp": {"running": one("isLLRPRunning", "LLRPStartStatus"), "port": one("getLLRPConfig", "portNum"),
                 "secure": one("getLLRPConfig", "IsSecure"), "validate_peer": one("getLLRPConfig", "ValidatePeerInSecureMode")},
        "versions": vers,
    }
