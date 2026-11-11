# ⚔️ Raven Forge: Mining, Firmware & Cooling Lab

Home-lab notes, tools and tuning logs for my solo-mining rigs, built around
the **Bitaxe Gamma Hex 1300 Hydro ("Loki's Bifrost")** and the retired/blown-up
**NerdQAxe++ Hydro**. Everything here is Norse-themed, hands-on and automated
where possible.

## 1. Hardware

| Rig | ASICs | Cooling | Firmware | Status |
|---|---|---|---|---|
| Bitaxe Gamma Hex 1300 Hydro ("Loki's Bifrost") | 6x BM1370 (2 domains of 3) | AIO liquid, push/pull fans | AxeOS / ESP-Miner 2.15.3 | Active |
| NerdQAxe++ Hydro | 4x BM1370 | Liquid (VR cooler) | ESP-Miner-NerdQAxePlus | Retired (failed Aug 2026) |

### Loki's Bifrost: Gamma Hex 1300 Hydro
- Board: ESP32-S3, 1.9" LCD, 12 V input, four-phase TPS546D24S regulator, EMC2103 fan controller (open-source KiCad design, CERN-OHL).
- Factory spec: ~8.4 TH/s at ~140-150 W; overclocked ~10.5-11 TH/s at ~191-200 W.
- **My current tune:** 960 MHz @ 1.255 V -> ~12.2 TH/s at 205 W, ASIC 49 °C, VRM 45 °C.
- Input voltage set to 12.1 V.
- Power: interim MEAN WELL 350 W for the board; a **second** MEAN WELL 350 W runs the fans only.
- Cooling mods:
  - Original single RGB fan replaced with a high-CFM Noctua (push).
  - Extra 3000 RPM fan pulling through the radiator (pull).
  - VRM heatsinks plus a blower.
  - Fresh MX-7 paste: ASIC1 vs ASIC2 delta improved from ~3 °C to ~0.9 °C.
- Bought from SoloSatoshi. Pool: TODO

### NerdQAxe++ Hydro
- 4x BM1370, stock ~4.8 TH/s at ~72-76 W (~15-16 J/TH), ESP32-S3, AxeOS.
- Ran on CKPool at 192.168.68.102; VR cooler fitted.
- Firmware: shufps/ESP-Miner-NerdQAxePlus.
- Lessons: watch reboot frequency, pick a stable firmware, never skimp on VRM cooling.

> NerdQAxe firmware does **not** run on the Gamma Hex. The Hex uses the
> Bitaxe/ESP-Miner multichip lineage. Keep the two builds separate.

## 2. Software Projects

| Project | Language | Purpose |
|---|---|---|
| **LoKI-Miner** (v1.1.0 base) | Python | Custom GUI dashboard for the Gamma Hex |
| **RavenMiner HQ** | Python | Multi-miner monitoring GUI |
| **NerdQAxe-Dashboard** (Raven-Black00) | TODO | Dashboard for the NerdQAxe++ |
| **Selene Forge GUI** | Python + Docker | Builds Hex firmware: clones ESP-Miner and libsecp256k1, unpacks zip/rar sources, validates, reads config.csv, shows a Norse console with Start/Pause/Cancel |
| **Valhalla Flasher** (v2.0) | Python/Tkinter, esptool | Serial and IP/OTA flasher: www.bin -> wait for reload -> esp-miner.bin -> reboot; packaged with PyInstaller |
| Temp/efficiency logger | Python | Polls AxeOS, finds the J/TH knee point |

## 3. Firmware Build Workflow

```bash
# NerdQAxe++ fork
git clone [https://github.com/shufps/ESP-Miner-NerdQAxePlus](https://github.com/shufps/ESP-Miner-NerdQAxePlus)
cd ESP-Miner-NerdQAxePlus
git submodule update --init --recursive

# Upstream Bitaxe / Hex (devcontainer approach)
git clone [https://github.com/bitaxeorg/ESP-Miner.git](https://github.com/bitaxeorg/ESP-Miner.git)
cd ESP-Miner
docker build -t espminer-build .devcontainer
docker run --rm -it -v $(pwd):/workspace espminer-build /bin/bash
idf.py build
./merge_bin.sh ./esp-miner-merged.bin
```

Notes:
- Artifacts: `esp-miner.bin`, `www.bin` (check `flash_args` for the real offsets).
- Needs libsecp256k1 in `components/`.
- Stack: Windows + WSL2 + Docker + ESP-IDF; Windows .exe via PyInstaller.

## 4. Tuning Playbook
1. Change one variable at a time: frequency, then core voltage.
2. Log hashrate, error %, ASIC temp, VRM temp, power, J/TH.
3. Hold ASIC/VRM temps at or below target (currently 49 °C / 45 °C).
4. Stop where J/TH efficiency falls off (the knee).
5. Fan curve tied to chip temperature (TODO: controller choice).
6. Peltier/TEC cooling was ruled out: the cold plate is rated ~300 W and chips
   sit near 50-60 °C, so a TEC would add more heat than it removes.
   A small TEC or better heatsink on the VRM area is the only plausible use.

## 5. Interests & Roadmap
- ASIC control loops and auto-tuning
- Embedded displays on ESP32/ESP32-S3
- OTA flashing and API integration (AxeOS REST)
- Telemetry visualisation, animations, Viking UI and sound effects
- Cooling: liquid, air, thermoelectric experiments
- TODO: fan-curve controller, alerting on over-temp, release packaging

## 6. Credits
Bitaxe and ESP-Miner open-source community, shufps (NerdQAxe), SoloSatoshi.
*May the Norns weave good blocks.* ᚱᚨᚢᚾ