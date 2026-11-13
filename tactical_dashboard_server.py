import sys
import time
import json
import threading
import math
import serial
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# Global Telemetry & Control State
telemetry_data = {
    "bearing": 0.0,
    "band": "PERIMETER SECURE",
    "rssi": -115,
    "dist": 0.0,
    "alert": False,
    "baseLat": 28.5412,
    "baseLon": 77.2031,
    "targetLat": 28.5412,
    "targetLon": 77.2031,
    "sats": 0,
    "utc": "--:--:--",
    "wifiDetection": False,
    "rawHex": "-- -- -- -- -- -- -- --",
    "ciphertext": "--------",
    "cipherType": "STANDBY // SECURE",
    "lastUpdate": time.time(),
    "node1Connected": False,
    "node2Connected": False
}

ser_com3 = None
ser_com3_lock = threading.Lock()
ser_com4 = None
wifi_override_until = 0.0
last_alert_lock_time = 0.0

def decode_payload_bytes(hex_str, is_wifi):
    try:
        clean_hex = hex_str.strip().replace(" ", "").upper()
        if len(clean_hex) >= 16:
            bytes_list = [clean_hex[i:i+2] for i in range(0, 16, 2)]
            formatted_hex = " : ".join(bytes_list)
            chars = []
            for b in bytes_list:
                val = int(b, 16)
                if 33 <= val <= 126 and chr(val) not in ['<', '>', '&', '"']:
                    chars.append(chr(val))
                else:
                    chars.append(["#", "$", "%", "&", "@", "!", "?", "*", "~", "^"][val % 10])
            cipher_word = "".join(chars)
            cipher_type = "2.4G WPA2 ENCRYPTED (MOBILE BURST)" if is_wifi else "433M ROLLING CODE CIPHER (KEELOQ)"
            return formatted_hex, cipher_word, cipher_type
    except:
        pass
    return "-- -- -- -- -- -- -- --", "--------", "STANDBY // SECURE"

def com3_worker():
    """Maintains persistent connection to Node 1 (Turret Unit on COM3) and logs ACKs."""
    global ser_com3, telemetry_data
    while True:
        try:
            if not ser_com3 or not ser_com3.is_open:
                print("[BRIDGE] Opening COM3 (Node 1 Turret Unit)...")
                s = serial.Serial()
                s.port = 'COM3'
                s.baudrate = 115200
                s.timeout = 1
                s.dtr = False
                s.rts = False
                s.open()
                time.sleep(1.8) # Allow ESP32 to stabilize after USB connection
                with ser_com3_lock:
                    ser_com3 = s
                telemetry_data["node1Connected"] = True
                print("[BRIDGE] COM3 Connected & Stabilized!")

            line = ser_com3.readline()
            if line:
                msg = line.decode('utf-8', errors='replace').strip()
                if msg:
                    print(f"[NODE 1 / COM3] {msg}")
                    if "WiFiMode: ON" in msg or "ACTIVATED" in msg:
                        telemetry_data["wifiDetection"] = True
                    elif "WiFiMode: OFF" in msg or "Muted" in msg:
                        telemetry_data["wifiDetection"] = False

                    if "HOSTILE" in msg:
                        global last_alert_lock_time
                        last_alert_lock_time = time.time()
                        telemetry_data["alert"] = True
                        telemetry_data["band"] = "2.4GHz COVERT/MOBILE" if "2.4GHz" in msg else "433MHz TACTICAL LORA"
                        for p in msg.split('|'):
                            p = p.strip()
                            if p.startswith("LOB:"):
                                try: telemetry_data["bearing"] = float(p.replace("LOB:", "").replace("°", "").strip())
                                except: pass
                            elif p.startswith("HEX:"):
                                hex_val = p.replace("HEX:", "").strip()
                                f_hex, f_ciph, f_type = decode_payload_bytes(hex_val, "2.4GHz" in msg)
                                telemetry_data["rawHex"] = f_hex
                                telemetry_data["ciphertext"] = f_ciph
                                telemetry_data["cipherType"] = f_type
                            elif p.startswith("SIG:"):
                                try:
                                    rssi_val = int(p.replace("SIG:", "").replace("dBm", "").strip())
                                    telemetry_data["rssi"] = rssi_val
                                    ratio = (-40.0 - float(rssi_val)) / (10.0 * 2.4)
                                    telemetry_data["dist"] = round(min(max(pow(10.0, ratio), 5.0), 500.0), 1)
                                    rad = math.radians(telemetry_data["bearing"])
                                    telemetry_data["targetLat"] = round(telemetry_data["baseLat"] + (telemetry_data["dist"] * math.cos(rad)) / 111139.0, 5)
                                    telemetry_data["targetLon"] = round(telemetry_data["baseLon"] + (telemetry_data["dist"] * math.sin(rad)) / (111139.0 * math.cos(math.radians(telemetry_data["baseLat"]))), 5)
                                except: pass
        except Exception as e:
            telemetry_data["node1Connected"] = False
            with ser_com3_lock:
                if ser_com3:
                    try: ser_com3.close()
                    except: pass
                    ser_com3 = None
            time.sleep(2)

def send_com3_cmd(cmd):
    """Sends command string directly to Node 1 via open COM3 port."""
    global ser_com3
    with ser_com3_lock:
        if ser_com3 and ser_com3.is_open:
            try:
                msg = (cmd.strip() + "\n").encode('utf-8')
                ser_com3.write(msg)
                ser_com3.flush()
                print(f"[BRIDGE -> COM3] Command Sent: {cmd}")
                return True
            except Exception as e:
                print(f"[BRIDGE ERROR] Write to COM3 failed: {e}")
                return False
        else:
            print("[BRIDGE ERROR] COM3 is not open yet!")
            return False

def com4_reader():
    """Reads live telemetry stream from Node 2 (Ground HUD & GPS on COM4)."""
    global telemetry_data, ser_com4, last_alert_lock_time
    while True:
        try:
            if not ser_com4 or not ser_com4.is_open:
                print("[BRIDGE] Opening COM4 (Node 2 Ground Station HUD)...")
                s = serial.Serial()
                s.port = 'COM4'
                s.baudrate = 115200
                s.timeout = 1
                s.dtr = False
                s.rts = False
                s.open()
                ser_com4 = s
                telemetry_data["node2Connected"] = True
                print("[BRIDGE] COM4 Connected!")

            line = ser_com4.readline()
            if line:
                s = line.decode('utf-8', errors='replace').strip()
                if s:
                    print(f"[NODE 2 / COM4] {s}")
                if "BORDER SEC HUD" in s or "TARGET LOCKED" in s or "PERIMETER PATROL" in s:
                    # Parse telemetry from COM4 log:
                    # [BORDER SEC HUD] 🚨 TARGET LOCKED | LOB: 014° | Band: 2 | WiFiMode: 0 | RSSI: -86dBm | Sats: 0 | BaseLat: 28.5412
                    parts = s.split('|')
                    lob = 0.0
                    band_id = 0
                    alert_level = 0
                    wifi_mode = 0
                    dist_in = 0.0
                    rssi = -115
                    sats = 0
                    base_lat = 28.5412

                    hex_val = ""
                    for p in parts:
                        p = p.strip()
                        if p.startswith("Alert:"):
                            try: alert_level = int(p.replace("Alert:", "").strip())
                            except: pass
                        elif p.startswith("Dist:"):
                            try: dist_in = float(p.replace("Dist:", "").strip())
                            except: pass
                        elif p.startswith("HEX:"):
                            hex_val = p.replace("HEX:", "").strip()
                        elif p.startswith("LOB:"):
                            try: lob = float(p.replace("LOB:", "").replace("°", "").strip())
                            except: pass
                        elif p.startswith("Band:"):
                            try: band_id = int(p.replace("Band:", "").strip())
                            except: pass
                        elif p.startswith("WiFiMode:"):
                            try: wifi_mode = int(p.replace("WiFiMode:", "").strip())
                            except: pass
                        elif p.startswith("RSSI:"):
                            try: rssi = int(p.replace("RSSI:", "").replace("dBm", "").strip())
                            except: pass
                        elif p.startswith("Sats:"):
                            try: sats = int(p.replace("Sats:", "").strip())
                            except: pass
                        elif p.startswith("BaseLat:"):
                            try: base_lat = float(p.replace("BaseLat:", "").strip())
                            except: pass

                    is_alert = (alert_level == 2) or ("TARGET LOCKED" in s) or ("TARGET_LOCKED" in s) or ("HOSTILE" in s)
                    band_str = "433MHz TACTICAL LORA" if band_id == 2 else ("2.4GHz COVERT/MOBILE" if band_id == 1 else "PERIMETER SECURE")

                    # Calculate Range and Target Coordinates
                    est_dist = dist_in if dist_in > 0 else 0.0
                    tgt_lat = base_lat
                    tgt_lon = 77.2031

                    if is_alert:
                        if est_dist <= 0:
                            ratio = (-40.0 - float(rssi)) / (10.0 * 2.4)
                            est_dist = round(min(max(pow(10.0, ratio), 5.0), 500.0), 1)
                        rad = math.radians(lob)
                        tgt_lat = round(base_lat + (est_dist * math.cos(rad)) / 111139.0, 5)
                        tgt_lon = round(77.2031 + (est_dist * math.sin(rad)) / (111139.0 * math.cos(math.radians(base_lat))), 5)

                    if is_alert:
                        last_alert_lock_time = time.time()
                        telemetry_data["alert"] = True
                        telemetry_data["band"] = band_str
                        telemetry_data["dist"] = est_dist
                        telemetry_data["targetLat"] = tgt_lat
                        telemetry_data["targetLon"] = tgt_lon
                        if hex_val:
                            f_hex, f_ciph, f_type = decode_payload_bytes(hex_val, band_id == 1)
                            telemetry_data["rawHex"] = f_hex
                            telemetry_data["ciphertext"] = f_ciph
                            telemetry_data["cipherType"] = f_type
                    elif time.time() - last_alert_lock_time < 2.8:
                        telemetry_data["alert"] = True
                    else:
                        telemetry_data["alert"] = False
                        telemetry_data["band"] = "PERIMETER SECURE"
                        telemetry_data["dist"] = 0.0
                        telemetry_data["targetLat"] = base_lat
                        telemetry_data["targetLon"] = 77.2031
                        telemetry_data["rawHex"] = "-- -- -- -- -- -- -- --"
                        telemetry_data["ciphertext"] = "--------"
                        telemetry_data["cipherType"] = "STANDBY // SECURE"

                    telemetry_data["bearing"] = lob
                    telemetry_data["rssi"] = rssi
                    telemetry_data["baseLat"] = base_lat
                    telemetry_data["sats"] = sats
                    if time.time() > wifi_override_until:
                        telemetry_data["wifiDetection"] = (wifi_mode == 1)
                    telemetry_data["utc"] = time.strftime("%H:%M:%S", time.gmtime())
                    telemetry_data["lastUpdate"] = time.time()
        except Exception as e:
            telemetry_data["node2Connected"] = False
            if ser_com4:
                try: ser_com4.close()
                except: pass
                ser_com4 = None
            time.sleep(2)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Tactical Border SIGINT Command Dashboard</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Consolas', 'Courier New', monospace; }
    body { background-color: #06090e; color: #e6edf3; padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #238636; padding-bottom: 12px; margin-bottom: 20px; }
    .header h1 { font-size: 20px; color: #39d353; letter-spacing: 2px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .card { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 18px; }
    .card-title { font-size: 13px; color: #8b949e; letter-spacing: 1.5px; border-bottom: 1px solid #21262d; padding-bottom: 8px; margin-bottom: 14px; display: flex; justify-content: space-between; }

    /* Leaflet Tactical Dark Styling */
    .leaflet-popup-content-wrapper { background: #0d1117; color: #e6edf3; border: 1px solid #30363d; font-family: 'Consolas', monospace; font-size: 11px; }
    .leaflet-popup-tip { background: #0d1117; }
    .leaflet-container { background: #010409; }
    
    /* Radar Canvas */
    .radar-box { display: flex; justify-content: center; align-items: center; position: relative; }
    canvas { background: #010409; border: 1px solid #161b22; border-radius: 8px; }

    /* Control Switch */
    .switch-box { background: #161b22; padding: 14px; border-radius: 6px; margin-bottom: 16px; border-left: 4px solid #238636; }
    .btn { background: #238636; color: #fff; border: none; padding: 10px 18px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; transition: 0.2s; }
    .btn.active { background: #da3633; box-shadow: 0 0 10px rgba(218,54,51,0.5); }
    .btn:hover { opacity: 0.9; }

    /* Telemetry Readout */
    .row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #161b22; font-size: 13px; }
    .val { font-weight: bold; }
    .val.alert { color: #f85149; }
    .val.safe { color: #39d353; }
    .val.coord { color: #58a6ff; font-size: 14px; }
    
    .status-banner { margin-top: 18px; padding: 12px; border-radius: 6px; text-align: center; font-weight: bold; font-size: 14px; letter-spacing: 1.5px; }
    .status-banner.safe { background: rgba(35, 134, 54, 0.2); border: 1px solid #238636; color: #39d353; }
    .status-banner.alert { background: rgba(218, 54, 51, 0.25); border: 1px solid #da3633; color: #f85149; animation: pulse 1s infinite; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.6; } 100% { opacity: 1; } }

    .badge-mode { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
    .badge-mode.muted { background: #30363d; color: #e3b341; }
    .badge-mode.live { background: #1f6feb; color: #ffffff; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>NATIONAL BORDER RECON // TACTICAL COMMAND DASHBOARD</h1>
      <small style="color: #8b949e;">RF SIGNALS INTELLIGENCE &amp; GEOLOCATION TRACKER</small>
    </div>
    <div>
      <button id="audio-toggle-btn" class="btn" style="background:#21262d; border:1px solid #30363d; padding:4px 10px; font-size:11px; margin-right:12px; color:#39d353;" onclick="toggleTacticalAudio()">🔊 TACTICAL AUDIO: ON</button>
      UTC: <span id="clock" style="color: #39d353; font-weight: bold;">--:--:--</span> |
      SATELLITES: <span id="sats" style="color: #58a6ff; font-weight: bold;">0</span>
    </div>
  </div>

  <div class="grid">
    <!-- Left: Radar Display -->
    <div class="card">
      <div class="card-title">
        <span>SECTOR SURVEILLANCE RADAR (180°)</span>
        <span id="lob-badge" style="color: #58a6ff;">BEARING: 000°</span>
      </div>
      <div class="radar-box">
        <canvas id="radarCanvas" width="460" height="230"></canvas>
      </div>
      <div id="status-banner" class="status-banner safe">BORDER PERIMETER: SECURE</div>

      <!-- Real-Time RF Spectrum Analyzer Canvas -->
      <div style="margin-top: 14px; background: #010409; border: 1px solid #21262d; border-radius: 6px; padding: 10px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <span style="font-size: 11px; color: #8b949e; font-weight: bold; letter-spacing: 1px;">RF SPECTRUM ANALYZER // 2.4GHz &amp; 433MHz CARRIER</span>
          <span id="spectrum-peak-badge" style="font-size: 10px; color: #39d353; font-weight: bold;">NOISE FLOOR: -112 dBm</span>
        </div>
        <canvas id="spectrumCanvas" width="460" height="85" style="width: 100%; height: 85px; background: #010409; border: 1px solid #161b22; border-radius: 4px;"></canvas>
        <div style="display: flex; justify-content: space-between; font-size: 9px; color: #8b949e; margin-top: 4px; padding: 0 4px;">
          <span>433.92M</span>
          <span>CH 1 (2.412G)</span>
          <span>CH 6 (2.437G)</span>
          <span>CH 11 (2.462G)</span>
          <span>BLE ADV (2.480G)</span>
        </div>
      </div>
    </div>

    <!-- Right: Telemetry & Controls -->
    <div class="card">
      <div class="card-title">
        <span>RF DETECTION CONTROL &amp; TARGET GEOLOCATION</span>
        <span id="rf-mode-badge" class="badge-mode muted">433MHz TACTICAL ONLY</span>
      </div>

      <!-- Wi-Fi Toggle Control -->
      <div class="switch-box" id="switch-box-elem">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-weight: bold; font-size: 14px;">2.4 GHz WI-FI &amp; MOBILE PHONE SCANNER</div>
            <div id="switch-desc" style="font-size: 11px; color: #8b949e; margin-top: 4px;">
              CURRENT: Muted (Filters Home Wi-Fi) | Tactical 433MHz Only
            </div>
          </div>
          <button id="toggle-btn" class="btn" onclick="toggleWifi()">ENABLE WI-FI SCAN</button>
        </div>
      </div>

      <!-- 433 MHz Tactical Drill Trigger -->
      <div class="switch-box" style="border-left-color: #d29922; margin-top: -6px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-weight: bold; font-size: 13px; color: #e3b341;">TACTICAL 433 MHz DRILL (WITHOUT WI-FI)</div>
            <div style="font-size: 11px; color: #8b949e; margin-top: 3px;">
              Simulates covert 433 MHz encrypted comms / key fob rolling code intercept.
            </div>
          </div>
          <button id="drill-btn" class="btn" style="background: #9e6a03;" onclick="trigger433Drill()">TRIGGER 433M DRILL</button>
        </div>
      </div>

      <!-- Telemetry Rows -->
      <div class="row"><span>INTERCEPT STATUS:</span><span id="threat-stat" class="val safe">SECURE</span></div>
      <div class="row"><span>ACTIVE BAND:</span><span id="band-val" class="val">---</span></div>
      <div class="row"><span>LINE-OF-BEARING (LOB):</span><span id="lob-val" class="val">000°</span></div>
      <div class="row"><span>ESTIMATED RANGE:</span><span id="dist-val" class="val">---</span></div>
      <div class="row"><span>SIGNAL STRENGTH:</span><span id="rssi-val" class="val">---</span></div>
      
      <div style="margin-top: 14px; padding: 10px; background: #010409; border: 1px solid #21262d; border-radius: 6px;">
        <div style="font-size: 11px; color: #8b949e; margin-bottom: 6px; font-weight: bold;">EXACT TARGET GPS COORDINATES (CALCULATED):</div>
        <div class="row" style="border: none; padding: 2px 0;"><span>TARGET LAT:</span><span id="tgt-lat" class="val coord">--.----° N</span></div>
        <div class="row" style="border: none; padding: 2px 0;"><span>TARGET LON:</span><span id="tgt-lon" class="val coord">--.----° E</span></div>
      </div>

      <!-- Raw SIGINT Packet & Ciphertext / Random Word Inspector -->
      <div style="margin-top: 14px; padding: 12px; background: #010409; border: 1px solid #30363d; border-radius: 6px; border-left: 4px solid #58a6ff;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 11px; color: #8b949e; font-weight: bold; letter-spacing: 1px;">RAW RF INTERCEPT &amp; CIPHER DECODER</span>
          <span id="cipher-type-badge" style="font-size: 10px; padding: 2px 6px; border-radius: 4px; background: #21262d; color: #58a6ff; font-weight: bold;">STANDBY // SECURE</span>
        </div>
        <div style="margin-bottom: 6px;">
          <div style="font-size: 10px; color: #8b949e;">RAW HEX PAYLOAD (8 BYTES / 64-BIT):</div>
          <div id="raw-hex-display" style="font-family: 'Consolas', monospace; font-size: 13px; color: #e3b341; letter-spacing: 1.5px; font-weight: bold; background: #0d1117; padding: 6px 8px; border-radius: 4px; margin-top: 2px; border: 1px dashed #30363d;">-- : -- : -- : -- : -- : -- : -- : --</div>
        </div>
        <div>
          <div style="font-size: 10px; color: #8b949e;">SCRAMBLED CIPHERTEXT / ROLLING CODE ("RANDOM WORD"):</div>
          <div id="cipher-word-display" style="font-family: 'Consolas', monospace; font-size: 16px; color: #39d353; letter-spacing: 3px; font-weight: bold; background: #0d1117; padding: 6px 8px; border-radius: 4px; margin-top: 2px; border: 1px dashed #30363d;">--------</div>
        </div>
      </div>

      <!-- DEFCON Gauge & AI Threat Classifier Card -->
      <div style="margin-top: 14px; padding: 12px; background: #010409; border: 1px solid #30363d; border-radius: 6px; border-left: 4px solid #39d353;" id="defcon-box">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <span style="font-size: 11px; color: #8b949e; font-weight: bold; letter-spacing: 1px;">AUTOMATED THREAT SIGNATURE CLASSIFIER</span>
          <span id="defcon-badge" style="padding: 2px 7px; border-radius: 4px; background: rgba(35, 134, 54, 0.3); border: 1px solid #238636; color: #39d353; font-size: 10px; font-weight: bold;">DEFCON 5 // SECURE</span>
        </div>
        <div id="ai-threat-title" style="font-size: 13px; font-weight: bold; color: #39d353; margin-bottom: 3px;">
          TARGET PROFILE: AMBIENT SPECTRUM CLEAR
        </div>
        <div id="ai-threat-advisory" style="font-size: 11px; color: #8b949e; line-height: 1.4;">
          All defense sectors quiet. Baseline noise floor within standard operational limits.
        </div>
        <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px;">
          <span style="font-size: 10px; color: #8b949e; width: 120px;">DEFENSE INTEGRITY:</span>
          <div style="flex: 1; height: 6px; background: #161b22; border-radius: 3px; overflow: hidden;">
            <div id="threat-score-bar" style="width: 100%; height: 100%; background: #39d353; transition: width 0.3s, background 0.3s;"></div>
          </div>
          <span id="threat-score-txt" style="font-size: 10px; font-weight: bold; color: #39d353; width: 50px; text-align: right;">100%</span>
        </div>
      </div>

      <div style="margin-top: 10px; font-size: 11px; color: #8b949e; text-align: right;">
        BASE STATION: <span id="base-coord">28.5412° N, 77.2031° E</span>
      </div>
    </div>
  </div>

  <!-- Tactical Geolocation Satellite Map Card -->
  <div class="card" style="margin-top: 20px;">
    <div class="card-title">
      <span>TACTICAL BORDER SATELLITE GEOLOCATION MAP</span>
      <span id="map-status-badge" style="color: #39d353; font-weight: bold;">LIVE GEO-TRACKING // SATELLITE OVERLAY</span>
    </div>
    <div id="map" style="width: 100%; height: 380px; border-radius: 6px; border: 1px solid #30363d;"></div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 11px; color: #8b949e;">
      <div>
        <span style="color: #39d353; font-weight: bold;">● GREEN:</span> BASE STATION DEFENSE PERIMETER | 
        <span style="color: #58a6ff; font-weight: bold;">▲ BLUE CONE:</span> TURRET SWEEP BEAM | 
        <span style="color: #f85149; font-weight: bold;">🎯 RED:</span> HOSTILE EMITTER TARGET
      </div>
      <div>
        MAP ENGINE: Leaflet.js | SATELLITE LAYER: Esri World Imagery &amp; OpenStreetMap
      </div>
    </div>
  </div>

  <!-- Tactical Mission Blackbox & Incident Intelligence Log Card -->
  <div class="card" style="margin-top: 20px;">
    <div class="card-title">
      <div style="display: flex; align-items: center; gap: 8px;">
        <span>TACTICAL MISSION BLACKBOX // INCIDENT INTELLIGENCE LOG</span>
        <span id="log-count-badge" style="background: #1f6feb; color: #fff; padding: 2px 7px; border-radius: 10px; font-size: 10px; font-weight: bold;">0 INCIDENTS</span>
      </div>
      <div>
        <button class="btn" style="background: #238636; padding: 6px 14px; font-size: 11px;" onclick="exportIncidentCSV()">📥 EXPORT MISSION REPORT (CSV)</button>
        <button class="btn" style="background: #21262d; border: 1px solid #30363d; padding: 6px 10px; font-size: 11px; margin-left: 6px;" onclick="clearIncidentLog()">CLEAR LOG</button>
      </div>
    </div>
    <div style="max-height: 220px; overflow-y: auto; border: 1px solid #21262d; border-radius: 6px;">
      <table style="width: 100%; border-collapse: collapse; font-size: 11px; text-align: left;">
        <thead style="background: #161b22; position: sticky; top: 0; color: #8b949e; z-index: 10;">
          <tr>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">UTC TIME</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">THREAT CLASSIFICATION</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">BEARING</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">EST. RANGE</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">RSSI</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">TARGET GPS COORDINATES</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">RAW HEX PAYLOAD</th>
            <th style="padding: 8px; border-bottom: 1px solid #30363d;">CIPHERTEXT</th>
          </tr>
        </thead>
        <tbody id="incident-log-body">
          <tr><td colspan="8" style="padding: 16px; text-align: center; color: #8b949e;">STANDBY // NO INCIDENTS RECORDED IN CURRENT SORTIE</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    const canvas = document.getElementById('radarCanvas');
    const ctx = canvas.getContext('2d');
    let isWifiActive = false;
    let lastAlertTime = 0;
    let lastAlertBearing = 0;
    let lastAlertDist = 50.0;

    // Tactical Web Audio Synthesizer State
    let audioCtx = null;
    let audioEnabled = true;
    let lastSonarPingBearing = 0;
    let lastAlertAudioTime = 0;

    // Mission Blackbox Incident Logging State
    const incidentRecords = [];
    let lastLoggedTime = 0;
    let lastLoggedPayload = "";

    // RF Spectrum Analyzer State
    const specCanvas = document.getElementById('spectrumCanvas');
    const specCtx = specCanvas ? specCanvas.getContext('2d') : null;
    const NUM_BINS = 18;
    const specLevels = new Array(NUM_BINS).fill(0.12);
    const specPeaks = new Array(NUM_BINS).fill(0.12);

    // Leaflet Satellite Map Engine
    let map = null;
    let baseMarker = null;
    let targetMarker = null;
    let sweepBeam = null;
    let targetLine = null;
    let perimeterCircle = null;
    let mapInitialized = false;

    function projectPoint(lat, lon, distMeters, bearingDeg) {
      const rad = bearingDeg * (Math.PI / 180);
      const dLat = (distMeters * Math.cos(rad)) / 111139.0;
      const dLon = (distMeters * Math.sin(rad)) / (111139.0 * Math.cos(lat * (Math.PI / 180)));
      return [lat + dLat, lon + dLon];
    }

    function initMap(lat, lon) {
      if (mapInitialized || typeof L === 'undefined') return;
      try {
        map = L.map('map', { zoomControl: true }).setView([lat, lon], 17);

        // High-resolution Esri World Imagery (True Satellite View)
        const satLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
          attribution: 'Esri Satellite Recon',
          maxZoom: 19
        }).addTo(map);

        // Tactical Street Map (OSM Fallback)
        const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '&copy; OpenStreetMap'
        });

        L.control.layers({ "Satellite View": satLayer, "Tactical Street Map": osmLayer }).addTo(map);

        // Base Station Marker
        const baseIcon = L.divIcon({
          className: 'custom-base-icon',
          html: '<div style="background:#238636; border:2px solid #39d353; width:16px; height:16px; border-radius:50%; box-shadow:0 0 10px #39d353;"></div>',
          iconSize: [16, 16],
          iconAnchor: [8, 8]
        });

        baseMarker = L.marker([lat, lon], { icon: baseIcon }).addTo(map)
          .bindPopup('<b>COMMAND POST // BASE STATION</b><br>Turret Direction Finder Online');

        // Defense Perimeter Circle (100m radius)
        perimeterCircle = L.circle([lat, lon], {
          radius: 120,
          color: '#39d353',
          fillColor: '#238636',
          fillOpacity: 0.12,
          weight: 1.5,
          dashArray: '4, 4'
        }).addTo(map);

        // Target Marker (pulsing red)
        const targetIcon = L.divIcon({
          className: 'custom-target-icon',
          html: '<div style="background:#da3633; border:2px solid #ffffff; width:18px; height:18px; border-radius:50%; box-shadow:0 0 15px #f85149; animation: pulse 1s infinite;"></div>',
          iconSize: [18, 18],
          iconAnchor: [9, 9]
        });
        targetMarker = L.marker([lat, lon], { icon: targetIcon });

        // Vector line from Base to Target
        targetLine = L.polyline([[lat, lon], [lat, lon]], {
          color: '#e3b341',
          weight: 2,
          dashArray: '6, 6'
        });

        mapInitialized = true;
      } catch (err) {
        console.error("Map initialization failed", err);
      }
    }

    function updateMap(bearing, isAlert, baseLat, baseLon, targetLat, targetLon, dist, band) {
      if (!mapInitialized) {
        initMap(baseLat, baseLon);
      }
      if (!map) return;

      // 1. Update Base Marker & Perimeter if GPS drifts
      if (baseMarker) {
        baseMarker.setLatLng([baseLat, baseLon]);
      }
      if (perimeterCircle) {
        perimeterCircle.setLatLng([baseLat, baseLon]);
      }

      // 2. Real-Time Radar Sweep Beam Cone (±8° wide, 140m length)
      const pLeft = projectPoint(baseLat, baseLon, 140, bearing - 8);
      const pRight = projectPoint(baseLat, baseLon, 140, bearing + 8);
      const beamCoords = [[baseLat, baseLon], pLeft, pRight];

      if (!sweepBeam) {
        sweepBeam = L.polygon(beamCoords, {
          color: '#58a6ff',
          fillColor: '#1f6feb',
          fillOpacity: 0.22,
          weight: 1
        }).addTo(map);
      } else {
        sweepBeam.setLatLngs(beamCoords);
      }

      // 3. Target Position & Vector Line
      const showTarget = isAlert || (Date.now() - lastAlertTime < 2800);
      if (showTarget) {
        targetMarker.setLatLng([targetLat, targetLon]);
        targetMarker.bindPopup(`<b>🚨 HOSTILE EMITTER LOCKED</b><br>Band: ${band}<br>Range: ${dist}m<br>Bearing: ${Math.round(bearing)}°`);
        if (!map.hasLayer(targetMarker)) {
          targetMarker.addTo(map);
        }
        targetLine.setLatLngs([[baseLat, baseLon], [targetLat, targetLon]]);
        if (!map.hasLayer(targetLine)) {
          targetLine.addTo(map);
        }
      } else {
        if (map.hasLayer(targetMarker)) map.removeLayer(targetMarker);
        if (map.hasLayer(targetLine)) map.removeLayer(targetLine);
      }
    }

    function drawRadar(bearing, isAlert, dist) {
      const now = Date.now();
      if (isAlert) {
        lastAlertTime = now;
        lastAlertBearing = bearing;
        lastAlertDist = (dist > 0) ? dist : 45.0;
      }

      const showBlip = isAlert || (now - lastAlertTime < 2800);
      const activeBearing = isAlert ? bearing : lastAlertBearing;
      const activeDist = isAlert ? ((dist > 0) ? dist : 45.0) : lastAlertDist;

      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h - 10;
      const maxR = h - 25;

      ctx.clearRect(0, 0, w, h);

      // Semicircle Range Rings
      [0.33, 0.66, 1.0].forEach(r => {
        ctx.beginPath();
        ctx.arc(cx, cy, maxR * r, Math.PI, 2 * Math.PI);
        ctx.strokeStyle = showBlip ? 'rgba(248, 81, 73, 0.4)' : 'rgba(57, 211, 83, 0.25)';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      });

      // Rays (0°, 45°, 90°, 135°, 180°)
      [0, 45, 90, 135, 180].forEach(deg => {
        const rad = (180 - deg) * (Math.PI / 180);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + maxR * Math.cos(rad), cy - maxR * Math.sin(rad));
        ctx.strokeStyle = (deg === 90) ? 'rgba(57, 211, 83, 0.4)' : 'rgba(57, 211, 83, 0.15)';
        ctx.stroke();
      });

      // Needle
      const sweepRad = (180 - bearing) * (Math.PI / 180);
      const nx = cx + maxR * Math.cos(sweepRad);
      const ny = cy - maxR * Math.sin(sweepRad);

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(nx, ny);
      ctx.strokeStyle = showBlip ? '#f85149' : '#39d353';
      ctx.lineWidth = 2.5;
      ctx.shadowColor = showBlip ? '#f85149' : '#39d353';
      ctx.shadowBlur = 8;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Target Blip & Vector
      if (showBlip) {
        const targetRad = (180 - activeBearing) * (Math.PI / 180);
        const normDist = Math.min(Math.max(activeDist / 350.0, 0.25), 0.92);
        const blipR = maxR * normDist;
        const bx = cx + blipR * Math.cos(targetRad);
        const by = cy - blipR * Math.sin(targetRad);

        // Vector line
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(bx, by);
        ctx.strokeStyle = '#e3b341';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 3]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Pulsing Target Lock Outer Ring
        ctx.beginPath();
        ctx.arc(bx, by, 12, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(248, 81, 73, 0.8)';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Target Crosshair
        ctx.beginPath();
        ctx.moveTo(bx - 8, by); ctx.lineTo(bx + 8, by);
        ctx.moveTo(bx, by - 8); ctx.lineTo(bx, by + 8);
        ctx.strokeStyle = '#ffff00';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Glowing Target Core
        ctx.beginPath();
        ctx.arc(bx, by, 6, 0, 2 * Math.PI);
        ctx.fillStyle = '#f85149';
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Target Label
        ctx.fillStyle = '#ff7b72';
        ctx.font = 'bold 11px monospace';
        ctx.fillText(`TARGET [${Math.round(activeBearing)}° | ${Math.round(activeDist)}m]`, bx + 14, by - 6);
      }

      // Center
      ctx.beginPath();
      ctx.arc(cx, cy, 4, 0, 2 * Math.PI);
      ctx.fillStyle = showBlip ? '#f85149' : '#39d353';
      ctx.fill();
    }

    async function fetchTelemetry() {
      try {
        const res = await fetch('/telemetry');
        const data = await res.json();

        document.getElementById('clock').innerText = data.utc;
        document.getElementById('sats').innerText = data.sats;
        document.getElementById('lob-badge').innerText = `BEARING: ${Math.round(data.bearing).toString().padStart(3, '0')}°`;
        document.getElementById('lob-val').innerText = `${Math.round(data.bearing)}°`;
        document.getElementById('band-val').innerText = data.band;
        document.getElementById('rssi-val').innerText = data.rssi + ' dBm';
        document.getElementById('base-coord').innerText = `${data.baseLat.toFixed(4)}° N, ${data.baseLon.toFixed(4)}° E`;

        const banner = document.getElementById('status-banner');
        const threatStat = document.getElementById('threat-stat');
        const distVal = document.getElementById('dist-val');
        const tgtLat = document.getElementById('tgt-lat');
        const tgtLon = document.getElementById('tgt-lon');
        const rawHexElem = document.getElementById('raw-hex-display');
        const cipherElem = document.getElementById('cipher-word-display');
        const cipherBadge = document.getElementById('cipher-type-badge');

        rawHexElem.innerText = data.rawHex;
        cipherElem.innerText = data.ciphertext;
        cipherBadge.innerText = data.cipherType;

        if (data.alert) {
          banner.innerText = '🚨 INTRUDER COMMS INTERCEPTED // TARGET LOCKED';
          banner.className = 'status-banner alert';
          threatStat.innerText = 'HOSTILE TARGET DETECTED';
          threatStat.className = 'val alert';
          distVal.innerText = `${data.dist} METERS`;
          tgtLat.innerText = `${data.targetLat.toFixed(4)}° N`;
          tgtLon.innerText = `${data.targetLon.toFixed(4)}° E`;
          rawHexElem.style.color = '#f85149';
          cipherElem.style.color = '#39d353';
          cipherBadge.style.color = '#ff7b72';
          cipherBadge.style.background = 'rgba(218,54,51,0.2)';
        } else {
          banner.innerText = 'BORDER PERIMETER: SECURE';
          banner.className = 'status-banner safe';
          threatStat.innerText = 'SECURE (PERIMETER PATROL)';
          threatStat.className = 'val safe';
          distVal.innerText = '---';
          tgtLat.innerText = `${data.baseLat.toFixed(4)}° N`;
          tgtLon.innerText = `${data.baseLon.toFixed(4)}° E`;
          rawHexElem.style.color = '#8b949e';
          cipherElem.style.color = '#8b949e';
          cipherBadge.style.color = '#8b949e';
          cipherBadge.style.background = '#21262d';
        }

        // Closed-Loop Wi-Fi Mode UI Sync
        isWifiActive = data.wifiDetection;
        const btn = document.getElementById('toggle-btn');
        const badge = document.getElementById('rf-mode-badge');
        const desc = document.getElementById('switch-desc');
        const box = document.getElementById('switch-box-elem');

        if (isWifiActive) {
          btn.innerText = 'MUTE WI-FI SCAN';
          btn.className = 'btn active';
          badge.innerText = 'ALL BANDS (WI-FI ACTIVE)';
          badge.className = 'badge-mode live';
          desc.innerText = 'STATUS: ACTIVE // Detects Mobile Bursts & 2.4 GHz Intercepts';
          box.style.borderLeftColor = '#da3633';
        } else {
          btn.innerText = 'ENABLE WI-FI SCAN';
          btn.className = 'btn';
          badge.innerText = '433MHz TACTICAL ONLY';
          badge.className = 'badge-mode muted';
          desc.innerText = 'STATUS: MUTED (Filters Home Wi-Fi) // Tactical 433MHz Radios Only';
          box.style.borderLeftColor = '#238636';
        }

        drawRadar(data.bearing, data.alert, data.dist);
        drawSpectrum(data);
        updateAIClassifier(data);
        updateMap(data.bearing, data.alert, data.baseLat, data.baseLon, data.targetLat, data.targetLon, data.dist, data.band);

        // Tactical Audio & Mission Blackbox Integration
        if (data.alert) {
          recordIncident(data);
          playTacticalAlarmKlaxon();
        } else {
          // Sonar Ping on 90° center pass
          if (Math.abs(data.bearing - 90) < 6 && Math.abs(lastSonarPingBearing - 90) >= 6) {
            playSonarPing();
          }
        }
        lastSonarPingBearing = data.bearing;
      } catch (e) {}
    }

    async function trigger433Drill() {
      const btn = document.getElementById('drill-btn');
      btn.disabled = true;
      btn.innerText = 'DRILL ENGAGED!';
      btn.style.background = '#da3633';
      try {
        await fetch('/trigger_433_drill', { method: 'POST' });
        setTimeout(() => {
          btn.innerText = 'TRIGGER 433M DRILL';
          btn.style.background = '#9e6a03';
          btn.disabled = false;
        }, 3500);
      } catch (e) {
        btn.disabled = false;
        btn.innerText = 'TRIGGER 433M DRILL';
        btn.style.background = '#9e6a03';
      }
    }

    async function toggleWifi() {
      const btn = document.getElementById('toggle-btn');
      btn.disabled = true;
      const targetState = isWifiActive ? 'OFF' : 'ON';
      btn.innerText = (targetState === 'ON') ? 'ENABLING...' : 'MUTING...';
      try {
        const res = await fetch(`/toggle_wifi?state=${targetState}`, { method: 'POST' });
        const json = await res.json();
        console.log("Toggle result:", json);
        isWifiActive = (targetState === 'ON');
        setTimeout(() => {
          btn.disabled = false;
        }, 500);
      } catch (e) {
        console.error("Toggle request failed", e);
        btn.disabled = false;
      }
    }

    // Web Audio Synthesizer Engine
    function getAudioContext() {
      if (!audioCtx) {
        const AudioClass = window.AudioContext || window.webkitAudioContext;
        if (AudioClass) audioCtx = new AudioClass();
      }
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
      return audioCtx;
    }

    function toggleTacticalAudio() {
      audioEnabled = !audioEnabled;
      const btn = document.getElementById('audio-toggle-btn');
      if (audioEnabled) {
        btn.innerText = '🔊 TACTICAL AUDIO: ON';
        btn.style.color = '#39d353';
        getAudioContext();
        playSonarPing();
      } else {
        btn.innerText = '🔇 TACTICAL AUDIO: OFF';
        btn.style.color = '#8b949e';
      }
    }

    function playSonarPing() {
      if (!audioEnabled) return;
      try {
        const ctx = getAudioContext();
        if (!ctx) return;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.35);

        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);

        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.36);
      } catch (e) {}
    }

    function playTacticalAlarmKlaxon() {
      if (!audioEnabled) return;
      const now = Date.now();
      if (now - lastAlertAudioTime < 550) return;
      lastAlertAudioTime = now;
      try {
        const ctx = getAudioContext();
        if (!ctx) return;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(750, ctx.currentTime);
        osc.frequency.linearRampToValueAtTime(1100, ctx.currentTime + 0.12);
        osc.frequency.linearRampToValueAtTime(750, ctx.currentTime + 0.24);

        gain.gain.setValueAtTime(0.12, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.28);

        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.29);
      } catch (e) {}
    }

    // Mission Blackbox Logging Engine
    function recordIncident(data) {
      const now = Date.now();
      if (now - lastLoggedTime < 2400 && lastLoggedPayload === data.rawHex) return;
      lastLoggedTime = now;
      lastLoggedPayload = data.rawHex;

      const record = {
        utc: data.utc,
        threat: data.cipherType.replace("STANDBY // SECURE", "UNKNOWN RF BURST"),
        lob: Math.round(data.bearing) + "°",
        range: data.dist + "m",
        rssi: data.rssi + " dBm",
        gps: `${data.targetLat.toFixed(5)}° N, ${data.targetLon.toFixed(5)}° E`,
        hex: data.rawHex,
        cipher: data.ciphertext
      };

      incidentRecords.unshift(record);

      const tbody = document.getElementById('incident-log-body');
      if (incidentRecords.length === 1) {
        tbody.innerHTML = '';
      }

      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid #21262d';
      tr.style.background = 'rgba(218, 54, 51, 0.15)';
      tr.style.transition = 'background 0.8s';
      tr.innerHTML = `
        <td style="padding: 8px; font-weight: bold; color: #58a6ff;">${record.utc}</td>
        <td style="padding: 8px; font-weight: bold; color: #f85149;">${record.threat}</td>
        <td style="padding: 8px; color: #e3b341; font-weight: bold;">${record.lob}</td>
        <td style="padding: 8px;">${record.range}</td>
        <td style="padding: 8px;">${record.rssi}</td>
        <td style="padding: 8px; color: #39d353;">${record.gps}</td>
        <td style="padding: 8px; font-family: monospace; color: #e3b341;">${record.hex}</td>
        <td style="padding: 8px; font-family: monospace; font-weight: bold; color: #39d353;">${record.cipher}</td>
      `;
      tbody.prepend(tr);

      setTimeout(() => {
        tr.style.background = 'transparent';
      }, 1500);

      document.getElementById('log-count-badge').innerText = `${incidentRecords.length} INCIDENT${incidentRecords.length > 1 ? 'S' : ''}`;
    }

    function clearIncidentLog() {
      incidentRecords.length = 0;
      document.getElementById('incident-log-body').innerHTML = `
        <tr><td colspan="8" style="padding: 16px; text-align: center; color: #8b949e;">STANDBY // NO INCIDENTS RECORDED IN CURRENT SORTIE</td></tr>
      `;
      document.getElementById('log-count-badge').innerText = '0 INCIDENTS';
    }

    function exportIncidentCSV() {
      if (incidentRecords.length === 0) {
        alert("No incident records available to export. Trigger a threat lock first!");
        return;
      }
      let csv = "UTC Time,Threat Classification,Line-of-Bearing (Azimuth),Estimated Range,Signal Strength (RSSI),Target GPS Coordinates,Raw Hex Payload,Decoded Ciphertext\\n";
      incidentRecords.forEach(r => {
        csv += `"${r.utc}","${r.threat}","${r.lob}","${r.range}","${r.rssi}","${r.gps}","${r.hex}","${r.cipher}"\\n`;
      });

      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      link.setAttribute("href", url);
      link.setAttribute("download", `TACTICAL_SIGINT_MISSION_REPORT_${dateStr}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }

    // Real-Time RF Spectrum Analyzer Engine
    function drawSpectrum(data) {
      if (!specCtx) return;
      const w = specCanvas.width;
      const h = specCanvas.height;
      specCtx.clearRect(0, 0, w, h);

      // Background subtle reference lines
      specCtx.strokeStyle = 'rgba(33, 38, 45, 0.6)';
      specCtx.lineWidth = 1;
      [0.25, 0.5, 0.75].forEach(r => {
        specCtx.beginPath();
        specCtx.moveTo(0, h * r);
        specCtx.lineTo(w, h * r);
        specCtx.stroke();
      });

      const isAlert = data.alert || (Date.now() - lastAlertTime < 2800);
      const isWifiAlert = isAlert && data.band.includes('2.4GHz');
      const is433Alert = isAlert && data.band.includes('433MHz');

      const badge = document.getElementById('spectrum-peak-badge');
      if (isWifiAlert) {
        badge.innerText = `PEAK BURST: ${data.rssi} dBm (CH 6 / 2.437 GHz)`;
        badge.style.color = '#f85149';
      } else if (is433Alert) {
        badge.innerText = `PEAK BURST: ${data.rssi} dBm (433.92 MHz CARRIER)`;
        badge.style.color = '#d29922';
      } else {
        badge.innerText = 'NOISE FLOOR: -112 dBm';
        badge.style.color = '#39d353';
      }

      const barW = (w / NUM_BINS) - 3;
      for (let i = 0; i < NUM_BINS; i++) {
        let target = 0.08 + Math.random() * 0.08;
        if (is433Alert && (i === 0 || i === 1)) {
          target = 0.88 + Math.random() * 0.10;
        } else if (isWifiAlert) {
          if (i >= 6 && i <= 8) {
            target = 0.85 + Math.random() * 0.12;
          } else if (i === 2 || i === 3 || i === 12 || i === 13) {
            target = 0.40 + Math.random() * 0.15;
          } else if (i >= 15) {
            target = 0.65 + Math.random() * 0.15;
          }
        }

        specLevels[i] += (target - specLevels[i]) * 0.28;
        if (specLevels[i] > specPeaks[i]) {
          specPeaks[i] = specLevels[i];
        } else {
          specPeaks[i] -= 0.012;
        }

        const barH = specLevels[i] * (h - 10);
        const x = i * (barW + 3) + 2;
        const y = h - barH - 4;

        const grad = specCtx.createLinearGradient(0, y, 0, h);
        if (specLevels[i] > 0.6) {
          grad.addColorStop(0, '#f85149');
          grad.addColorStop(0.5, '#e3b341');
          grad.addColorStop(1, '#238636');
        } else if (specLevels[i] > 0.3) {
          grad.addColorStop(0, '#e3b341');
          grad.addColorStop(1, '#238636');
        } else {
          grad.addColorStop(0, '#39d353');
          grad.addColorStop(1, 'rgba(35, 134, 54, 0.2)');
        }

        specCtx.fillStyle = grad;
        specCtx.fillRect(x, y, barW, barH);

        const peakY = h - (specPeaks[i] * (h - 10)) - 4;
        specCtx.fillStyle = '#ffffff';
        specCtx.fillRect(x, Math.max(peakY, 2), barW, 2);
      }
    }

    // AI Threat Classifier Engine (Reversed Defense Integrity Metrics)
    function updateAIClassifier(data) {
      const isAlert = data.alert || (Date.now() - lastAlertTime < 2800);
      const isWifi = isAlert && data.band.includes('2.4GHz');
      const is433 = isAlert && data.band.includes('433MHz');

      const defconBox = document.getElementById('defcon-box');
      const defconBadge = document.getElementById('defcon-badge');
      const title = document.getElementById('ai-threat-title');
      const advisory = document.getElementById('ai-threat-advisory');
      const bar = document.getElementById('threat-score-bar');
      const txt = document.getElementById('threat-score-txt');

      if (!defconBox || !defconBadge) return;

      if (isWifi) {
        defconBox.style.borderLeftColor = '#da3633';
        defconBadge.innerText = 'DEFCON 2 // BORDER INTRUSION';
        defconBadge.style.background = 'rgba(218, 54, 51, 0.3)';
        defconBadge.style.borderColor = '#da3633';
        defconBadge.style.color = '#f85149';
        title.innerText = '⚡ [AI 97.4% THREAT MATCH] COVERT 2.4 GHz EMITTER BURST';
        title.style.color = '#f85149';
        advisory.innerText = `Hostile radio emitter active within ${data.dist}m perimeter. Defense integrity compromised. Intercepting raw WPA2 frame.`;
        bar.style.width = '4%';
        bar.style.background = '#da3633';
        txt.innerText = '4%';
        txt.style.color = '#f85149';
      } else if (is433) {
        defconBox.style.borderLeftColor = '#d29922';
        defconBadge.innerText = 'DEFCON 2 // COVERT TRANSMISSION';
        defconBadge.style.background = 'rgba(210, 153, 34, 0.3)';
        defconBadge.style.borderColor = '#d29922';
        defconBadge.style.color = '#e3b341';
        title.innerText = '⚡ [AI 99.1% THREAT MATCH] 433 MHz TACTICAL ROLLING-CODE TRANSMITTER';
        title.style.color = '#e3b341';
        advisory.innerText = `433.92 MHz sub-GHz transmission locked at ${Math.round(data.bearing)}°. Defense integrity compromised. Decrypting KeeLoq counter.`;
        bar.style.width = '2%';
        bar.style.background = '#d29922';
        txt.innerText = '2%';
        txt.style.color = '#d29922';
      } else {
        defconBox.style.borderLeftColor = '#39d353';
        defconBadge.innerText = 'DEFCON 5 // 100% SECURE';
        defconBadge.style.background = 'rgba(35, 134, 54, 0.3)';
        defconBadge.style.borderColor = '#238636';
        defconBadge.style.color = '#39d353';
        title.innerText = 'BORDER DEFENSE INTEGRITY: 100% SECURE';
        title.style.color = '#39d353';
        advisory.innerText = 'All defense sectors clear. Baseline noise floor within standard operational limits. Zero border incursions detected.';
        bar.style.width = '100%';
        bar.style.background = '#39d353';
        txt.innerText = '100%';
        txt.style.color = '#39d353';
      }
    }

    setInterval(fetchTelemetry, 100);
    drawRadar(0, false, 0);
  </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        elif self.path == '/telemetry':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(telemetry_data).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global wifi_override_until
        if self.path.startswith('/toggle_wifi'):
            query = self.path.split('?')[-1]
            state = "ON" if "state=ON" in query else "OFF"
            is_on = (state == "ON")
            telemetry_data["wifiDetection"] = is_on
            wifi_override_until = time.time() + 3.0
            cmd = "WIFI_ON" if is_on else "WIFI_OFF"
            success = send_com3_cmd(cmd)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "wifi": state}).encode('utf-8'))
        elif self.path.startswith('/trigger_433_drill'):
            success = send_com3_cmd("TEST_433\n")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "drill": "433M"}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return # suppress console spam

def start_server():
    t3 = threading.Thread(target=com3_worker, daemon=True)
    t3.start()

    t4 = threading.Thread(target=com4_reader, daemon=True)
    t4.start()

    server_address = ('0.0.0.0', 5000)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    print("\n=======================================================", flush=True)
    print(">>> TACTICAL BORDER COMMAND DASHBOARD RUNNING <<<", flush=True)
    print(">>> URL: http://localhost:5000 or http://127.0.0.1:5000 <<<", flush=True)
    print("=======================================================\n", flush=True)
    httpd.serve_forever()

if __name__ == '__main__':
    start_server()
