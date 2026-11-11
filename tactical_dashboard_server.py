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
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Consolas', 'Courier New', monospace; }
    body { background-color: #06090e; color: #e6edf3; padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #238636; padding-bottom: 12px; margin-bottom: 20px; }
    .header h1 { font-size: 20px; color: #39d353; letter-spacing: 2px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .card { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 18px; }
    .card-title { font-size: 13px; color: #8b949e; letter-spacing: 1.5px; border-bottom: 1px solid #21262d; padding-bottom: 8px; margin-bottom: 14px; display: flex; justify-content: space-between; }
    
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

      <div style="margin-top: 10px; font-size: 11px; color: #8b949e; text-align: right;">
        BASE STATION: <span id="base-coord">28.5412° N, 77.2031° E</span>
      </div>
    </div>
  </div>

  <script>
    const canvas = document.getElementById('radarCanvas');
    const ctx = canvas.getContext('2d');
    let isWifiActive = false;
    let lastAlertTime = 0;
    let lastAlertBearing = 0;
    let lastAlertDist = 50.0;

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
      } catch (e) {}
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
