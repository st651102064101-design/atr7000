# ATR7000 RFID live direction

Zebra ATR7000 (Zebra IoT Connector, HTTP POST) -> `rfid-bridge/bridge.py` -> WebSocket -> `web/ATR7000_Floor_360_RFID_Test_Lab.html`.

## Run
1. Reader: Communication > Zebra IoT Connector > Configuration, endpoint type HTTP POST, URL `http://<this-pc-ip>:8080/rfid`.
2. Reader: Operating Mode = Simple, Tag Metadata RSSI + ANTENNA (+ SEEN_COUNT), Report Filter type PER_ANTENNA, enable the LHCP ports 301-397.
3. PC: `python rfid-bridge/bridge.py` (Python 3.8+, no dependencies). Allow TCP 8080 in the firewall.
4. Open `web/ATR7000_Floor_360_RFID_Test_Lab.html` in a browser. It connects to `ws://localhost:8765` automatically.

Bridge endpoints: `POST /rfid`, `POST /simulate`, `GET /health`, `GET /last`. Raw payloads are logged to `rfid-bridge/raw_payloads.jsonl` (git-ignored).

Direction is estimated from the strongest LHCP beams (beam table in the ATR7000 Integration Guide, Table 4); resolution is about 15 degrees.
