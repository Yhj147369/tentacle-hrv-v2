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
 *  - 新增设备类型(Device Profile)适配框架(toy_profiles.h/.cpp)：玩具通道参数化，
 *    支持体感设备 A(默认，现协议逐字节不变)与体感设备 B类(预留，待逆向)。切换方式见下方
 *    ACTIVE_TOY_PROFILE 宏；设计见仓库 docs/体感设备适配说明.md。
 *  - 修正心率特征解析：按 0x2A37 位标志读取心率(1/2 字节)与 RR-Interval(1/1024 秒→毫秒)，
 *    修复旧实现「心率 16 位时 IBI 字节与心率重叠」「用 len>=4 代替 bit4 判断 RR 存在」
 *    导致 IBI 恒错、后端 HRV 基线失效的问题。
 *  - 新增断线检测：3.x 的 BLE 客户端不会自动复位我方状态，旧逻辑一旦连上就永远认为
 *    「还连着」——手环自动关机/玩具休眠后既不再重连也不再上报，后端 HRV 静默失效。
 *    现改为在 loop() 中用 isConnected() 轮询，掉线即复位标志并重连。
 *  - 串口改为非阻塞行缓冲轮询：旧实现每轮只 readStringUntil 一行，而 BLE 扫描会阻塞
 *    3 秒，期间到达的多条指令只能排队、甚至丢失（尤其跟在 SET 后面的 STOP）。
 *    现每轮把已到达的字节全部取走并按 '\n' 切分，且绝不解析半行。
 */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLE2902.h>
#include "toy_profiles.h"

// ===== 设备类型切换（编译期，默认体感设备 A）=====
// 体感设备 A:   TOY_PROFILE_JUMP_EGG （默认，现协议 0xFFE0/0xFFE1 + 0xA1 0x02 ... 报文）
// 体感设备 B: TOY_PROFILE_MALE_MST（预留：占位 UUID，编码未实现，逆向后填入 toy_profiles.cpp）
// 也可命令行覆盖：--build-property build.extra_flags=-DACTIVE_TOY_PROFILE=TOY_PROFILE_MALE_MST
#ifndef ACTIVE_TOY_PROFILE
#define ACTIVE_TOY_PROFILE TOY_PROFILE_JUMP_EGG
#endif
ToyProfileId g_active_profile_id = ACTIVE_TOY_PROFILE; // 活动 profile（当前=编译期选择）

#define HR_SERVICE_UUID "0000180D-0000-1000-8000-00805F9B34FB"
#define HR_CHAR_UUID    "00002A37-0000-1000-8000-00805F9B34FB"

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

// 串口行缓冲：非阻塞地累积字节，遇到 '\n' 才作为一条完整指令交给 handleSerialCommand
String serialBuf = "";

// ===== 时长自动停止（非阻塞，基于 millis） =====
unsigned long stopAtMillis = 0;   // 计划自动停止的时刻（毫秒）
bool autoStopActive = false;      // 是否有未完成的定时停止任务

// 返回 true 表示已真正写入玩具特征；false 表示玩具未连接或当前 profile 无字节可发
bool sendToToy(int intensity, int waveform) {
  if (!toyConnected || toyChar == nullptr) {
    Serial.println("⚠️ 玩具未连接，指令已记录");
    return false;
  }
  const ToyProfile *prof = toyActiveProfile();
  uint8_t buf[32];
  size_t len = (prof && prof->encode) ? prof->encode(intensity, waveform, buf, sizeof(buf)) : 0;
  if (len == 0 || len > sizeof(buf)) {
    // 预留 profile（如 MALE_MST）尚未实现编码时不发送任何字节
    Serial.printf("⚠️ 当前设备类型(%s)未生成指令字节（预留未实现/无动作），指令已忽略\n",
                  prof ? prof->name : "?");
    return false;
  }
  toyChar->writeValue(buf, len);
  Serial.print("📤 发送:");
  for (size_t i = 0; i < len; i++) Serial.printf(" %02X", buf[i]);
  Serial.println();
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
    intensity = constrain(intensity, 0, (int)toyActiveProfile()->max_intensity);
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
    const ToyProfile *prof = toyActiveProfile();
    if (prof && device.haveServiceUUID() &&
        device.isAdvertisingService(BLEUUID(prof->service_uuid))) {
      BLEDevice::getScan()->stop();
      toyDevice = new BLEAdvertisedDevice(device);
      doConnectToy = true;
    }
  }
};

// arduino-esp32 3.x 的通知回调签名
// BLE 标准心率测量特征 0x2A37 字节布局（按位标志）：
//   byte0  = flags：bit0=心率是否 16 位；bit4=是否携带 RR-Interval(RR 间隔)
//   byte1+ = 心率（1 或 2 字节，小端）
//   其后   = RR-Interval（2 字节，小端，单位 1/1024 秒）——仅当 bit4=1 时存在
// 注意：旧实现从 data[2] 取 IBI，在「心率 16 位」时与心率字节重叠，且用 len>=4 代替 bit4
// 判断 RR 是否存在，导致 IBI 恒为错误值（后端 HRV 基线会因此失效）。
static void hrNotify(BLERemoteCharacteristic* pBLERemoteCharacteristic, uint8_t* data, size_t len, bool isNotify) {
  if (len < 2) return;
  uint8_t flags = data[0];
  size_t idx = 1;
  int hr = 0;
  if (flags & 0x01) {                          // 心率 16 位（小端）
    if (len < idx + 2) return;
    hr = data[idx] | (data[idx + 1] << 8);
    idx += 2;
  } else {                                     // 心率 8 位
    hr = data[idx];
    idx += 1;
  }
  int ibi = 0;                                 // 0 = 本包无 RR-Interval（后端按「无 IBI」处理）
  if ((flags & 0x10) && len >= idx + 2) {      // RR-Interval present
    uint16_t rr = data[idx] | (data[idx + 1] << 8);
    ibi = (int)((float)rr * 1000.0f / 1024.0f); // 1/1024 秒 → 毫秒
  }
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
  const ToyProfile *prof = toyActiveProfile();
  if (!prof) return;
  if (toyClient == nullptr) toyClient = BLEDevice::createClient();
  if (!toyClient->connect(toyDevice)) {
    Serial.println("⚠️ 玩具连接失败，稍后重试");
    return;
  }
  auto svc = toyClient->getService(BLEUUID(prof->service_uuid));
  if (svc) {
    toyChar = svc->getCharacteristic(BLEUUID(prof->tx_uuid));
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
  Serial.setTimeout(50);  // 仍在其它同步读取路径上缩短阻塞等待
  BLEDevice::init("ESP32_Bridge");
  scanHR();
  scanToy();
}

// 断线检测：arduino-esp32 3.x 的 BLEClient 不会回调我们的标志位，必须自己轮询 isConnected()。
// 不复位的话，手环关机或玩具休眠后 hrConnected/toyConnected 永远为 true：
// 既不重连，也不再上报心率 —— 后端会以为「手环没数据」，HRV 与熔断层静默失效且无任何日志。
void maintainConnections() {
  if (hrConnected && (hrClient == nullptr || !hrClient->isConnected())) {
    hrConnected = false;
    hrChar = nullptr;
    Serial.println("⚠️ 手环连接已断开，稍后重连");
  }
  if (toyConnected && (toyClient == nullptr || !toyClient->isConnected())) {
    toyConnected = false;
    toyChar = nullptr;
    Serial.println("⚠️ 玩具连接已断开，稍后重连");
  }
}

// 非阻塞串口轮询：把本轮已到达的字节全部取走，按 '\n' 切分成完整指令。
// 旧实现每轮只处理一行，而 scanHR()/scanToy() 会阻塞 3 秒 + delay(1000)，
// 扫描窗口内到达的多条指令要排队好几轮，甚至因为 UART 缓冲有限而丢失
// （最危险的是紧跟在 SET 后面的 STOP）。
void pollSerial() {
  int budget = 512;   // 单轮最多消费 512 字节，防止指令洪水把 BLE 维护饿死
  while (budget-- > 0 && Serial.available() > 0) {
    int c = Serial.read();
    if (c < 0) break;
    if (c == '\n') {
      String line = serialBuf;
      serialBuf = "";
      handleSerialCommand(line);
    } else if (c != '\r') {
      if (serialBuf.length() < 128) serialBuf += (char)c;   // 畸形超长行直接截断，不吃满内存
    }
  }
}

void loop() {
  // 1) 每轮先检查定时停止，尽量准时
  checkAutoStop();

  // 2) BLE 连接维护（先剔除已掉线的连接，再尝试重连）
  maintainConnections();
  if (doConnectHR && !hrConnected) { connectHR(); doConnectHR = false; }
  if (!hrConnected && hrDevice) { scanHR(); delay(1000); }
  if (doConnectToy && !toyConnected) { connectToy(); doConnectToy = false; }
  if (!toyConnected && toyDevice) { scanToy(); delay(1000); }

  // 3) 串口指令解析（非阻塞，一轮可处理多条完整指令）
  pollSerial();

  delay(10);
}
