# 🛰️ National Border RF SIGINT Direction Finder & Geolocation Tracking System

A dual-node tactical Signals Intelligence (SIGINT) border defense system built on dual ESP32 microcontrollers, dual-axis Pan/Tilt servo tracking, multi-band RF reception (2.4 GHz & 433 MHz), geodetic target triangulation, real-time 1.8" ST7735 TFT HUD, and a full-featured web command dashboard.

---

## 🎛️ System Architecture

```
                      +---------------------------------------+
                      |       AIRBORNE / EMITTER TARGET       |
                      |  433MHz LoRa / 2.4GHz Mobile Bursts   |
                      +---------------------------------------+
                                          |
                                          v  (RF Signals)
+-----------------------------------------------------------------------------------+
| NODE 1: TURRET DIRECTION FINDER (COM3)                                            |
| - MG996R Pan/Tilt High-Torque Servos (Bearing Azimuth 0-180°, Elevation Tilt)    |
| - SX1278 LoRa Receiver (433 MHz Tactical Reconnaissance Band)                     |
| - NRF24L01+ Transceiver (2.4 GHz Multi-Channel RF Energy Detector)                |
| - ESP-NOW Real-Time High-Speed Packet Telemetry Link                              |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  (ESP-NOW Wireless Telemetry)
+-----------------------------------------------------------------------------------+
| NODE 2: GROUND STATION HUD & GEOLOCATION (COM4)                                   |
| - 1.8" ST7735 SPI Color TFT Display (160x128 Tactical Radar & Target Vector)       |
| - Ublox NEO-6M GPS Module (Base Station Reference Coordinates & UTC Time)         |
| - Active Tactical Buzzer (GPIO 22 Alert Chirp on Target Lock)                      |
| - USB Serial Bridge (10 Hz JSON/Telemetry Stream)                                 |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  (USB Serial Streaming)
+-----------------------------------------------------------------------------------+
| TACTICAL WEB COMMAND DASHBOARD (http://localhost:5000)                             |
| - 180° Sector Surveillance Radar Scope with Dynamic Needle & Blip Vector           |
| - Target Geodetic GPS Triangulation (Bearing + Log-Distance Range Equation)       |
| - Interactive Closed-Loop Wi-Fi & Mobile Burst Scanner Toggle                      |
| - Raw SIGINT Hex Payload Inspector (8-byte / 64-bit RF frame)                     |
| - Scrambled Ciphertext ("Random Word" / Rolling Code KeeLoq) Decoder               |
+-----------------------------------------------------------------------------------+
```

---

## 🚀 Key Features

1. **Dual-Band Signals Intelligence (SIGINT):**
   - **433 MHz Tactical LoRa Band:** Intercepts long-range covert military transmitters and car key fob rolling code frames.
   - **2.4 GHz Mobile/Covert Band:** Scans Wi-Fi and mobile bursts across 14 channels to detect nearby phones, hotspots, or covert drone links.
2. **Servo Radar Sweep & Auto-Lock:**
   - Smooth continuous perimeter surveillance sweep (0° to 180°).
   - Upon RF intercept, immediately stops sweeping and locks pan/tilt bearing angle directly onto the emitter with a 2.5-second dwell hold.
3. **Target Geolocation Engine:**
   - Utilizes direct geodetic projection from NEO-6M GPS base coordinates combined with the Line-of-Bearing (LOB) and RSSI log-distance path loss to calculate the exact latitude and longitude of the hostile transmitter.
4. **Raw Payload & Scrambled Ciphertext Decoder ("Random Word"):**
   - Intercepts 64-bit RF frames in real time.
   - Displays raw hexadecimal bytes and decodes scrambled rolling-code characters on both the TFT HUD and Web Dashboard.
5. **Closed-Loop Remote Control:**
   - One-click toggle on the web dashboard to enable/mute 2.4 GHz mobile scanning to avoid interference with ambient home Wi-Fi when needed.
6. **Tactical Audio Alert:**
   - Connected to GPIO 22 on Node 2 with active-low pulsing logic for distinct audio alarm during target lock.

---

## 📌 Hardware Pinout & Wiring

### Node 1: Turret Unit (ESP32 Dev Module)
| Component | Pin Function | ESP32 GPIO |
| :--- | :--- | :--- |
| **MG996R Pan Servo** | PWM Signal | `GPIO 13` |
| **MG996R Tilt Servo** | PWM Signal | `GPIO 25` |
| **SX1278 LoRa** | NSS (CS) | `GPIO 5` |
| **SX1278 LoRa** | RST | `GPIO 14` |
| **SX1278 LoRa** | DIO0 | `GPIO 26` |
| **SX1278 LoRa** | SCK / MISO / MOSI | `GPIO 18 / 19 / 23` |
| **NRF24L01+** | CE | `GPIO 22` |
| **NRF24L01+** | CSN | `GPIO 27` |
| **NRF24L01+** | SCK / MISO / MOSI | `GPIO 18 / 19 / 23` |

### Node 2: Ground Station HUD (ESP32 Dev Module)
| Component | Pin Function | ESP32 GPIO |
| :--- | :--- | :--- |
| **ST7735 TFT Display** | MOSI (SDA) | `GPIO 23` |
| **ST7735 TFT Display** | SCLK (SCK) | `GPIO 18` |
| **ST7735 TFT Display** | CS | `GPIO 15` |
| **ST7735 TFT Display** | DC / A0 | `GPIO 2` |
| **ST7735 TFT Display** | RESET | `GPIO 4` |
| **NEO-6M GPS** | TX ➔ ESP32 RX | `GPIO 17` |
| **NEO-6M GPS** | RX ➔ ESP32 TX | `GPIO 16` |
| **Tactical Buzzer** | Signal (I/O or `+`) | `GPIO 22` |

---

## 💻 Software & Dashboard Quickstart

### 1. Requirements
- Python 3.8+
- `pyserial` (`pip install pyserial`)
- PlatformIO (VS Code extension or CLI)

### 2. Flashing Node Firmware
```bash
# Upload Node 1 (Turret Unit)
pio run --target upload --upload-port COM3 -d firmware/node1_turret

# Upload Node 2 (Ground Station HUD)
pio run --target upload --upload-port COM4 -d firmware/node2_ground_hud
```

### 3. Running the Tactical Command Dashboard
```bash
python tactical_dashboard_server.py
```
Open your browser and navigate to:
👉 **`http://localhost:5000`**

---

## 🛡️ License
Released under the MIT License for educational, defensive, and research purposes.
