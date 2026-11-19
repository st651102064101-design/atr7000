# ATR7000 RFID live direction

Two ways to get tag reads from the reader into the web page:

- **LLRP (recommended, real-time, ~70-180 reads/s):** `rfid-bridge/llrp.py` connects to the reader's LLRP server (default TLS port 5085, no certificate check), installs one inventory ROSpec over beams 301-397 with a per-beam dwell (default 20 ms, whole sweep ~2.2 s) and streams every tag read. Requires that no IoT Connector endpoint is mapped to the Tag Data / Management Events interfaces (Communication > Zebra IoT Connector > Configuration > Interface Configuration = None), otherwise the reader's LLRP server is not running.
- **IoT Connector HTTP POST (batched about every 2.4 s):** original path, see below. Use `--no-llrp` to disable the LLRP client.

Bridge options: `--llrp-host H`, `--llrp-port P`, `--llrp-plain` (port 5084, only if secure mode is off), `--ants 301-397`, `--dwell MS` (below 15 ms the reads collapse), `--no-llrp`.

Zebra ATR7000 (Zebra IoT Connector, HTTP POST) -> `rfid-bridge/bridge.py` -> WebSocket -> `web/ATR7000_Floor_360_RFID_Test_Lab.html`.

## Run
1. Reader: Communication > Zebra IoT Connector > Configuration, endpoint type HTTP POST, URL `http://<this-pc-ip>:8080/rfid`.
2. Reader: Operating Mode = Simple, Tag Metadata RSSI + ANTENNA (+ SEEN_COUNT), Report Filter type PER_ANTENNA, enable the LHCP ports 301-397.
3. PC: `python rfid-bridge/bridge.py` (Python 3.8+, no dependencies). Allow TCP 8080 in the firewall.
4. Open `web/ATR7000_Floor_360_RFID_Test_Lab.html` in a browser. It connects to `ws://localhost:8765` automatically.

Bridge endpoints: `POST /rfid`, `POST /simulate`, `GET /health`, `GET /last`. Raw payloads are logged to `rfid-bridge/raw_payloads.jsonl` (git-ignored).

Direction is estimated from the strongest LHCP beams (beam table in the ATR7000 Integration Guide, Table 4); resolution is about 15 degrees.
