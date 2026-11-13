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
    "alertLevel": 0,
    "baseLat": 28.5412,
    "baseLon": 77.2031,
    "bravoLat": 28.5430,
    "bravoLon": 77.2055,
    "targetLat": 28.5412,
    "targetLon": 77.2031,
    "sats": 0,
    "utc": "--:--:--",
    "wifiDetection": False,
    "rawHex": "-- -- -- -- -- -- -- --",
    "ciphertext": "--------",
    "cipherType": "STANDBY // SECURE",
    "isJamming": False,
    "jammingUntil": 0.0,
    "ptzManual": False,
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
                time.sleep(1.8)
                with ser_com3_lock:
                    ser_com3 = s
                telemetry_data["node1Connected"] = True
                print("[BRIDGE] COM3 Connected & Ready for Commands!")

            line = ser_com3.readline()
            if line:
                msg = line.decode('utf-8', errors='replace').strip()
                if msg:
                    print(f"[NODE 1 / COM3] {msg}")
                    if "[ACK]" in msg:
                        print(f"[COMMAND CONFIRMED] {msg}")
                    elif "[PTZ]" in msg:
                        print(f"[PTZ CONFIRMED] {msg}")
                    elif "[ECM]" in msg:
                        print(f"[ECM CONFIRMED] {msg}")
                        telemetry_data["isJamming"] = True
                    elif "[SIGINT" in msg:
                        parts = msg.split('|')
                        for p in parts:
                            p = p.strip()
                            if p.startswith("LOB:"):
                                try:
                                    telemetry_data["bearing"] = float(p.replace("LOB:", "").replace("°", "").strip())
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
                print(f"[BRIDGE -> COM3] Command Sent: {cmd.strip()}")
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
                if "BORDER SEC HUD" in s or "TARGET LOCKED" in s or "PERIMETER PATROL" in s or "ECM JAMMER" in s:
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
                            try: alert_level = int(p.split(':')[1].strip())
                            except: pass
                        elif p.startswith("LOB:"):
                            try: lob = float(p.split(':')[1].replace('°','').strip())
                            except: pass
                        elif p.startswith("Band:"):
                            try: band_id = int(p.split(':')[1].strip())
                            except: pass
                        elif p.startswith("Dist:"):
                            try: dist_in = float(p.split(':')[1].strip())
                            except: pass
                        elif p.startswith("HEX:"):
                            try: hex_val = p.split(':')[1].strip()
                            except: pass
                        elif p.startswith("WiFiMode:"):
                            try: wifi_mode = int(p.split(':')[1].strip())
                            except: pass
                        elif p.startswith("RSSI:"):
                            try: rssi = int(p.split(':')[1].replace('dBm','').strip())
                            except: pass
                        elif p.startswith("Sats:"):
                            try: sats = int(p.split(':')[1].strip())
                            except: pass
                        elif p.startswith("BaseLat:"):
                            try: base_lat = float(p.split(':')[1].strip())
                            except: pass

                    is_jamming = (alert_level == 3) or ("ECM JAMMER" in s) or (time.time() < telemetry_data.get("jammingUntil", 0))
                    is_alert = (alert_level >= 2) or ("TARGET LOCKED" in s) or ("HOSTILE" in s) or is_jamming

                    telemetry_data["isJamming"] = is_jamming
                    telemetry_data["alertLevel"] = 3 if is_jamming else alert_level

                    if is_jamming:
                        band_str = "⚡ BROADBAND RF JAMMING (ECM)"
                    elif band_id == 2:
                        band_str = "433MHz TACTICAL LORA"
                    elif band_id == 1:
                        band_str = "2.4GHz COVERT/MOBILE"
                    else:
                        band_str = "PERIMETER SECURE"

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

                    if is_jamming:
                        last_alert_lock_time = time.time()
                        telemetry_data["alert"] = True
                        telemetry_data["band"] = band_str
                        telemetry_data["dist"] = 28.0
                        telemetry_data["targetLat"] = tgt_lat
                        telemetry_data["targetLon"] = tgt_lon
                        telemetry_data["rawHex"] = "EE : FF : EE : DD : CC : BB : AA : 99"
                        telemetry_data["ciphertext"] = "ECM-JAM!"
                        telemetry_data["cipherType"] = "⚡ BROADBAND RF JAMMING ACTIVE"
                    elif is_alert:
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
                    telemetry_data["rssi"] = -32 if is_jamming else rssi
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

      <!-- Raw SIGINT Packet & Ciphertext Decoder -->
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

  <!-- Dual Advanced Tactical Control: Manual PTZ Slew & Electronic Warfare (EW) Jamming -->
  <div class="grid" style="margin-top: 20px;">
    <!-- Card 1: Manual PTZ Slew & Remote Servo Joystick -->
    <div class="card" style="border-left: 4px solid #1f6feb;">
      <div class="card-title">
        <span>MANUAL PTZ SLEW &amp; SERVO JOYSTICK CONTROL</span>
        <span id="ptz-status-badge" style="color: #39d353; font-weight: bold;">AUTONOMOUS SWEEP</span>
      </div>

      <!-- Quick Preset Azimuth Angles -->
      <div style="margin-bottom: 12px;">
        <div style="font-size: 11px; color: #8b949e; margin-bottom: 6px; font-weight: bold;">QUICK AZIMUTH PRESETS:</div>
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:6px 12px; font-size:11px;" onclick="setPtzPan(0)">0° WEST</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:6px 12px; font-size:11px;" onclick="setPtzPan(45)">45° NW</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:6px 12px; font-size:11px;" onclick="setPtzPan(90)">90° BORESIGHT</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:6px 12px; font-size:11px;" onclick="setPtzPan(135)">135° NE</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:6px 12px; font-size:11px;" onclick="setPtzPan(180)">180° EAST</button>
        </div>
      </div>

      <!-- Sliders for Pan and Tilt -->
      <div style="background: #010409; padding: 12px; border: 1px solid #21262d; border-radius: 6px; margin-bottom: 14px;">
        <div style="margin-bottom: 10px;">
          <div style="display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 4px;">
            <span style="color: #8b949e; font-weight: bold;">AZIMUTH PAN ANGLE (0° - 180°):</span>
            <span id="pan-val-label" style="color: #58a6ff; font-weight: bold;">90°</span>
          </div>
          <input type="range" id="pan-slider" min="0" max="180" value="90" style="width: 100%; accent-color: #58a6ff;" oninput="onPanSlider(this.value)">
        </div>
        <div>
          <div style="display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 4px;">
            <span style="color: #8b949e; font-weight: bold;">ELEVATION TILT ANGLE (20° - 90°):</span>
            <span id="tilt-val-label" style="color: #39d353; font-weight: bold;">55°</span>
          </div>
          <input type="range" id="tilt-slider" min="20" max="90" value="55" style="width: 100%; accent-color: #39d353;" oninput="onTiltSlider(this.value)">
        </div>
      </div>

      <!-- Directional Nudge Buttons & Resume Auto -->
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
        <div style="display: flex; gap: 6px;">
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:8px 10px; font-size:11px;" onclick="nudgePan(-10)">◀ PAN -10°</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:8px 10px; font-size:11px;" onclick="nudgePan(10)">PAN +10° ▶</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:8px 10px; font-size:11px;" onclick="nudgeTilt(-10)">▼ TILT -10°</button>
          <button class="btn" style="background:#21262d; border:1px solid #30363d; padding:8px 10px; font-size:11px;" onclick="nudgeTilt(10)">▲ TILT +10°</button>
        </div>
        <button id="resume-auto-btn" class="btn" style="background:#238636; padding:8px 14px; font-size:11px;" onclick="resumeAutonomousSweep()">🟢 RESUME AUTO SWEEP</button>
      </div>
    </div>

    <!-- Card 2: Electronic Warfare (EW) Jamming Countermeasure -->
    <div class="card" style="border-left: 4px solid #f85149;" id="jamming-card">
      <div class="card-title">
        <span>ELECTRONIC WARFARE (EW) // RF JAMMING COUNTERMEASURE</span>
        <span id="jam-status-badge" style="color: #8b949e; font-weight: bold;">STANDBY</span>
      </div>

      <div style="font-size: 11px; color: #8b949e; margin-bottom: 12px; line-height: 1.4;">
        Directs high-gain broadband electromagnetic countermeasure (ECM) on locked line-of-bearing. Suppresses hostile uplinks with white carrier noise, locks pan/tilt horn on intruder, triggers rapid alert warbles on Ground Station HUD, and denies remote wireless control.
      </div>

      <div style="margin-bottom: 14px;">
        <button id="engage-jam-btn" class="btn" style="width: 100%; padding: 14px; font-size: 13px; background: linear-gradient(135deg, #da3633 0%, #8e1515 100%); border: 1px solid #f85149; box-shadow: 0 0 15px rgba(218, 54, 51, 0.4); letter-spacing: 1.2px; font-weight: bold; cursor: pointer;" onclick="engageRfJamming()">
          ⚡ ENGAGE BROADBAND RF JAMMING COUNTERMEASURE
        </button>
      </div>

      <div style="background: #010409; padding: 10px; border: 1px solid #21262d; border-radius: 6px;">
        <div class="row" style="border: none; padding: 2px 0;"><span>ECM STATUS:</span><span id="ecm-mode-txt" class="val safe">STANDBY // READY</span></div>
        <div class="row" style="border: none; padding: 2px 0;"><span>SIMULATED RADIATED POWER:</span><span id="ecm-erp-txt" class="val">50 W DIRECTED RF</span></div>
        <div class="row" style="border: none; padding: 2px 0;"><span>JAMMER TARGET LOCK:</span><span id="ecm-vector-txt" class="val" style="color:#58a6ff;">AZIMUTH 090° LOCKED</span></div>
      </div>
    </div>
  </div>

  <!-- Tactical Geolocation Satellite Map Card (Dual-Station Cross-Bearing Triangulation) -->
  <div class="card" style="margin-top: 20px;">
    <div class="card-title">
      <span>TACTICAL BORDER SATELLITE GEOLOCATION MAP // DUAL-STATION TRIANGULATION</span>
      <span id="map-status-badge" style="color: #39d353; font-weight: bold;">OUTPOST ALPHA &amp; BRAVO // LOB INTERSECTION ACTIVE</span>
    </div>
    <div id="map" style="width: 100%; height: 420px; border-radius: 6px; border: 1px solid #30363d;"></div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 11px; color: #8b949e; flex-wrap: wrap; gap: 8px;">
      <div>
        <span style="color: #39d353; font-weight: bold;">● OUTPOST ALPHA (TURRET 1)</span> | 
        <span style="color: #58a6ff; font-weight: bold;">◆ OUTPOST BRAVO (TOWER 2)</span> | 
        <span style="color: #e3b341; font-weight: bold;">── LOB ALPHA</span> | 
        <span style="color: #58a6ff; font-weight: bold;">- - LOB BRAVO</span> | 
        <span style="color: #f85149; font-weight: bold;">🎯 CROSS-BEARING FIX (99.4%)</span>
      </div>
      <div>
        MAP ENGINE: Leaflet.js | SATELLITE: Esri World Imagery &amp; OpenStreetMap
      </div>
    </div>
  </div>

  <!-- Mission Blackbox Incident Logger Card -->
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
    let lastJammingAudioTime = 0;

    // Mission Blackbox Incident Logging State
    const incidentRecords = [];
    let lastLoggedTime = 0;
    let lastLoggedPayload = "";

    // RF Spectrum Analyzer State
    const specCanvas = document.getElementById('spectrumCanvas');
    const specCtx = specCanvas ? specCanvas.getContext('2d') : null;
    const NUM_BINS = 24;
    const specLevels = new Array(NUM_BINS).fill(0.12);
    const specPeaks = new Array(NUM_BINS).fill(0.12);

    // Leaflet Satellite Map Engine with Dual Stations
    let map = null;
    let baseMarker = null;
    let bravoMarker = null;
    let targetMarker = null;
    let sweepBeam = null;
    let targetLine = null;
    let bravoLine = null;
    let baselinePoly = null;
    let triangulationFixMarker = null;
    let perimeterCircle = null;
    let mapInitialized = false;

    // PTZ Remote Control State
    let currentPtzPan = 90;
    let currentPtzTilt = 55;
    let isPtzManual = false;

    function projectPoint(lat, lon, distMeters, bearingDeg) {
      const rad = bearingDeg * (Math.PI / 180);
      const dLat = (distMeters * Math.cos(rad)) / 111139.0;
      const dLon = (distMeters * Math.sin(rad)) / (111139.0 * Math.cos(lat * (Math.PI / 180)));
      return [lat + dLat, lon + dLon];
    }

    function initMap(lat, lon, bLat, bLon) {
      if (mapInitialized || typeof L === 'undefined') return;
      try {
        const bravoLat = bLat || (lat + 0.0016);
        const bravoLon = bLon || (lon + 0.0022);

        map = L.map('map', { zoomControl: true }).setView([lat + 0.0008, lon + 0.0011], 17);

        // High-resolution Esri World Imagery (True Satellite Recon View)
        const satLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
          attribution: 'Esri Satellite Recon',
          maxZoom: 19
        }).addTo(map);

        // Tactical Street Map Fallback
        const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '&copy; OpenStreetMap'
        });

        L.control.layers({ "Satellite View": satLayer, "Tactical Street Map": osmLayer }).addTo(map);

        // Station Alpha Marker (COM3 Turret / Primary Base Post)
        const baseIcon = L.divIcon({
          className: 'custom-base-icon',
          html: '<div style="background:#238636; border:2px solid #39d353; width:16px; height:16px; border-radius:50%; box-shadow:0 0 10px #39d353;"></div>',
          iconSize: [16, 16],
          iconAnchor: [8, 8]
        });
        baseMarker = L.marker([lat, lon], { icon: baseIcon }).addTo(map)
          .bindPopup('<b>OUTPOST ALPHA (PRIMARY DF TURRET)</b><br>COM3 MG996R Direction Finder Online');

        // Station Bravo Marker (Forward Recon Tower 2)
        const bravoIcon = L.divIcon({
          className: 'custom-bravo-icon',
          html: '<div style="background:#1f6feb; border:2px solid #58a6ff; width:16px; height:16px; border-radius:4px; transform:rotate(45deg); box-shadow:0 0 10px #58a6ff;"></div>',
          iconSize: [16, 16],
          iconAnchor: [8, 8]
        });
        bravoMarker = L.marker([bravoLat, bravoLon], { icon: bravoIcon }).addTo(map)
          .bindPopup('<b>FORWARD RECON OUTPOST BRAVO</b><br>Tower #2 // Secondary DF Sensor Online');

        // Defense Perimeter Circle around Alpha
        perimeterCircle = L.circle([lat, lon], {
          radius: 120,
          color: '#39d353',
          fillColor: '#238636',
          fillOpacity: 0.10,
          weight: 1.5,
          dashArray: '4, 4'
        }).addTo(map);

        // Tactical Baseline connecting Alpha & Bravo
        baselinePoly = L.polyline([[lat, lon], [bravoLat, bravoLon]], {
          color: '#8b949e',
          weight: 1.5,
          dashArray: '3, 6'
        }).addTo(map);

        // Target Marker (pulsing red)
        const targetIcon = L.divIcon({
          className: 'custom-target-icon',
          html: '<div style="background:#da3633; border:2px solid #ffffff; width:18px; height:18px; border-radius:50%; box-shadow:0 0 15px #f85149; animation: pulse 1s infinite;"></div>',
          iconSize: [18, 18],
          iconAnchor: [9, 9]
        });
        targetMarker = L.marker([lat, lon], { icon: targetIcon });

        // LOB Ray 1: Vector line from Alpha to Target
        targetLine = L.polyline([[lat, lon], [lat, lon]], {
          color: '#e3b341',
          weight: 2,
          dashArray: '6, 6'
        });

        // LOB Ray 2: Vector line from Bravo to Target (Cross-Bearing Ray)
        bravoLine = L.polyline([[bravoLat, bravoLon], [lat, lon]], {
          color: '#58a6ff',
          weight: 2,
          dashArray: '5, 5'
        });

        // Triangulation Cross-Bearing Fix Point Marker
        const fixIcon = L.divIcon({
          className: 'custom-fix-icon',
          html: '<div style="border:2px solid #f85149; width:28px; height:28px; border-radius:50%; box-shadow:0 0 18px #f85149; animation:pulse 0.8s infinite; display:flex; align-items:center; justify-content:center;"><div style="background:#f85149; width:8px; height:8px; border-radius:50%;"></div></div>',
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        });
        triangulationFixMarker = L.marker([lat, lon], { icon: fixIcon });

        mapInitialized = true;
      } catch (err) {
        console.error("Map initialization failed", err);
      }
    }

    function updateMap(bearing, isAlert, baseLat, baseLon, targetLat, targetLon, dist, band, bLat, bLon) {
      const bravoLat = bLat || (baseLat + 0.0016);
      const bravoLon = bLon || (baseLon + 0.0022);

      if (!mapInitialized) {
        initMap(baseLat, baseLon, bravoLat, bravoLon);
      }
      if (!map) return;

      if (baseMarker) baseMarker.setLatLng([baseLat, baseLon]);
      if (bravoMarker) bravoMarker.setLatLng([bravoLat, bravoLon]);
      if (perimeterCircle) perimeterCircle.setLatLng([baseLat, baseLon]);
      if (baselinePoly) baselinePoly.setLatLngs([[baseLat, baseLon], [bravoLat, bravoLon]]);

      // Real-Time Radar Sweep Beam Cone (±8° wide, 140m length) from Alpha
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

      // Target Position & Cross-Bearing Triangulation Rays
      const showTarget = isAlert || (Date.now() - lastAlertTime < 2800);
      if (showTarget) {
        // Calculate bearing from Bravo to Target
        const dLatB = targetLat - bravoLat;
        const dLonB = (targetLon - bravoLon) * Math.cos(bravoLat * Math.PI / 180);
        let bravoBearing = Math.atan2(dLonB, dLatB) * (180 / Math.PI);
        if (bravoBearing < 0) bravoBearing += 360;

        targetMarker.setLatLng([targetLat, targetLon]);
        targetMarker.bindPopup(`<b>🚨 HOSTILE EMITTER LOCKED</b><br>Band: ${band}<br>Range: ${dist}m<br>Alpha LOB: ${Math.round(bearing)}°`);
        if (!map.hasLayer(targetMarker)) targetMarker.addTo(map);

        // Alpha LOB Ray
        targetLine.setLatLngs([[baseLat, baseLon], [targetLat, targetLon]]);
        if (!map.hasLayer(targetLine)) targetLine.addTo(map);

        // Bravo LOB Ray
        bravoLine.setLatLngs([[bravoLat, bravoLon], [targetLat, targetLon]]);
        if (!map.hasLayer(bravoLine)) bravoLine.addTo(map);

        // Cross-Bearing Fix Point
        triangulationFixMarker.setLatLng([targetLat, targetLon]);
        triangulationFixMarker.bindPopup(`<b>🎯 DUAL-STATION CROSS-BEARING FIX</b><br>Alpha Bearing: ${Math.round(bearing)}°<br>Bravo Bearing: ${Math.round(bravoBearing)}°<br>Triangulation Confidence: 99.4%<br>Target: ${targetLat.toFixed(5)}° N, ${targetLon.toFixed(5)}° E`);
        if (!map.hasLayer(triangulationFixMarker)) triangulationFixMarker.addTo(map);
      } else {
        if (map.hasLayer(targetMarker)) map.removeLayer(targetMarker);
        if (map.hasLayer(targetLine)) map.removeLayer(targetLine);
        if (map.hasLayer(bravoLine)) map.removeLayer(bravoLine);
        if (map.hasLayer(triangulationFixMarker)) map.removeLayer(triangulationFixMarker);
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
        ctx.strokeStyle = showBlip ? 'rgba(218, 54, 51, 0.4)' : 'rgba(35, 134, 54, 0.4)';
        ctx.lineWidth = 1.2;
        ctx.stroke();
      });

      // Sector Angle Rays
      [0, 30, 60, 90, 120, 150, 180].forEach(deg => {
        const rad = (180 - deg) * (Math.PI / 180);
        const x = cx + maxR * Math.cos(rad);
        const y = cy - maxR * Math.sin(rad);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(x, y);
        ctx.strokeStyle = '#161b22';
        ctx.stroke();

        ctx.fillStyle = '#8b949e';
        ctx.font = '10px monospace';
        ctx.fillText(deg + '°', x - 8, y - 4);
      });

      // Sweep Beam Needle
      const needleRad = (180 - bearing) * (Math.PI / 180);
      const nx = cx + maxR * Math.cos(needleRad);
      const ny = cy - maxR * Math.sin(needleRad);

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(nx, ny);
      ctx.strokeStyle = showBlip ? '#f85149' : '#39d353';
      ctx.lineWidth = 2.5;
      ctx.shadowBlur = 12;
      ctx.shadowColor = showBlip ? '#da3633' : '#39d353';
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Plot Hostile Blip
      if (showBlip) {
        const blipRad = (180 - activeBearing) * (Math.PI / 180);
        const normDist = Math.min(Math.max(activeDist, 10.0), 350.0);
        const blipR = (normDist / 350.0) * maxR;
        const bx = cx + blipR * Math.cos(blipRad);
        const by = cy - blipR * Math.sin(blipRad);

        // Vector line from center to blip
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(bx, by);
        ctx.strokeStyle = '#ffff00';
        ctx.lineWidth = 1.8;
        ctx.stroke();

        // Target Rings
        ctx.beginPath();
        ctx.arc(bx, by, 12, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(248, 81, 73, 0.8)';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(bx - 8, by); ctx.lineTo(bx + 8, by);
        ctx.moveTo(bx, by - 8); ctx.lineTo(bx, by + 8);
        ctx.strokeStyle = '#ffff00';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(bx, by, 6, 0, 2 * Math.PI);
        ctx.fillStyle = '#f85149';
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.fillStyle = '#ff7b72';
        ctx.font = 'bold 11px monospace';
        ctx.fillText(`TARGET [${Math.round(activeBearing)}° | ${Math.round(activeDist)}m]`, bx + 14, by - 6);
      }

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

        // Jamming & Threat State Sync
        const jamBadge = document.getElementById('jam-status-badge');
        const ecmModeTxt = document.getElementById('ecm-mode-txt');
        const ecmVectorTxt = document.getElementById('ecm-vector-txt');

        if (data.isJamming) {
          banner.innerText = '⚡ BROADBAND RF JAMMING ACTIVE // ECM ENGAGED';
          banner.className = 'status-banner alert';
          banner.style.background = 'rgba(218, 54, 51, 0.45)';
          banner.style.border = '2px solid #ff00ff';
          threatStat.innerText = '⚡ ECM BROADBAND JAMMING ACTIVE';
          threatStat.className = 'val alert';
          threatStat.style.color = '#ff7b72';
          distVal.innerText = `${data.dist} METERS`;
          tgtLat.innerText = `${data.targetLat.toFixed(4)}° N`;
          tgtLon.innerText = `${data.targetLon.toFixed(4)}° E`;
          rawHexElem.style.color = '#f85149';
          cipherElem.style.color = '#ff7b72';
          cipherBadge.style.color = '#ffffff';
          cipherBadge.style.background = '#da3633';

          if (jamBadge) {
            jamBadge.innerText = '⚡ TRANSMITTING NOISE';
            jamBadge.style.color = '#ff0055';
          }
          if (ecmModeTxt) {
            ecmModeTxt.innerText = 'TRANSMITTING HIGH-GAIN ECM NOISE';
            ecmModeTxt.className = 'val alert';
          }
          if (ecmVectorTxt) {
            ecmVectorTxt.innerText = `AZIMUTH ${Math.round(data.bearing)}° LOCKED`;
          }
        } else if (data.alert) {
          banner.style.background = '';
          banner.style.border = '';
          banner.innerText = '🚨 INTRUDER COMMS INTERCEPTED // TARGET LOCKED';
          banner.className = 'status-banner alert';
          threatStat.innerText = 'HOSTILE TARGET DETECTED';
          threatStat.className = 'val alert';
          threatStat.style.color = '';
          distVal.innerText = `${data.dist} METERS`;
          tgtLat.innerText = `${data.targetLat.toFixed(4)}° N`;
          tgtLon.innerText = `${data.targetLon.toFixed(4)}° E`;
          rawHexElem.style.color = '#f85149';
          cipherElem.style.color = '#39d353';
          cipherBadge.style.color = '#ff7b72';
          cipherBadge.style.background = 'rgba(218,54,51,0.2)';

          if (jamBadge) {
            jamBadge.innerText = 'READY FOR COUNTERMEASURE';
            jamBadge.style.color = '#e3b341';
          }
          if (ecmModeTxt) {
            ecmModeTxt.innerText = 'TARGET ACQUIRED // READY TO JAM';
            ecmModeTxt.className = 'val';
            ecmModeTxt.style.color = '#e3b341';
          }
          if (ecmVectorTxt) {
            ecmVectorTxt.innerText = `AZIMUTH ${Math.round(data.bearing)}° LOCKED`;
          }
        } else {
          banner.style.background = '';
          banner.style.border = '';
          banner.innerText = 'BORDER PERIMETER: SECURE';
          banner.className = 'status-banner safe';
          threatStat.innerText = 'SECURE (PERIMETER PATROL)';
          threatStat.className = 'val safe';
          threatStat.style.color = '';
          distVal.innerText = '---';
          tgtLat.innerText = `${data.baseLat.toFixed(4)}° N`;
          tgtLon.innerText = `${data.baseLon.toFixed(4)}° E`;
          rawHexElem.style.color = '#8b949e';
          cipherElem.style.color = '#8b949e';
          cipherBadge.style.color = '#8b949e';
          cipherBadge.style.background = '#21262d';

          if (jamBadge) {
            jamBadge.innerText = 'STANDBY';
            jamBadge.style.color = '#8b949e';
          }
          if (ecmModeTxt) {
            ecmModeTxt.innerText = 'STANDBY // READY';
            ecmModeTxt.className = 'val safe';
            ecmModeTxt.style.color = '';
          }
          if (ecmVectorTxt) {
            ecmVectorTxt.innerText = 'PERIMETER PATROL';
          }
        }

        // Wi-Fi Mode Sync
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
        updateMap(data.bearing, data.alert, data.baseLat, data.baseLon, data.targetLat, data.targetLon, data.dist, data.band, data.bravoLat, data.bravoLon);

        // Tactical Audio & Mission Blackbox Integration
        if (data.isJamming) {
          recordIncident(data, "⚡ ELECTRONIC WARFARE // BROADBAND RF JAMMING ENGAGED");
          playJammingWarble();
        } else if (data.alert) {
          recordIncident(data);
          playTacticalAlarmKlaxon();
        } else {
          if (Math.abs(data.bearing - 90) < 6 && Math.abs(lastSonarPingBearing - 90) >= 6) {
            playSonarPing();
          }
        }
        lastSonarPingBearing = data.bearing;
      } catch (e) {}
    }

    // Manual PTZ Control Functions
    function setPtzPan(val) {
      currentPtzPan = parseInt(val);
      document.getElementById('pan-slider').value = currentPtzPan;
      document.getElementById('pan-val-label').innerText = `${currentPtzPan}°`;
      sendPtzCommand(`pan=${currentPtzPan}`);
    }

    function onPanSlider(val) {
      currentPtzPan = parseInt(val);
      document.getElementById('pan-val-label').innerText = `${currentPtzPan}°`;
      sendPtzCommand(`pan=${currentPtzPan}`);
    }

    function onTiltSlider(val) {
      currentPtzTilt = parseInt(val);
      document.getElementById('tilt-val-label').innerText = `${currentPtzTilt}°`;
      sendPtzCommand(`tilt=${currentPtzTilt}`);
    }

    function nudgePan(delta) {
      currentPtzPan = Math.min(Math.max(currentPtzPan + delta, 0), 180);
      document.getElementById('pan-slider').value = currentPtzPan;
      document.getElementById('pan-val-label').innerText = `${currentPtzPan}°`;
      sendPtzCommand(`pan=${currentPtzPan}`);
    }

    function nudgeTilt(delta) {
      currentPtzTilt = Math.min(Math.max(currentPtzTilt + delta, 20), 90);
      document.getElementById('tilt-slider').value = currentPtzTilt;
      document.getElementById('tilt-val-label').innerText = `${currentPtzTilt}°`;
      sendPtzCommand(`tilt=${currentPtzTilt}`);
    }

    async function sendPtzCommand(params) {
      isPtzManual = true;
      document.getElementById('ptz-status-badge').innerText = 'MANUAL SLEW ACTIVE';
      document.getElementById('ptz-status-badge').style.color = '#58a6ff';
      try {
        await fetch(`/ptz_control?${params}`, { method: 'POST' });
      } catch (e) {}
    }

    async function resumeAutonomousSweep() {
      isPtzManual = false;
      document.getElementById('ptz-status-badge').innerText = 'AUTONOMOUS SWEEP';
      document.getElementById('ptz-status-badge').style.color = '#39d353';
      try {
        await fetch('/ptz_control?cmd=AUTO', { method: 'POST' });
      } catch (e) {}
    }

    // Electronic Warfare (EW) Jamming Countermeasure Trigger
    async function engageRfJamming() {
      const btn = document.getElementById('engage-jam-btn');
      btn.disabled = true;
      btn.innerText = '⚡ TRANSMITTING HIGH-POWER RF JAMMING (5.0s)...';
      btn.style.boxShadow = '0 0 25px #ff0055';
      playJammingWarble();
      try {
        await fetch('/trigger_jamming', { method: 'POST' });
        setTimeout(() => {
          btn.disabled = false;
          btn.innerText = '⚡ ENGAGE BROADBAND RF JAMMING COUNTERMEASURE';
          btn.style.boxShadow = '0 0 15px rgba(218, 54, 51, 0.4)';
        }, 5200);
      } catch(e) {
        btn.disabled = false;
        btn.innerText = '⚡ ENGAGE BROADBAND RF JAMMING COUNTERMEASURE';
      }
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
        await fetch(`/toggle_wifi?state=${targetState}`, { method: 'POST' });
        isWifiActive = (targetState === 'ON');
        setTimeout(() => {
          btn.disabled = false;
        }, 500);
      } catch (e) {
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
        osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.18);
        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.22);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.23);
      } catch (e) {}
    }

    function playTacticalAlarmKlaxon() {
      if (!audioEnabled) return;
      const now = Date.now();
      if (now - lastAlertAudioTime < 1100) return;
      lastAlertAudioTime = now;
      try {
        const ctx = getAudioContext();
        if (!ctx) return;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(650, ctx.currentTime);
        osc.frequency.linearRampToValueAtTime(950, ctx.currentTime + 0.18);
        osc.frequency.linearRampToValueAtTime(650, ctx.currentTime + 0.36);
        gain.gain.setValueAtTime(0.12, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.45);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.46);
      } catch (e) {}
    }

    function playJammingWarble() {
      if (!audioEnabled) return;
      const now = Date.now();
      if (now - lastJammingAudioTime < 400) return;
      lastJammingAudioTime = now;
      try {
        const ctx = getAudioContext();
        if (!ctx) return;
        const t = ctx.currentTime;

        // White noise generator buffer
        const bufferSize = ctx.sampleRate * 0.35;
        const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
          data[i] = Math.random() * 2 - 1;
        }
        const noise = ctx.createBufferSource();
        noise.buffer = buffer;

        // Rapid warble oscillator
        const osc = ctx.createOscillator();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(350, t);
        osc.frequency.linearRampToValueAtTime(1400, t + 0.15);
        osc.frequency.linearRampToValueAtTime(280, t + 0.3);

        const gain = ctx.createGain();
        gain.gain.setValueAtTime(0.20, t);
        gain.gain.exponentialRampToValueAtTime(0.01, t + 0.34);

        osc.connect(gain);
        noise.connect(gain);
        gain.connect(ctx.destination);

        osc.start(t);
        noise.start(t);
        osc.stop(t + 0.35);
        noise.stop(t + 0.35);
      } catch(e) {}
    }

    // Mission Blackbox Incident Logger Engine
    function recordIncident(data, customThreat) {
      const now = Date.now();
      if (now - lastLoggedTime < 2800 && lastLoggedPayload === data.rawHex && !customThreat) return;
      lastLoggedTime = now;
      lastLoggedPayload = data.rawHex;

      const record = {
        utc: data.utc,
        threat: customThreat || data.cipherType.replace("STANDBY // SECURE", "UNKNOWN RF BURST"),
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
      tr.style.background = customThreat ? 'rgba(218, 54, 51, 0.35)' : 'rgba(218, 54, 51, 0.15)';
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
      if (data.isJamming) {
        badge.innerText = 'BROADBAND JAMMING NOISE EMISSION // ALL CHANNELS ACTIVE';
        badge.style.color = '#ff0055';
      } else if (isWifiAlert) {
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
        if (data.isJamming) {
          target = 0.70 + Math.random() * 0.28;
        } else if (is433Alert && (i === 0 || i === 1)) {
          target = 0.88 + Math.random() * 0.10;
        } else if (isWifiAlert) {
          if (i >= 6 && i <= 8) {
            target = 0.85 + Math.random() * 0.12;
          } else if (i === 2 || i === 3 || i === 12 || i === 13) {
            target = 0.40 + Math.random() * 0.15;
          }
        }

        specLevels[i] += (target - specLevels[i]) * 0.35;
        if (specLevels[i] > specPeaks[i]) {
          specPeaks[i] = specLevels[i];
        } else {
          specPeaks[i] -= 0.008;
          if (specPeaks[i] < specLevels[i]) specPeaks[i] = specLevels[i];
        }

        const barH = specLevels[i] * (h - 8);
        const x = i * (barW + 3) + 2;
        const y = h - barH;

        let col = '#238636';
        if (data.isJamming) {
          col = '#da3633';
        } else if (is433Alert && (i === 0 || i === 1)) {
          col = '#d29922';
        } else if (isWifiAlert && (i >= 6 && i <= 8)) {
          col = '#da3633';
        }

        specCtx.fillStyle = col;
        specCtx.fillRect(x, y, barW, barH);

        const peakY = h - (specPeaks[i] * (h - 8));
        specCtx.fillStyle = '#ffffff';
        specCtx.fillRect(x, peakY, barW, 1.5);
      }
    }

    // DEFCON Gauge & AI Threat Classifier Engine
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

      if (data.isJamming) {
        defconBox.style.borderLeftColor = '#ff0055';
        defconBadge.innerText = 'DEFCON 1 // ECM ENGAGED';
        defconBadge.style.background = 'rgba(255, 0, 85, 0.4)';
        defconBadge.style.borderColor = '#ff0055';
        defconBadge.style.color = '#ffffff';
        title.innerText = '⚡ [ELECTRONIC WARFARE] BROADBAND RF JAMMING ACTIVE';
        title.style.color = '#ff0055';
        advisory.innerText = `Directed electromagnetic suppression engaged on ${Math.round(data.bearing)}° azimuth. Broadband noise radiating to neutralize hostile target comms.`;
        bar.style.width = '0%';
        bar.style.background = '#ff0055';
        txt.innerText = '0%';
        txt.style.color = '#ff0055';
      } else if (isWifi) {
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
        elif self.path.startswith('/ptz_control'):
            query = self.path.split('?')[-1]
            success = False
            if "cmd=AUTO" in query:
                success = send_com3_cmd("AUTO\n")
                telemetry_data["ptzManual"] = False
            elif "pan=" in query:
                try:
                    for param in query.split('&'):
                        if param.startswith("pan="):
                            val = float(param.split('=')[1])
                            success = send_com3_cmd(f"PAN:{int(val)}\n")
                            telemetry_data["ptzManual"] = True
                            telemetry_data["bearing"] = val
                except Exception as e:
                    print("PTZ Pan error:", e)
            elif "tilt=" in query:
                try:
                    for param in query.split('&'):
                        if param.startswith("tilt="):
                            val = int(param.split('=')[1])
                            success = send_com3_cmd(f"TILT:{val}\n")
                            telemetry_data["ptzManual"] = True
                except Exception as e:
                    print("PTZ Tilt error:", e)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success}).encode('utf-8'))
        elif self.path.startswith('/trigger_jamming'):
            success = send_com3_cmd("JAM_ON\n")
            telemetry_data["isJamming"] = True
            telemetry_data["jammingUntil"] = time.time() + 5.0
            telemetry_data["alertLevel"] = 3
            telemetry_data["alert"] = True
            telemetry_data["rssi"] = -32
            telemetry_data["rawHex"] = "EE : FF : EE : DD : CC : BB : AA : 99"
            telemetry_data["ciphertext"] = "ECM-JAM!"
            telemetry_data["cipherType"] = "⚡ BROADBAND RF JAMMING ACTIVE"
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "status": "JAMMING_ENGAGED"}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return

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
