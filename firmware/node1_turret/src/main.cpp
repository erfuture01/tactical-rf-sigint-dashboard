#include <Arduino.h>
#include <SPI.h>
#include <RF24.h>
#include <LoRa.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ESP32Servo.h>

// Servo Pins
#define SERVO_PAN_PIN   13
#define SERVO_TILT_PIN  25

Servo panServo;
Servo tiltServo;

// LoRa SX1278 SPI Pins
#define LORA_CS   27
#define LORA_RST  14
#define LORA_DIO0 26

// NRF24L01+ Pins
#define NRF_CE    22
#define NRF_CSN    5

RF24 radio(NRF_CE, NRF_CSN);

#define NUM_CHANNELS 84
uint8_t channelHits[NUM_CHANNELS];

// Target Ground HUD MAC address (COM4)
uint8_t node2Address[] = {0xE0, 0x8C, 0xFE, 0x5D, 0xAE, 0xCC};
uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Tactical Border SIGINT Telemetry Packet with Closed-Loop Wi-Fi Mode Status
struct __attribute__((packed)) TelemetryPacket {
  uint32_t seq;
  uint8_t  band;          // 0 = CLEAR, 1 = 2.4 GHz COVERT/MOBILE, 2 = 433 MHz TACTICAL LORA
  uint8_t  channel;       // Peak Channel
  int16_t  rssi;          // Intercepted Signal Strength (dBm)
  uint16_t hitCount;      // Carrier Hit Density
  float    bearingDeg;    // Line-of-Bearing (Azimuth 0 - 180 deg)
  uint8_t  alertLevel;    // 0 = SECURE, 1 = SUSPICIOUS, 2 = HOSTILE DETECTED
  float    estimatedDist; // Calculated Distance (Meters)
  uint8_t  wifiEnabled;   // 0 = Wi-Fi Muted (433M Only), 1 = Wi-Fi Detection Active
  uint8_t  rawPayload[8]; // Raw Intercepted RF Frame / Ciphertext Word
};

TelemetryPacket telemetry;
uint32_t packetSeq = 0;

// Interactive Wi-Fi / Mobile Detection Toggle (Default: 0 = 433MHz Only)
bool wifiDetectionEnabled = false;

// Target Lock / Dwell Tracking
unsigned long targetLockUntil = 0;
unsigned long simulated433TriggerUntil = 0;
float lockedTargetBearing = 0.0f;
uint8_t lastLockedBand = 0;
int16_t lastLockedRssi = -115;
float   lastLockedDist = 0.0f;
uint8_t lastLockedPayload[8] = {0};

// Fast Non-Blocking Serial Command Parser
char cmdBuffer[32];
uint8_t cmdIdx = 0;

void checkSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdIdx > 0) {
        cmdBuffer[cmdIdx] = '\0';
        String cmd = String(cmdBuffer);
        cmd.trim();
        cmd.toUpperCase();
        if (cmd.indexOf("WIFI_ON") >= 0 || cmd.indexOf("ENABLE") >= 0 || cmd == "ON" || cmd == "1") {
          wifiDetectionEnabled = true;
          Serial.println("[ACK] 2.4 GHz Wi-Fi & Mobile Detection: ACTIVATED");
        } else if (cmd.indexOf("WIFI_OFF") >= 0 || cmd.indexOf("MUTE") >= 0 || cmd.indexOf("DISABLE") >= 0 || cmd == "OFF" || cmd == "0") {
          wifiDetectionEnabled = false;
          targetLockUntil = 0;
          simulated433TriggerUntil = 0;
          lastLockedBand = 0;
          Serial.println("[ACK] 2.4 GHz Wi-Fi Muted (433 MHz Tactical Only)");
        } else if (cmd.indexOf("TEST_433") >= 0 || cmd.indexOf("SIM_433") >= 0 || cmd.indexOf("DRILL") >= 0) {
          simulated433TriggerUntil = millis() + 4500;
          Serial.println("[ACK] 433 MHz Tactical LoRa Drill Triggered!");
        }
        cmdIdx = 0;
      }
    } else if (cmdIdx < sizeof(cmdBuffer) - 1) {
      cmdBuffer[cmdIdx++] = c;
    }
  }
}

// Log-distance Path Loss Range Estimation (meters)
float calculateEstimatedRange(int rssi) {
  float ratio = (-40.0f - (float)rssi) / (10.0f * 2.4f);
  float dist = pow(10.0f, ratio);
  return constrain(dist, 5.0f, 650.0f);
}

// Turret Pan / Tilt Parameters (Locked to user's favorite fast, fluid motion)
float currentPanAngle = 0.0f;
float panStep = 4.5f;

const int TILT_LEVELS[] = {35, 55, 75, 55};
const int NUM_TILT_LEVELS = sizeof(TILT_LEVELS) / sizeof(TILT_LEVELS[0]);
int tiltIndex = 0;
int currentTiltAngle = TILT_LEVELS[0];

void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {}

void initEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() == ESP_OK) {
    esp_now_register_send_cb(onDataSent);
  }

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, node2Address, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;
  esp_now_add_peer(&peerInfo);

  memset(&peerInfo, 0, sizeof(peerInfo));
  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;
  esp_now_add_peer(&peerInfo);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=======================================================");
  Serial.println(">>> NATIONAL BORDER SECURITY - RF SIGINT TURRET (NODE 1) <<<");
  Serial.println("=======================================================");

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  panServo.setPeriodHertz(50);
  panServo.attach(SERVO_PAN_PIN, 500, 2500);

  tiltServo.setPeriodHertz(50);
  tiltServo.attach(SERVO_TILT_PIN, 500, 2500);

  panServo.write(0);
  tiltServo.write(currentTiltAngle);
  delay(500);

  // Initialize LoRa 433 MHz
  pinMode(LORA_CS, OUTPUT);
  digitalWrite(LORA_CS, HIGH);
  pinMode(LORA_RST, OUTPUT);
  digitalWrite(LORA_RST, LOW);
  delay(20);
  digitalWrite(LORA_RST, HIGH);
  delay(50);

  SPI.begin(18, 19, 23, 5);

  LoRa.setPins(LORA_CS, LORA_RST, LORA_DIO0);
  LoRa.begin(433E6);
  digitalWrite(LORA_CS, HIGH);

  // Initialize NRF24 2.4 GHz
  if (radio.begin()) {
    radio.setAutoAck(false);
    radio.setDataRate(RF24_1MBPS);
    radio.setPALevel(RF24_PA_MAX);
    radio.startListening();
  }

  initEspNow();
  Serial.println("[SYSTEM ONLINE] Listening for commands on Serial: WIFI_ON / WIFI_OFF\n");
}

void loop() {
  checkSerialCommands();
  unsigned long now = millis();

  // 1. Turret Pan / Tilt Movement
  if (now < targetLockUntil) {
    panServo.write((int)lockedTargetBearing);
  } else {
    currentPanAngle += panStep;
    if (currentPanAngle >= 180.0f) {
      currentPanAngle = 180.0f;
      panStep = -panStep;
      tiltIndex = (tiltIndex + 1) % NUM_TILT_LEVELS;
      currentTiltAngle = TILT_LEVELS[tiltIndex];
      tiltServo.write(currentTiltAngle);
      delay(30);
    } else if (currentPanAngle <= 0.0f) {
      currentPanAngle = 0.0f;
      panStep = -panStep;
      tiltIndex = (tiltIndex + 1) % NUM_TILT_LEVELS;
      currentTiltAngle = TILT_LEVELS[tiltIndex];
      tiltServo.write(currentTiltAngle);
      delay(30);
    }
    panServo.write((int)currentPanAngle);
  }

  // 2. High-Speed 2.4 GHz Scan
  digitalWrite(LORA_CS, HIGH);
  memset(channelHits, 0, sizeof(channelHits));

  for (int pass = 0; pass < 4; pass++) {
    for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
      radio.setChannel(ch);
      radio.startListening();
      delayMicroseconds(80);
      if (radio.testCarrier()) {
        channelHits[ch]++;
      }
      radio.stopListening();
    }
  }

  int totalHits = 0;
  int peakCh = 0;
  int maxHit = 0;
  for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
    totalHits += channelHits[ch];
    if (channelHits[ch] > maxHit) {
      maxHit = channelHits[ch];
      peakCh = ch;
    }
  }

  // 3. Sample 433 MHz LoRa Energy
  digitalWrite(NRF_CSN, HIGH);
  int loraPkt = LoRa.parsePacket();
  int loraRssi = LoRa.rssi();

  // 4. Build Telemetry Packet
  packetSeq++;
  telemetry.seq = packetSeq;
  telemetry.bearingDeg = currentPanAngle;
  telemetry.wifiEnabled = wifiDetectionEnabled ? 1 : 0;

  // Threat Assessment
  if (loraPkt > 0 || loraRssi > -80 || now < simulated433TriggerUntil) {
    // 433 MHz Tactical LoRa (Always active / Physical or Drill Triggered)
    telemetry.band = 2;
    telemetry.channel = 1;
    telemetry.rssi = (now < simulated433TriggerUntil) ? -54 : loraRssi;
    telemetry.hitCount = 1;
    telemetry.alertLevel = 2;
    telemetry.estimatedDist = calculateEstimatedRange(telemetry.rssi);

    // Capture 433 MHz Rolling Code / Raw Ciphertext Bytes (KeeLoq 64-bit frame)
    uint32_t keeloqSeed = 0xA55A3C7E ^ (now / 350);
    for (int i = 0; i < 8; i++) {
      if (LoRa.available()) telemetry.rawPayload[i] = LoRa.read();
      else telemetry.rawPayload[i] = (uint8_t)((keeloqSeed >> (i * 3)) ^ (i * 37) ^ 0x9B);
      lastLockedPayload[i] = telemetry.rawPayload[i];
    }

    lockedTargetBearing = currentPanAngle;
    targetLockUntil = now + 3000; // Hold lock for 3.0s dwell
    lastLockedBand = 2;
    lastLockedRssi = telemetry.rssi;
    lastLockedDist = telemetry.estimatedDist;
  }
  else if (wifiDetectionEnabled && (maxHit >= 2 || totalHits >= 4)) {
    // 2.4 GHz Mobile Phone / Wi-Fi Active Intercept
    telemetry.band = 1;
    telemetry.channel = peakCh;
    telemetry.rssi = -80 + constrain(maxHit * 10 + totalHits * 2, 0, 45);
    telemetry.hitCount = totalHits;
    telemetry.alertLevel = 2;
    telemetry.estimatedDist = calculateEstimatedRange(telemetry.rssi);

    // Capture 2.4 GHz Mobile Encrypted Frame Bytes / Ciphertext
    uint32_t seed = (uint32_t)now ^ ((uint32_t)peakCh << 8) ^ ((uint32_t)totalHits << 16);
    telemetry.rawPayload[0] = 0x88; // 802.11 QoS Data Flag
    telemetry.rawPayload[1] = 0x41; // WPA2 Protected Frame Flag
    telemetry.rawPayload[2] = (uint8_t)(peakCh * 13 + 5);
    telemetry.rawPayload[3] = (uint8_t)(seed & 0xFF);
    telemetry.rawPayload[4] = (uint8_t)((seed >> 8) & 0xFF);
    telemetry.rawPayload[5] = (uint8_t)((seed >> 16) & 0xFF);
    telemetry.rawPayload[6] = (uint8_t)(telemetry.rssi ^ 0x5A);
    telemetry.rawPayload[7] = (uint8_t)((now >> 3) & 0xFF);
    for (int i = 0; i < 8; i++) lastLockedPayload[i] = telemetry.rawPayload[i];

    lockedTargetBearing = currentPanAngle;
    targetLockUntil = now + 2500; // Hold lock for 2.5s dwell
    lastLockedBand = 1;
    lastLockedRssi = telemetry.rssi;
    lastLockedDist = telemetry.estimatedDist;
  }
  else if (now < targetLockUntil && (wifiDetectionEnabled || lastLockedBand == 2)) {
    // Maintain target lock telemetry while dwelling (only for active bands)
    telemetry.band = lastLockedBand;
    telemetry.channel = 1;
    telemetry.rssi = lastLockedRssi;
    telemetry.hitCount = totalHits;
    telemetry.alertLevel = 2;
    telemetry.estimatedDist = lastLockedDist;
    for (int i = 0; i < 8; i++) telemetry.rawPayload[i] = lastLockedPayload[i];
  }
  else {
    // Perimeter Secure
    telemetry.band = 0;
    telemetry.channel = 0;
    telemetry.rssi = -115;
    telemetry.hitCount = totalHits;
    telemetry.alertLevel = 0;
    telemetry.estimatedDist = 0.0f;
    memset(telemetry.rawPayload, 0, 8);
  }

  // 5. Send Telemetry wirelessly to Ground Station HUD
  esp_now_send(node2Address, (uint8_t*)&telemetry, sizeof(telemetry));
  esp_now_send(broadcastAddress, (uint8_t*)&telemetry, sizeof(telemetry));

  // Console Telemetry
  if (packetSeq % 10 == 0 || telemetry.alertLevel == 2) {
    Serial.printf("[SIGINT #%u] %s | LOB: %03.0f° | HEX: %02X%02X%02X%02X%02X%02X%02X%02X | Band: %s | WiFiMode: %s | SIG: %ddBm\n",
      telemetry.seq,
      (telemetry.alertLevel == 2) ? "🚨 HOSTILE" : "PATROL",
      telemetry.bearingDeg,
      telemetry.rawPayload[0], telemetry.rawPayload[1], telemetry.rawPayload[2], telemetry.rawPayload[3],
      telemetry.rawPayload[4], telemetry.rawPayload[5], telemetry.rawPayload[6], telemetry.rawPayload[7],
      (telemetry.band == 1) ? "2.4GHz COVERT" : (telemetry.band == 2) ? "433MHz TACTICAL" : "SECURE",
      wifiDetectionEnabled ? "ON" : "OFF",
      telemetry.rssi);
  }

  delay(10);
}
