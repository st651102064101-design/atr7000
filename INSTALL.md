# Installation

Zebra ATR7000 -> bridge (Python) -> WebSocket -> browser page (Floor 360 RFID Test Lab).

## 1. Requirements

- Windows, macOS or Linux PC on the same network as the reader
- Python 3.8 or newer (standard library only, no `pip install` needed)
- A browser (Chrome or Edge)
- Zebra ATR7000 reachable from the PC (example: reader `192.168.1.10`, PC `192.168.1.1`)

Check Python:

```powershell
python --version
```

## 2. Get the code

```powershell
git clone https://github.com/st651102064101-design/atr7000.git
cd atr7000
```

## 3. Prepare the reader (LLRP mode, recommended)

In the reader web UI (log in yourself):

1. Communication > Zebra IoT Connector > Configuration > Interface Configuration:
   set Tag Data Interface and Management Events Interface to `None`
   (otherwise the reader's LLRP server does not run).
2. Close any "Reader Operation" page that is connected over LLRP. The reader accepts only one LLRP client.
3. Enable the LHCP antenna ports 301-397 and set the transmit power you need (27 dBm was used for testing).
4. Leave LLRP secure mode as it is. The bridge connects with TLS on port 5085 and does not verify the certificate.

## 4. Allow the network ports on the PC

The WebSocket (8765) is only used on this PC. TCP 8080 is needed only for the IoT Connector (HTTP POST) path.

```powershell
New-NetFirewallRule -DisplayName "ATR7000 bridge 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
```

Run it in an administrator PowerShell, or add the rule by hand in Windows Defender Firewall.

## 5. Start the bridge

Run from the `rfid-bridge` folder (it imports the sibling `llrp.py`, so do not use `python -I`):

```powershell
cd rfid-bridge
python bridge.py
```

Expected output:

```
[llrp] connected to 192.168.1.10:5085 (TLS)
[llrp] ADD_ROSPEC status 0
[llrp] streaming tag reports
```

Options:

| Option | Meaning | Default |
|---|---|---|
| `--llrp-host H` | reader IP | `192.168.1.10` |
| `--llrp-port P` | LLRP port | `5085` |
| `--llrp-plain` | plain LLRP on 5084 (only if secure mode is off) | off |
| `--ants 301-397` | antenna (beam) ids to scan | `301-397` |
| `--dwell MS` | dwell per beam in ms, below 15 the reads collapse, 0 = reader default | `20` |
| `--no-llrp` | disable LLRP and use only the HTTP POST receiver | off |

Example for a different reader IP:

```powershell
python bridge.py --llrp-host 192.168.1.20
```

## 6. Open the web page

Open `web/ATR7000_Floor_360_RFID_Test_Lab.html` in the browser. It connects to `ws://localhost:8765` automatically and shows live tags. Place a tag above the reader (inside the 0-60 degree cone, at least 1.5 m high for best accuracy).

## 7. Alternative: IoT Connector HTTP POST (batched about every 2.4 s)

1. Reader: Communication > Zebra IoT Connector > Configuration, endpoint type HTTP POST, URL `http://<pc-ip>:8080/rfid`, map it to the Tag Data and Management Events interfaces.
2. Reader: Operating Mode = Simple, Tag Metadata RSSI + ANTENNA (+ SEEN_COUNT), Report Filter Duration 0, type PER_ANTENNA.
3. PC: `python bridge.py --no-llrp`

To go back to LLRP, set both interfaces to `None` again and run `python bridge.py`.

## 8. Check it works

```powershell
curl http://localhost:8080/health
curl http://localhost:8080/last
```

Test without a reader (synthetic read):

```powershell
curl -X POST http://localhost:8080/simulate
```

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `[llrp] not connected ... retrying every 3 s` | IoT endpoint still mapped to an interface, another LLRP client open (close Reader Operation), wrong IP, or the PC cannot reach the reader |
| Connected but no tag reads | tag outside the beam cone, ports 301-397 not enabled, tag too far for the power setting |
| Few reads or none with `--dwell` under 15 | dwell too short, use 20 |
| Page says offline | bridge not running, or port 8765 blocked by another program |
| Page shows no data in HTTP POST mode | firewall blocks TCP 8080, or wrong PC IP in the endpoint URL |

Raw payloads are logged to `rfid-bridge/raw_payloads.jsonl` (git-ignored).

## 10. Documentation

Zebra manuals are in `docs/zebra/` (ATR7000 Integration Guide, LLRP Interface Control Guide, SDK guides). See `README.md` for the direction estimation notes.
