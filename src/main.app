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

/*
 * 修订记录：
 *  - 修复串口协议解析：服务器下发 "SET <强度> <时长秒> <波形名>"，
 *    波形为字符串(constant/sine/pulse/random)，映射为整数模式后交给 sendToToy()；
 *    兼容旧格式的数字波形(0-3)。
 *  - 新增时长定时停止：基于 millis() 的非阻塞倒计时，SET 发送后
 *    duration 秒自动发送停止指令；收到新 SET 会重新计时，收到 STOP 会取消计时。
 *  - 新增 BEAT 心跳静默处理（服务器每 2 秒发送一次）。
 *  - setup() 中 Serial.setTimeout(50) 缩短 readStringUntil 阻塞时间，
 *    避免串口空闲时卡住主循环、影响定时停止精度。
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

// 蓝牙玩具物理强度上限（固件硬限制，最终按 min(服务器值, 此值) 执行）
#define MAX_INTENSITY 60
// 单次 SET 允许的最大时长（秒），与 server.py 的 MAX_DURATION_S=300 保持一致
#define MAX_DURATION_S 300

// 波形模式整数编码: constant=0, sine=1, pulse=2, random=3
#define WAVE_CONSTANT 0
#define WAVE_SINE     1
#define WAVE_PULSE    2
#define WAVE_RANDOM   3

BLEAdvertisedDevice* hrDevice = nullptr;
BLEAdvertisedDevice* toyDevice = nullptr;
bool doConnectHR = false;
bool doConnectToy = false;
bool hrConnected = false;
bool toyConnected = false;
BLERemoteCharacteristic* hrChar = nullptr;
BLERemoteCharacteristic* toyChar = nullptr;
BLEClient* hrClient = nullptr;
BLEClient* toyClient = nullptr;

// ===== 时长自动停止（非阻塞，基于 millis） =====
unsigned long stopAtMillis = 0;   // 计划自动停止的时刻（毫秒）
bool autoStopActive = false;      // 是否有未完成的定时停止任务

// 返回 true 表示已真正写入玩具特征；false 表示玩具未连接（仅记录）
bool sendToToy(int intensity, int waveform) {
  (void)waveform;  // TODO: 逆向出玩具协议后，把波形模式(0-3)编码进数据包的对应字节
  if (!toyConnected || toyChar == nullptr) {
    Serial.println("⚠️ 玩具未连接，指令已记录");
    return false;
  }
  // 根据你逆向的协议修改此处
  uint8_t packet[] = {0xA1, 0x02, (uint8_t)intensity, 0x00, 0xB3};
  packet[3] = packet[0] ^ packet[1] ^ packet[2];
  toyChar->writeValue(packet, sizeof(packet));
  Serial.printf("📤 发送: %02X %02X %02X %02X %02X\n", packet[0], packet[1], packet[2], packet[3], packet[4]);
  return true;
}

// 登记一个自动停止任务：durationSeconds 秒后发送停止
void scheduleStop(int durationSeconds) {
  if (durationSeconds <= 0) durationSeconds = 0;  // 0 秒 = 下一轮立即停止
  stopAtMillis = millis() + (unsigned long)durationSeconds * 1000UL;
  autoStopActive = true;
}

// 取消自动停止任务（不额外发送停止，调用方需自行处理）
void cancelAutoStop() {
  autoStopActive = false;
  stopAtMillis = 0;
}

// 在 loop() 中周期性调用：到达停止时刻则自动发送停止指令
void checkAutoStop() {
  if (!autoStopActive) return;
  // 用减法比较，避免 millis() 溢出回绕导致永不触发
  if ((long)(millis() - stopAtMillis) >= 0) {
    autoStopActive = false;
    Serial.println("⏹ 时长结束，自动停止");
    sendToToy(0, WAVE_CONSTANT);
  }
}

// 把波形字符串映射为整数模式；无法识别时返回 -1
int waveModeFromString(String s) {
  s.trim();
  s.toLowerCase();
  if (s == "constant") return WAVE_CONSTANT;
  if (s == "sine")     return WAVE_SINE;
  if (s == "pulse")    return WAVE_PULSE;
  if (s == "random")   return WAVE_RANDOM;
  // 兼容旧协议/手动调试发送数字波形: SET 30 10 2
  if (s.length() >= 1 && s[0] >= '0' && s[0] <= '9') {
    int n = s.toInt();
    if (n >= 0 && n <= 3) return n;
  }
  return -1;
}

// 按空格把命令切成最多 maxArgs 个参数（容忍连续/多余空格）
int parseArgs(const String &cmd, String *args, int maxArgs) {
  int n = 0;
  int pos = 0;
  int len = cmd.length();
  while (pos < len && n < maxArgs) {
    while (pos < len && cmd[pos] == ' ') pos++;   // 跳过前导空格
    if (pos >= len) break;
    int end = cmd.indexOf(' ', pos);              // 找下一个空格
    if (end == -1) end = len;
    args[n] = cmd.substring(pos, end);
    n++;
    pos = end + 1;
  }
  return n;
}

// 处理一行串口指令: SET <强度> <时长秒> <波形> | STOP
void handleSerialCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  // 服务器每 2 秒发送一次 BEAT 心跳，静默忽略
  if (cmd.equalsIgnoreCase("BEAT")) return;

  if (cmd.equalsIgnoreCase("STOP")) {
    sendToToy(0, WAVE_CONSTANT);
    cancelAutoStop();
    Serial.println("⏹ 已停止");
    return;
  }

  String args[4];
  int n = parseArgs(cmd, args, 4);
  if (n >= 4 && args[0].equalsIgnoreCase("SET")) {
    int intensity = args[1].toInt();
    int duration  = args[2].toInt();
    int waveform  = waveModeFromString(args[3]);
    if (waveform < 0) {
      Serial.printf("⚠️ 未知波形: %s（应为 constant/sine/pulse/random 或 0-3）\n", args[3].c_str());
      return;
    }
    intensity = constrain(intensity, 0, MAX_INTENSITY);
    duration  = constrain(duration, 0, MAX_DURATION_S);
    if (sendToToy(intensity, waveform)) {
      scheduleStop(duration);
      Serial.printf("✅ 执行: 强度%d 时长%d秒 波形%d\n", intensity, duration, waveform);
    } else {
      Serial.println("⚠️ 玩具未连接，指令已记录（未计时）");
    }
    return;
  }

  Serial.printf("⚠️ 无法识别指令: %s\n", cmd.c_str());
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

// arduino-esp32 3.x 的通知回调签名
static void hrNotify(BLERemoteCharacteristic* pBLERemoteCharacteristic, uint8_t* data, size_t len, bool isNotify) {
  if (len < 2) return;
  uint8_t flags = data[0];
  int hr = (flags & 0x01) ? (data[1] | (data[2] << 8)) : data[1];
  int ibi = (len >= 4) ? (data[2] | (data[3] << 8)) : 0;
  Serial.printf("HR:%d,IBI:%d\n", hr, ibi);
}

void connectHR() {
  if (hrClient == nullptr) hrClient = BLEDevice::createClient();
  if (!hrClient->connect(hrDevice)) {
    Serial.println("⚠️ 手环连接失败，稍后重试");
    return;
  }
  auto svc = hrClient->getService(BLEUUID(HR_SERVICE_UUID));
  if (svc) {
    hrChar = svc->getCharacteristic(BLEUUID(HR_CHAR_UUID));
    if (hrChar) {
      hrChar->registerForNotify(hrNotify);
      hrConnected = true;
      Serial.println("✅ 手环已连接");
    }
  }
}

void connectToy() {
  if (toyClient == nullptr) toyClient = BLEDevice::createClient();
  if (!toyClient->connect(toyDevice)) {
    Serial.println("⚠️ 玩具连接失败，稍后重试");
    return;
  }
  auto svc = toyClient->getService(BLEUUID(TOY_SERVICE_UUID));
  if (svc) {
    toyChar = svc->getCharacteristic(BLEUUID(TOY_CHAR_UUID));
    if (toyChar) {
      toyConnected = true;
      Serial.println("✅ 玩具已连接");
    }
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
  Serial.setTimeout(50);  // 缩短 readStringUntil 的阻塞等待，保证主循环与定时停止及时
  BLEDevice::init("ESP32_Bridge");
  scanHR();
  scanToy();
}

void loop() {
  // 1) 每轮先检查定时停止，尽量准时
  checkAutoStop();

  // 2) BLE 连接维护
  if (doConnectHR && !hrConnected) { connectHR(); doConnectHR = false; }
  if (!hrConnected && hrDevice) { scanHR(); delay(1000); }
  if (doConnectToy && !toyConnected) { connectToy(); doConnectToy = false; }
  if (!toyConnected && toyDevice) { scanToy(); delay(1000); }

  // 3) 串口指令解析
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    handleSerialCommand(cmd);
  }
  delay(10);
}
