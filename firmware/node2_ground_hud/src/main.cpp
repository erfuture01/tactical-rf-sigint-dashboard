#include <Arduino.h>
#include <SPI.h>
#include <TFT_eSPI.h>
#include <HardwareSerial.h>
#include <TinyGPS++.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#define GPS_RX_PIN 17
#define GPS_TX_PIN 16
#define BUZZER_PIN 22
#define BUZZER_ACTIVE_LOW true

inline void buzzerMute() {
  digitalWrite(BUZZER_PIN, BUZZER_ACTIVE_LOW ? HIGH : LOW);
}

inline void buzzerSound() {
  digitalWrite(BUZZER_PIN, BUZZER_ACTIVE_LOW ? LOW : HIGH);
}

TFT_eSPI tft = TFT_eSPI();
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;

// Tactical Border SIGINT Telemetry Packet (Exact match with Node 1)
struct __attribute__((packed)) TelemetryPacket {
  uint32_t seq;
  uint8_t  band;          // 0 = CLEAR, 1 = 2.4 GHz COVERT, 2 = 433 MHz TACTICAL LORA
  uint8_t  channel;
  int16_t  rssi;
  uint16_t hitCount;
  float    bearingDeg;    // Line-of-Bearing (Azimuth 0 - 180 deg)
  uint8_t  alertLevel;    // 0 = SECURE, 1 = SUSPICIOUS, 2 = HOSTILE DETECTED
  float    estimatedDist; // Calculated Distance (Meters)
  uint8_t  wifiEnabled;   // 0 = Wi-Fi Muted (433M Only), 1 = Wi-Fi Detection Active
  uint8_t  rawPayload[8]; // Raw Intercepted RF Frame / Ciphertext Word
};

volatile TelemetryPacket rxPacket;
volatile bool newPacketAvailable = false;
uint32_t totalPacketsReceived = 0;
unsigned long lastPacketTime = 0;

// Radar display coordinates & state
const int cx = 38;
const int cy = 104;
const int R  = 34;
int prevNeedleX = cx;
int prevNeedleY = cy - R;
int prevBlipX = -1;
int prevBlipY = -1;

unsigned long lastUiUpdate = 0;
unsigned long lastDiagTime = 0;

// ESP-NOW Receive Callback
void onDataRecv(const uint8_t *mac, const uint8_t *incomingData, int len) {
  if (len == sizeof(TelemetryPacket)) {
    memcpy((void*)&rxPacket, incomingData, sizeof(TelemetryPacket));
    newPacketAvailable = true;
    totalPacketsReceived++;
    lastPacketTime = millis();
  }
}

void initEspNowReceiver() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() == ESP_OK) {
    Serial.println("[ESP-NOW] National Border HUD Receiver Initialized");
    esp_now_register_recv_cb(onDataRecv);
  } else {
    Serial.println("[ESP-NOW] Initialization Failed!");
  }
}

void drawRadarStaticGrid(bool alert) {
  uint16_t ringColor = alert ? TFT_RED : TFT_DARKGREEN;
  uint16_t rayColor  = alert ? TFT_MAROON : TFT_DARKGREEN;

  // Concentric Border Perimeter Defense Rings
  tft.drawCircle(cx, cy, R, ringColor);
  tft.drawCircle(cx, cy, R * 2 / 3, ringColor);
  tft.drawCircle(cx, cy, R / 3, ringColor);
  tft.drawFastHLine(cx - R - 2, cy, (R * 2) + 5, ringColor);

  // Sector Angle Rays (0°, 45°, 90°, 135°, 180°)
  tft.drawLine(cx, cy, cx, cy - R, rayColor);
  int r45 = (int)(R * 0.7071f);
  tft.drawLine(cx, cy, cx + r45, cy - r45, rayColor);
  tft.drawLine(cx, cy, cx - r45, cy - r45, rayColor);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=======================================================");
  Serial.println(">>> NATIONAL BORDER SECURITY - GROUND HUD (NODE 2) <<<");
  Serial.println("=======================================================");

  tft.init();
  tft.setRotation(3); // 160x128 Landscape
  tft.fillScreen(TFT_BLACK);

  // Title Bar Header
  tft.fillRect(0, 0, 160, 15, TFT_DARKCYAN);
  tft.setTextColor(TFT_WHITE, TFT_DARKCYAN);
  tft.setTextSize(1);
  tft.drawString("BORDER DEFENSE", 4, 4, 1);

  // Frame Separators
  tft.drawFastVLine(77, 16, 96, TFT_DARKGREY);
  tft.drawFastHLine(0, 112, 160, TFT_DARKGREY);

  drawRadarStaticGrid(false);
  initEspNowReceiver();

  pinMode(BUZZER_PIN, OUTPUT);
  buzzerMute(); // Silence buzzer immediately upon boot

  gpsSerial.begin(9600, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  Serial.println("[GPS] Ublox NEO-6M initialized on RX: 17, TX: 16");
}

void loop() {
  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }

  if (millis() - lastUiUpdate >= 40) {
    lastUiUpdate = millis();

    // 1. Clock & Satellite Lock
    char timeBuf[16];
    if (gps.time.isValid()) {
      snprintf(timeBuf, sizeof(timeBuf), "%02d:%02d:%02d",
        gps.time.hour(), gps.time.minute(), gps.time.second());
    } else {
      snprintf(timeBuf, sizeof(timeBuf), "GPS SYNC");
    }
    tft.setTextColor(TFT_WHITE, TFT_DARKCYAN);
    tft.fillRect(92, 2, 66, 12, TFT_DARKCYAN);
    tft.drawString(timeBuf, 96, 4, 1);

    // 2. Telemetry Packet Snapshot
    TelemetryPacket localPkt;
    noInterrupts();
    memcpy(&localPkt, (const void*)&rxPacket, sizeof(TelemetryPacket));
    newPacketAvailable = false;
    interrupts();

    bool linkAlive = (millis() - lastPacketTime < 2500);
    bool isHostile = (localPkt.alertLevel == 2);
    if (localPkt.wifiEnabled == 0 && localPkt.band == 1) {
      isHostile = false; // Strictly normal state when Wi-Fi is OFF
    }

    // 3. Radar Scope Rendering
    float rad = radians(localPkt.bearingDeg);
    int newNeedleX = cx - (int)(R * cos(rad));
    int newNeedleY = cy - (int)(R * sin(rad));

    // Wi-Fi Mode Status Badge above Radar
    tft.fillRect(2, 18, 73, 10, TFT_BLACK);
    tft.setTextSize(1);
    if (!linkAlive) {
      tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
      tft.drawString("SIG: STANDBY", 2, 18, 1);
    } else if (localPkt.wifiEnabled == 1) {
      tft.setTextColor(TFT_CYAN, TFT_BLACK);
      tft.drawString("MODE: +WI-FI", 2, 18, 1);
    } else {
      tft.setTextColor(TFT_GREEN, TFT_BLACK);
      tft.drawString("MODE: NORMAL", 2, 18, 1);
    }

    // Raw Intercepted Hex & Ciphertext Word above Radar Scope
    tft.fillRect(2, 30, 73, 34, TFT_BLACK);
    if (isHostile) {
      tft.setTextColor(TFT_YELLOW, TFT_BLACK);
      tft.drawString("RAW CIPHER:", 2, 30, 1);
      char hexLine[14];
      snprintf(hexLine, sizeof(hexLine), "%02X%02X %02X%02X",
        localPkt.rawPayload[0], localPkt.rawPayload[1], localPkt.rawPayload[2], localPkt.rawPayload[3]);
      tft.drawString(hexLine, 2, 42, 1);
      snprintf(hexLine, sizeof(hexLine), "%02X%02X %02X%02X",
        localPkt.rawPayload[4], localPkt.rawPayload[5], localPkt.rawPayload[6], localPkt.rawPayload[7]);
      tft.drawString(hexLine, 2, 54, 1);
    } else if (localPkt.wifiEnabled == 1) {
      tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
      tft.drawString("SIG: SNIFFING", 2, 34, 1);
      tft.drawString("2.4G PACKETS", 2, 46, 1);
    } else {
      tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
      tft.drawString("BORDER: SAFE", 2, 34, 1);
      tft.drawString("433M STANDBY", 2, 46, 1);
    }

    // Erase previous needle and blip
    if (prevNeedleX != cx || prevNeedleY != cy) {
      tft.drawLine(cx, cy, prevNeedleX, prevNeedleY, TFT_BLACK);
    }
    if (prevBlipX >= 0) {
      tft.fillCircle(prevBlipX, prevBlipY, 6, TFT_BLACK);
      tft.drawLine(cx, cy, prevBlipX, prevBlipY, TFT_BLACK);
      prevBlipX = -1;
      prevBlipY = -1;
    }

    drawRadarStaticGrid(isHostile);

    if (linkAlive) {
      // Draw Radar Needle
      uint16_t needleColor = isHostile ? TFT_RED : TFT_GREEN;
      tft.drawLine(cx, cy, newNeedleX, newNeedleY, needleColor);
      prevNeedleX = newNeedleX;
      prevNeedleY = newNeedleY;

      // When Hostile Signal Intercepted: Plot Target Blip & Interception Vector
      if (isHostile) {
        int blipDist = map(constrain((int)localPkt.estimatedDist, 10, 400), 10, 400, 10, R - 2);
        int bx = cx - (int)(blipDist * cos(rad));
        int by = cy - (int)(blipDist * sin(rad));

        // Draw Interception Vector Line
        tft.drawLine(cx, cy, bx, by, TFT_YELLOW);

        // Glowing Target Icon
        tft.fillCircle(bx, by, 3, TFT_RED);
        tft.drawCircle(bx, by, 4, TFT_WHITE);

        prevBlipX = bx;
        prevBlipY = by;
      }
    } else {
      tft.drawLine(cx, cy, cx, cy - R, TFT_RED);
      prevNeedleX = cx;
      prevNeedleY = cy - R;
    }

    // 4. Target Geolocation Calculation (Direct Geodetic Projection)
    double baseLat = gps.location.isValid() ? gps.location.lat() : 28.5412;
    double baseLon = gps.location.isValid() ? gps.location.lng() : 77.2031;

    double tgtLat = baseLat;
    double tgtLon = baseLon;

    if (localPkt.alertLevel > 0 && localPkt.estimatedDist > 0) {
      double dLat = (localPkt.estimatedDist * cos(rad)) / 111139.0;
      double dLon = (localPkt.estimatedDist * sin(rad)) / (111139.0 * cos(radians(baseLat)));
      tgtLat = baseLat + dLat;
      tgtLon = baseLon + dLon;
    }

    // 5. Tactical Telemetry & Target Coordinates Card (x: 80, y: 18)
    tft.setTextSize(1);
    char buf[26];

    // Line 1: THREAT
    tft.fillRect(80, 18, 78, 10, TFT_BLACK);
    tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    tft.drawString("STAT:", 80, 18, 1);
    if (!linkAlive) {
      tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
      tft.drawString("OFFLINE", 110, 18, 1);
    } else if (isHostile) {
      tft.setTextColor(TFT_RED, TFT_BLACK);
      tft.drawString("HOSTILE", 110, 18, 1);
    } else if (localPkt.wifiEnabled == 1) {
      tft.setTextColor(TFT_CYAN, TFT_BLACK);
      tft.drawString("WI-FI ON", 110, 18, 1);
    } else {
      tft.setTextColor(TFT_GREEN, TFT_BLACK);
      tft.drawString("NORMAL", 110, 18, 1);
    }

    // Line 2: COMM BAND
    tft.fillRect(80, 31, 78, 10, TFT_BLACK);
    tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    tft.drawString("BAND:", 80, 31, 1);
    if (isHostile && localPkt.band == 2) {
      tft.setTextColor(TFT_MAGENTA, TFT_BLACK);
      tft.drawString("433 LORA", 110, 31, 1);
    } else if (isHostile && localPkt.band == 1) {
      tft.setTextColor(TFT_CYAN, TFT_BLACK);
      tft.drawString("2.4G WIFI", 110, 31, 1);
    } else if (localPkt.wifiEnabled == 1) {
      tft.setTextColor(TFT_CYAN, TFT_BLACK);
      tft.drawString("WI-FI SCAN", 110, 31, 1);
    } else {
      tft.setTextColor(TFT_GREEN, TFT_BLACK);
      tft.drawString("NORMAL", 110, 31, 1);
    }

    // Line 3: LINE-OF-BEARING (LOB)
    tft.fillRect(80, 44, 78, 10, TFT_BLACK);
    tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    tft.drawString("LOB :", 80, 44, 1);
    tft.setTextColor(isHostile ? TFT_YELLOW : TFT_WHITE, TFT_BLACK);
    snprintf(buf, sizeof(buf), "%03.0f DEG", localPkt.bearingDeg);
    tft.drawString(buf, 110, 44, 1);

    // Line 4: ESTIMATED RANGE
    tft.fillRect(80, 57, 78, 10, TFT_BLACK);
    tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    tft.drawString("DIST:", 80, 57, 1);
    if (isHostile) {
      tft.setTextColor(TFT_YELLOW, TFT_BLACK);
      snprintf(buf, sizeof(buf), "%.0fm", localPkt.estimatedDist);
      tft.drawString(buf, 110, 57, 1);
    } else {
      tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
      tft.drawString("---", 110, 57, 1);
    }

    // Line 5 & 6: EXACT TARGET GPS COORDINATES!
    tft.fillRect(80, 70, 78, 24, TFT_BLACK);
    if (isHostile) {
      tft.setTextColor(TFT_RED, TFT_BLACK);
      snprintf(buf, sizeof(buf), "T.LAT:%.4f", tgtLat);
      tft.drawString(buf, 80, 70, 1);
      snprintf(buf, sizeof(buf), "T.LON:%.4f", tgtLon);
      tft.drawString(buf, 80, 82, 1);
    } else {
      tft.setTextColor(TFT_GREEN, TFT_BLACK);
      snprintf(buf, sizeof(buf), "B.LAT:%.4f", baseLat);
      tft.drawString(buf, 80, 70, 1);
      snprintf(buf, sizeof(buf), "B.LON:%.4f", baseLon);
      tft.drawString(buf, 80, 82, 1);
    }

    // Line 7: SIG STRENGTH
    tft.fillRect(80, 96, 78, 10, TFT_BLACK);
    tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    tft.drawString("SIG :", 80, 96, 1);
    if (isHostile) {
      tft.setTextColor(TFT_RED, TFT_BLACK);
      snprintf(buf, sizeof(buf), "%ddBm", localPkt.rssi);
      tft.drawString(buf, 110, 96, 1);
    } else {
      tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
      tft.drawString("QUIET", 110, 96, 1);
    }

    // 6. Bottom Border Defense Status Banner (y: 114 - 127)
    uint16_t statusBg = (!linkAlive) ? TFT_MAROON :
                        isHostile ? TFT_RED : TFT_DARKGREEN;

    tft.fillRect(0, 114, 160, 14, statusBg);
    tft.setTextColor(TFT_WHITE, statusBg);

    if (!linkAlive) {
      tft.drawCentreString("LINK: OFFLINE", 80, 116, 1);
    } else if (isHostile) {
      tft.drawCentreString("! INTRUDER COMMS LOCKED !", 80, 116, 1);
    } else if (localPkt.wifiEnabled == 1) {
      tft.drawCentreString("WI-FI SCANNER: ACTIVE", 80, 116, 1);
    } else {
      tft.drawCentreString("BORDER DEFENSE: NORMAL", 80, 116, 1);
    }
  }

  // Serial Diagnostic Logging (10Hz Real-Time Telemetry Stream to Dashboard)
  if (millis() - lastDiagTime >= 100) {
    lastDiagTime = millis();
    int sats = gps.satellites.isValid() ? gps.satellites.value() : 0;
    bool isHostile = (rxPacket.alertLevel == 2);
    char hexWord[20];
    snprintf(hexWord, sizeof(hexWord), "%02X%02X%02X%02X%02X%02X%02X%02X",
      rxPacket.rawPayload[0], rxPacket.rawPayload[1], rxPacket.rawPayload[2], rxPacket.rawPayload[3],
      rxPacket.rawPayload[4], rxPacket.rawPayload[5], rxPacket.rawPayload[6], rxPacket.rawPayload[7]);
    Serial.printf("[BORDER SEC HUD] %s | Alert: %d | LOB: %03.0f° | Band: %d | Dist: %.1f | HEX: %s | WiFiMode: %d | RSSI: %ddBm | Sats: %d | BaseLat: %.4f\n",
      isHostile ? "TARGET LOCKED" : "PERIMETER PATROL",
      rxPacket.alertLevel,
      rxPacket.bearingDeg, rxPacket.band, rxPacket.estimatedDist,
      hexWord,
      rxPacket.wifiEnabled, rxPacket.rssi, sats,
      gps.location.isValid() ? gps.location.lat() : 28.5412);
  }

  // Tactical Alert Buzzer Pulse (GPIO 22 - Inverted for Active-Low Modules)
  static unsigned long lastBuzzerToggle = 0;
  static bool buzzerActive = false;
  bool isAlert = (millis() - lastPacketTime <= 2500) && (rxPacket.alertLevel == 2);
  if (isAlert) {
    if (millis() - lastBuzzerToggle >= 120) {
      lastBuzzerToggle = millis();
      buzzerActive = !buzzerActive;
      if (buzzerActive) {
        buzzerSound();
      } else {
        buzzerMute();
      }
    }
  } else {
    buzzerMute();
    buzzerActive = false;
  }
}
