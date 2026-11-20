# ATR7000 management panel - design

## Intent
Let the user manage the ATR7000 from the Floor 360 page (Settings > "Manage ATR7000") instead of switching to the reader's own web console.

## Approach (chosen by the user: LLRP + RM admin)
Two channels, built in three phases.

| Phase | What | Channel |
|---|---|---|
| 1 | Reader status/capabilities, TX power and enable/disable per beam group (301-397), scan mode and dwell, read counters | LLRP (the connection the bridge already holds; no password) |
| 2 | Read-only: model/firmware, region, network, LLRP state, CPU/RAM/temperature, system log | RM over HTTP/XML |
| 3 | Writes: reader name, time/NTP, region, LLRP config, watchdog, reboot | RM over HTTP/XML |

## Constraints
- RM allows one logged-in session; logging in from the bridge logs out the browser admin console and vice versa. The bridge logs in only while a management page is open and logs out right after each batch.
- Admin login lives in `rfid-bridge/reader.conf` (git-ignored, mode 600). It is never sent to the page, logged or committed.
- Every write asks for confirmation that states the effect. Region, LLRP config and reboot (which can drop the bridge link) need a typed confirmation.
- Out of scope (do it in the reader console): firmware update, users/passwords, certificates, enterprise reset.
- Write commands are first tried read-only against the real reader, one command at a time, before any write is added.

## Anti-goals
Anything that can brick or lock out the reader without an explicit, typed confirmation.

## Left to the builder
XML request format for RM (see docs/zebra/interface-control-guide-en.txt, "Reader Management"), page layout (iOS-style drill-down pages already used by the Settings drawer).
