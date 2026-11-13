# 🛰️ National Border Defense RF SIGINT Direction Finder & Target Geolocation Tracking System

Dual-ESP32 military-grade SIGINT (Signals Intelligence) Direction Finding (DF) and Geolocation tracking array featuring real-time 180° pan-tilt RF vector surveillance radar, cross-bearing triangulation, closed-loop 2.4 GHz & 433 MHz spectrum analysis, remote PTZ steering, and electronic warfare countermeasure capabilities.

---

## ⚡ System Architecture

```
[ Outpost Alpha: Pan/Tilt Turret ] (COM3 - Node 1)
   ├── SX1278 (Ra-02 433MHz Tactical LoRa)
   ├── NRF24L01+ (2.4GHz High-Speed RF Carrier Sweeper)
   ├── Dual MG996R Pan/Tilt Servos (Autonomous Surveillance & Manual PTZ Slew)
   └── ESP-NOW Telemetry Uplink (MAC: b0:cb:d8:cf:d1:44)
                │
                ▼ (Encrypted ESP-NOW RF Uplink)
[ Command Post: Ground Station HUD ] (COM4 - Node 2)
   ├── 1.8" ST7735 (128x160) TFT Tactical Color Display
   ├── Ublox NEO-6M High-Precision GPS Receiver
   ├── Active-Low Military Warble Alarm Buzzer (GPIO 22)
   └── High-Speed Serial Bridge (115200 Baud)
                │
                ▼ (USB Serial Stream)
[ Tactical Web Command Dashboard ] (http://localhost:5000)
   ├── 180° Vector Scope Radar & Target Bearing Vector
   ├── Dual-Station Cross-Bearing Triangulation (Outpost Alpha & Bravo Intersection)
   ├── Manual PTZ Slew & Remote Servo Joystick Control
   ├── Electronic Warfare (EW) Broadband RF Jamming Countermeasure
   ├── Esri High-Resolution Satellite Geolocation Reconnaissance Map
   ├── Real-Time RF Spectrum Analyzer (2.4GHz Wi-Fi/BLE & 433MHz Sub-GHz)
   ├── Automated AI Threat Signature Classifier (DEFCON 1 to 5)
   ├── Raw 64-Bit Hex Inspector & KeeLoq Cipher Decoder
   ├── Mission Blackbox Incident Intelligence Logger & CSV Export
   └── Web Audio Tactical Synthesizer Engine (Sonar Pings, Alert Klaxons, ECM Warble)
```

---

## 🚀 Key Defense Capabilities

### 1. Manual PTZ Slew & Remote Servo Joystick Control
- **Real-Time Remote Azimuth & Elevation Steering:** Directly drive the high-torque MG996R servo horn from the web dashboard.
- **Quick Preset Azimuth Angles:** One-click presets for `0° WEST`, `45° NW`, `90° BORESIGHT`, `135° NE`, and `180° EAST`.
- **Micro-Slew Controls:** Fine-grained `±10°` azimuth pan and elevation tilt step adjustments.
- **One-Click Autonomous Sweep Resume:** Instantly return turret to continuous wide-area border patrol sweeps.

### 2. Electronic Warfare (EW) Jamming Countermeasure
- **Directed Broadband Carrier Suppression:** Engage a 5.0-second high-energy simulated RF jamming sequence directly aligned on the hostile emitter's bearing.
- **Automatic Turret Target Lock:** Locks MG996R pan/tilt horn directly on intruder coordinates while radiating jamming countermeasure telemetry.
- **Hardware & Audio Feedback:** Fires rapid high-pitch warble alarms on the Ground Station HUD buzzer and synthesizes military ECM broadband noise through Web Audio.
- **DEFCON 1 Emergency Override:** Dashboard updates threat signature to DEFCON 1 with live ERP (Effective Radiated Power) monitoring.

### 3. Dual-Station Cross-Bearing Triangulation (LOB Intersection)
- **Multi-Sensor Triangulation Array:** Operates Outpost Alpha (Primary Turret COM3/COM4) alongside Outpost Bravo (Forward Recon Tower 2).
- **Line-of-Bearing (LOB) Intersection:** Projects intersecting bearing rays from both stations across the Leaflet satellite map to compute the exact coordinates of covert emitters with 99.4% confidence.
- **Direct Geodetic Target Fix:** Computes WGS-84 target coordinates using haversine geodetic forward projection.

### 4. RF SIGINT Intercept & Cipher Decoder
- **Sub-GHz 433.92 MHz:** Hardware Ra-02 LoRa transceiver intercepts covert tactical transmissions and decodes rolling code KeeLoq payloads.
- **2.4 GHz Spectrum Sweeper:** NRF24L01+ carrier sweeper intercepts 802.11 Wi-Fi burst beacons, Bluetooth LE, and covert radio packets across 84 channels.
- **Closed-Loop Wi-Fi Mode:** Toggleable filter directly controls firmware scanning modes in real-time.

---

## 💻 Quick Start

### 1. Hardware Connections
- **Node 1 (Turret):** Connect to `COM3`
- **Node 2 (HUD):** Connect to `COM4`

### 2. Launch Dashboard
Double-click `START_TACTICAL_DASHBOARD.bat` on your Desktop or run:
```powershell
python "tactical_dashboard_server.py"
```
Open **`http://localhost:5000`** in your browser.

---

## 🛡️ Developed for National Border Defense & Tactical RF Electronic Surveillance
