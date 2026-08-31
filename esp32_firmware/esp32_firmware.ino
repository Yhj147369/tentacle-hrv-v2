/*
 * Tentacle HRV - 体感交互控制程序
 * Copyright (C) 2026 Yi Hengjun (伊恒君)
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLE2902.h>

#define HR_SERVICE_UUID "0000180D-0000-1000-8000-00805F9B34FB"
#define HR_CHAR_UUID    "00002A37-0000-1000-8000-00805F9B34FB"

// 玩具 UUID（需替换成你逆向得到的值）
#define TOY_SERVICE_UUID   "0000FFE0-0000-1000-8000-00805F9B34FB"
#define TOY_CHAR_UUID      "0000FFE1-0000-1000-8000-00805F9B34FB"

#define MAX_INTENSITY 60

BLEAdvertisedDevice* hrDevice = nullptr;
BLEAdvertisedDevice* toyDevice = nullptr;
bool doConnectHR = false;
bool doConnectToy = false;
bool hrConnected = false;
bool toyConnected = false;
BLECharacteristic* hrChar = nullptr;
BLECharacteristic* toyChar = nullptr;
BLEClient* toyClient = nullptr;

void sendToToy(int intensity, int waveform) {
  if (!toyConnected || toyChar == nullptr) {
    Serial.println("⚠️ 玩具未连接，指令已记录");
    return;
  }
  // 根据你逆向的协议修改此处
  uint8_t packet[] = {0xA1, 0x02, (uint8_t)intensity, 0x00, 0xB3};
  packet[3] = packet[0] ^ packet[1] ^ packet[2];
  toyChar->writeValue(packet, sizeof(packet));
  Serial.printf("📤 发送: %02X %02X %02X %02X %02X\n", packet[0], packet[1], packet[2], packet[3], packet[4]);
}

class HrCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice device) {
    if (device.haveServiceUUID() && device.isAdvertisingService(BLEUUID(HR_SERVICE_UUID))) {
      BLEDevice::getScan()->stop();
      hrDevice = new BLEAdvertisedDevice(device);
      doConnectHR = true;
    }
  }
};

class ToyCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice device) {
    if (device.haveServiceUUID() && device.isAdvertisingService(BLEUUID(TOY_SERVICE_UUID))) {
      BLEDevice::getScan()->stop();
      toyDevice = new BLEAdvertisedDevice(device);
      doConnectToy = true;
    }
  }
};

static void hrNotify(BLECharacteristic* pChar, uint8_t* data, size_t len) {
  if (len < 2) return;
  uint8_t flags = data[0];
  int hr = (flags & 0x01) ? (data[1] | (data[2] << 8)) : data[1];
  int ibi = (len >= 4) ? (data[2] | (data[3] << 8)) : 0;
  Serial.printf("HR:%d,IBI:%d\n", hr, ibi);
}

void connectHR() {
  BLEClient* client = BLEDevice::createClient();
  client->connect(*hrDevice);
  auto svc = client->getService(BLEUUID(HR_SERVICE_UUID));
  if (svc) {
    hrChar = svc->getCharacteristic(BLEUUID(HR_CHAR_UUID));
    if (hrChar) { hrChar->registerForNotify(hrNotify); hrConnected = true; Serial.println("✅ 手环已连接"); }
  }
}

void connectToy() {
  toyClient = BLEDevice::createClient();
  toyClient->connect(*toyDevice);
  auto svc = toyClient->getService(BLEUUID(TOY_SERVICE_UUID));
  if (svc) {
    toyChar = svc->getCharacteristic(BLEUUID(TOY_CHAR_UUID));
    if (toyChar) { toyConnected = true; Serial.println("✅ 玩具已连接"); }
  }
}

void scanHR() {
  auto scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new HrCallbacks());
  scan->start(3, false);
}

void scanToy() {
  auto scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new ToyCallbacks());
  scan->start(3, false);
}

void setup() {
  Serial.begin(115200);
  BLEDevice::init("ESP32_Bridge");
  scanHR();
  scanToy();
}

void loop() {
  if (doConnectHR && !hrConnected) { connectHR(); doConnectHR = false; }
  if (!hrConnected && hrDevice) { scanHR(); delay(1000); }
  if (doConnectToy && !toyConnected) { connectToy(); doConnectToy = false; }
  if (!toyConnected && toyDevice) { scanToy(); delay(1000); }

  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("SET")) {
      int intensity, duration, waveform;
      if (sscanf(cmd.c_str(), "SET %d %d %d", &intensity, &duration, &waveform) == 3) {
        intensity = min(intensity, MAX_INTENSITY);
        sendToToy(intensity, waveform);
        Serial.printf("✅ 执行: 强度%d 时长%d 波形%d\n", intensity, duration, waveform);
      }
    } else if (cmd == "STOP") {
      sendToToy(0, 0);
      Serial.println("⏹ 已停止");
    }
  }
  delay(10);
}