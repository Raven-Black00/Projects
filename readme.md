# Alan's ASIC Mining Lab

A home lab for building, tuning, cooling, and monitoring open-source ASIC Bitcoin miners. It includes custom firmware builds, Python dashboards, flashing tools, and experiments with liquid, air, and thermoelectric cooling.

Based in Waterloo, Ontario. I build things, test them, break them, and build them again.

---

## Table of Contents

- [Hardware Fleet](#hardware-fleet)
  - [Gamma Hex 1300 Hydro](#gamma-hex-1300-hydro)
  - [NerdQAxe++ Hydro](#nerdqaxe-hydro)
- [Cooling Experiments](#cooling-experiments)
- [Firmware Development](#firmware-development)
- [Software and Tools](#software-and-tools)
- [ESP32 Display Projects](#esp32-display-projects)
- [Monitoring and Telemetry](#monitoring-and-telemetry)
- [Tuning Methodology](#tuning-methodology)
- [Development Environment](#development-environment)
- [Roadmap](#roadmap)
- [Safety Notes](#safety-notes)

---

## Hardware Fleet

### Gamma Hex 1300 Hydro

A six-ASIC, water-cooled build in the Bitaxe Gamma family. The Hex layout spreads the hashing load across six chips. Water cooling gives it enough thermal headroom to run well past what stock air cooling can handle.

| Item | Notes |
|---|---|
| Platform | Bitaxe-derived Hex board (6x BM1370-class ASICs) |
| Controller | ESP32-S3 |
| Cooling | Custom water block, pump, radiator loop |
| Firmware | AxeOS-based, custom builds |
| Focus | Sustained high hashrate, VRM and ASIC thermal control, efficiency (J/TH) |

What I'm working on:
- Getting stable frequency and core voltage pairs across all six chips
- Keeping coolant delta-T and VRM temperatures inside safe limits
- Matching pump and radiator fan curves to load
- Logging power, voltage, frequency, hashrate, and error rate over long runs

### NerdQAxe++ Hydro

A four-ASIC NerdQAxe++ (BM1370) converted to liquid cooling. It runs the NerdQAxe / ESP-Miner-NerdQAxe firmware branch.

| Item | Notes |
|---|---|
| Platform | NerdQAxe++ (4x BM1370) |
| Controller | ESP32-S3 with onboard display |
| Cooling | Water block conversion (hydro) |
| Firmware | ESP-Miner-NerdQAxe, built from source |
| Focus | Overclock headroom, quiet operation, display UI customization |

What I'm working on:
- Building firmware from source and flashing it over web, USB, and OTA
- Custom display themes and screens on the ESP32-S3
- Tuning the PID fan/pump control loop and temperature targets
- Comparing hydro and air efficiency at the same frequency

---

## Cooling Experiments

| Method | Use | Notes |
|---|---|---|
| Liquid (hydro) | Gamma Hex 1300, NerdQAxe++ | Water blocks, pumps, radiators, coolant temp monitoring |
| Air | Single-chip Bitaxes, test benches | High-static-pressure fans, heatsink upgrades |
| Thermoelectric (TEC/Peltier) | Experimental | Sub-ambient testing; watch condensation and power draw |
| Thermal interface | All | Pads, paste, and putty compared on ASICs and VRMs |

Principles:
- Cool the VRMs as well as the ASICs. VRMs are often the real limit.
- Log ambient, coolant, ASIC, and VRM temperatures together.
- Change one variable per test.

---

## Firmware Development

- ESP-IDF toolchain for ESP32 and ESP32-S3
- Dockerized builds so the toolchain is the same every time
- WSL2 on Windows 11 for Linux-native builds
- CMake / component manager dependency handling
- Web UI (AxeOS) built with npm/Angular
- Flashing over esptool, web flasher, and OTA (`esp-miner.bin` + `www.bin`)

Typical build:

```bash
# Inside Docker / WSL2 with ESP-IDF
git clone <firmware-repo>
cd <firmware-repo>
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

Common issues I've hit and fixed:
- ESP-IDF version mismatches
- npm build failures in the web UI
- CMake component resolution errors
- Docker volume and permission problems under WSL2

---

## Software and Tools

| Tool | Stack | Purpose |
|---|---|---|
| Mining Dashboard | Python (GUI) | Live fleet view: hashrate, temps, power, shares |
| Flashing Utility | Python + esptool | One-click firmware flashing and OTA updates |
| Build Automation | Bash / PowerShell / Docker | Repeatable firmware builds |
| Windows Packaging | PyInstaller + batch | Standalone `.exe` tools and installers |

Design: themed interfaces (Norse/Viking style), custom sound effects and animation, and clear telemetry visualization.

---

## ESP32 Display Projects

- ESP32 / ESP32-S3 displays for miner stats
- Pulling data from the miner REST APIs over the local network
- Custom fonts, themes, and screen layouts
- Embedded C/C++ (moving from Python into C/C++)

---

## Monitoring and Telemetry

Metrics tracked per miner:
- Hashrate (instant and averaged)
- ASIC, VRM, and coolant temperature
- Input voltage, core voltage, and power (W)
- Frequency (MHz)
- Efficiency (J/TH)
- Shares, best difficulty, and error rate

Data comes from the AxeOS / NerdQAxe HTTP API on the LAN (for example `GET /api/system/info`).

---

## Tuning Methodology

1. Get a stock baseline (hashrate, power, temps).
2. Raise frequency in small steps and hold each step long enough to stabilize.
3. Adjust core voltage only when the error rate climbs.
4. Watch VRM and ASIC temps. Back off at the thermal limit.
5. Record efficiency at each step and pick the sweet spot, not the peak.
6. Run a soak test for 24h+ before calling it stable.

---

## Development Environment

- OS: Windows 11, Ubuntu, WSL2
- Containers: Docker / Docker Desktop
- Editor: Visual Studio Code
- Version control: Git + GitHub
- Languages: Python, C, C++, Bash, PowerShell, JavaScript/Node.js

---

## Roadmap

- [ ] Unified dashboard for the Gamma Hex 1300 Hydro and NerdQAxe++ Hydro
- [ ] Automatic tuning loop (frequency/voltage sweeps with logging)
- [ ] Coolant flow and temperature sensors added to telemetry
- [ ] Long-term data storage and historical charts
- [ ] One-click Windows installer for all tools
- [ ] More themed ESP32 display screens

---

## Safety Notes

- Overclocking and overvolting can damage hardware and void warranties.
- Leak-test water loops before powering on the hardware.
- Use power supplies with headroom above peak draw.
- TEC cooling can cause condensation. Insulate and monitor dew point.

---

Built in Waterloo, Ontario.
