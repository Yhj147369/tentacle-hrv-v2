# ESP32（NodeMCU-32S）TTL 串口烧录指南 + 板子诊断结论

> 适用场景：ESP32 开发板插在本机 USB 上，**板子红色电源 LED 亮（供电正常），但 Windows 完全不枚举**（无 COM 口、无 CH340/CP210x/乐鑫设备、无未知设备）。
> 本文档给出一条**绕过板载 USB 转串口芯片**的烧录通路，并对板子本身给出判定结论。
> 编写日期：2026-09（本机实测工具链版本见下）。
> ⚠️ 本文档为方案文档，**未执行任何烧录、未安装任何驱动**。

---

## 0. 现场实测结论（先看这一段）

| 检查项 | 实测结果 | 影响 |
|---|---|---|
| 串口设备 | `Get-CimInstance Win32_SerialPort` → **空**；`Get-PnpDevice -Class Ports` → **空** | 当前**没有任何 COM 口**，arduino-cli 无法按端口工作 |
| 烧录工具 | `esptool.exe` **v5.3.1** 可用（Arduino15 esp32 core 3.3.11 自带） | ✅ 可以直接命令行直烧，不需要 COM 枚举以外的任何东西 |
| 编译产物 | **全部齐备**，含 `esp32_firmware.ino.merged.bin`（4 MB） | ✅ **无需重新编译**，一条命令即可烧录 |
| CH340/CH343 驱动 | **已在系统 DriverStore 中**：`oem93.inf`(ch341ser.inf 3.5.2019.1)、`oem94.inf`(ch343ser.inf 1.4.2021.4) | ✅ 插上 CH340/CH343 模块**应当自动出现 COM 口，无需联网下载、无需管理员安装** |
| CP210x 驱动 | DriverStore 中**没有** CP210x 驱动包 | ❌ **不要买 CP2102 模块**，除非自备驱动；优先选 **CH340/CH343** |
| 该机 USB 历史 | setupapi.dev.log 与 PnP 幽灵记录中**从未出现过** CH340/CP210x/FTDI/ESP32 原生 USB（VID_1A86 / VID_10C4 / VID_303A / VID_0403） | ⚠️ 该板 USB 通路**在本机从未被识别过**，强烈指向“数据线不通”或“板子 USB 部分损坏” |

> 系统安装日期为 2022-10-01，**不是刚装的系统**，所以“历史上从未出现”这一条参考价值很高。

**行动建议一句话**：先买一个 **CH340/CH343 的 USB-TTL 模块**（3.3V 逻辑档），按第 3 节接线、按第 5 节用 esptool 一条命令烧 `merged.bin`；同时在另一台电脑上插一次原 USB 线做交叉验证（见第 9 节）。

---

## 1. 本机工具链确认结果（已验证）

### 1.1 esptool

```text
C:\Users\Administrator\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe
长度 14,323,136 字节
```

实测：

```powershell
& "C:\Users\Administrator\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe" version
# 输出： esptool v5.3.1
```

⚠️ **注意：esptool 5.x 没有 `--version` 选项**，用 `version` 子命令（写 `--version` 会报 `No such option '--version'`）。
⚠️ 5.x 命令名改为**连字符**风格：`image-info`、`write-flash`、`read-flash`、`erase-flash`、`merge-bin`。旧的 `image_info` 会提示 Deprecated。

### 1.2 arduino-cli

```powershell
& "C:\Users\Administrator\Desktop\tentacle-hrv\arduino-cli.exe" version
# arduino-cli  Version: 1.5.2-rc.1 Commit: fef6e48df Date: 2026-07-23T11:13:25Z

& "...\arduino-cli.exe" core list
# esp32:esp32 3.3.11  3.3.11  esp32      ← core 已安装

& "...\arduino-cli.exe" board list
# No boards found.                       ← 当前无任何可用端口（与 USB 不枚举一致）
```

### 1.3 固件产物（**已存在，无需编译**）

目录：`C:\Users\Administrator\Desktop\tentacle-hrv\esp32_firmware\build\`

| 文件 | 大小（字节） | SHA256（前 16 位） | 烧录偏移 |
|---|---:|---|---|
| `esp32_firmware.ino.merged.bin` | 4,194,304 | A81F756C540AAC1E… | **0x0（推荐，一条命令）** |
| `esp32_firmware.ino.bootloader.bin` | 24,992 | 427F96E10C620C4F… | 0x1000 |
| `esp32_firmware.ino.partitions.bin` | 3,072 | 148B959CBFF1C38A… | 0x8000 |
| `boot_app0.bin` | 8,192 | F94C5D786A7A8FAB… | 0xE000 |
| `esp32_firmware.ino.bin` | 1,099,024 | 4D62E207E2B9C50C… | 0x10000 |

**产物自检（已做，只读）**：

- `build\flash_args` 内容为：
  ```
  --flash-mode dio --flash-freq 80m --flash-size 4MB
  0x1000 esp32_firmware.ino.bootloader.bin
  0x8000 esp32_firmware.ino.partitions.bin
  0xe000 boot_app0.bin
  0x10000 esp32_firmware.ino.bin
  ```
- 各文件头部魔数正确：bootloader `E9 03 02 2F`、partitions `AA 50 01 02`、app `E9 06 02 2F`（ESP32 镜像魔数 `0xE9`，分区表魔数 `0xAA50`）。
- `merged.bin` 在关键偏移处的内容与单独文件**完全一致**：`0x1000=E9 03 02 2F`、`0x8000=AA 50 01 02`、`0xE000=01 00 00 00`、`0x10000=E9 06 02 2F` → **merged.bin 可直接写 0x0 使用**。
- `esptool image-info esp32_firmware.ino.bin` 结果：Image 校验 `Checksum: 0x04 (valid)`、`Validation hash: valid`、Flash size 4MB / 80m / DIO、ESP-IDF v5.5.5 → **镜像本身健康**。
- 分区表 `build\partitions.csv`：
  ```
  nvs      0x009000  0x5000
  otadata  0x00E000  0x2000
  app0     0x010000  0x140000
  app1     0x015000  0x140000
  spiffs   0x290000  0x160000
  coredump 0x3F0000  0x10000
  ```
- FQBN（来自 `build\build.options.json`）：`esp32:esp32:esp32`
- 编译配置：`platformio.ini` 为 `board = esp32dev / monitor_speed = 115200`；固件 `setup()` 中 `Serial.begin(115200)`。

**只在产物缺失时才需要重新编译**（本机产物齐全，**不必执行**）：

```powershell
cd C:\Users\Administrator\Desktop\tentacle-hrv
.\arduino-cli.exe compile --fqbn esp32:esp32:esp32 --output-dir .\esp32_firmware\build .\esp32_firmware
```

---

## 2. 需要准备的硬件

| 物料 | 要求 | 备注 |
|---|---|---|
| USB-TTL 串口模块 | **CH340 / CH343**（本机驱动已在 DriverStore，插上即认） | ⚠️ **不推荐 CP2102**：本机没有 CP210x 驱动包 |
| 杜邦线 | 母-母 3 根（**至少 GND、TX、RX**） | 长度 ≤ 20 cm 更稳 |
| 电源 | 板子继续用 USB 线供电（红色 LED 亮即可），**TTL 模块不需要再给 3V3** | 见下方供电铁律 |
| 可选 | 万用表 / 另一台电脑 | 用于交叉验证板子 USB 通路 |

### ⚠️ 供电铁律（接错可能烧板）

1. **ESP32 是 3.3V 逻辑**。TTL 模块若有 5V/3.3V 跳线，**拨到 3.3V** 一侧（只影响 TX 电平，安全性更好）。
2. **绝不要把 5V 接到板子的 3V3 引脚**。
3. **板子已由 USB 供电时，不要再接 TTL 的 3V3 或 5V**——两个电源同时灌入可能损坏 LDO 或 USB 芯片。只接 **GND + TX + RX** 三根线即可。
4. NodeMCU-32S 的 VIN 脚可喂 5V，但只有在**不用 USB 供电**时才用它。
5. TTL 模块与板子**必须共地（GND 对接）**，否则串口数据全是乱码。

---

## 3. 接线表（TTL 模块 ↔ NodeMCU-32S）

| TTL 模块引脚 | 接到板子 | 板子丝印 / GPIO | 说明 |
|---|---|---|---|
| **TXD** | → | **RX / RXD0 / U0RXD（GPIO3）** | ⚠️ **交叉对接**：TTL 的 TX 进板子的 RX |
| **RXD** | ← | **TX / TXD0 / U0TXD（GPIO1）** | ⚠️ TTL 的 RX 接板子的 TX |
| **GND** | ↔ | **GND** | 必须接，否则乱码 |
| 3V3（可选） | — | **不接**（板子已由 USB 供电） | 仅在无 USB 供电时才接 3V3 |
| 5V | — | **永远不接** | 见供电铁律 |

最简记忆法：**TX↔RX 交叉，GND↔GND，外加一根都不接。**

> 排错技巧：如果 esptool 报连接超时，**第一件事就是把 TX/RX 两根线对调**（这是最常见的错误）。

---

## 4. 进入下载模式（Download Mode）

大多数 NodeMCU-32S 板载 USB 芯片时支持 DTR/RTS 自动下载，但**你现在走的是 TTL 手动通路，必须手动进下载模式**：

1. 板子通电（USB 供电，红灯亮）。
2. **按住 BOOT 键（丝印 BOOT 或 IO0，即 GPIO0）**，不要松。
3. 保持按住 BOOT，**点按一下 EN（丝印 EN 或 RST）**，然后松开 EN。
4. **再松开 BOOT**。
5. 此时板子停在 ROM 下载模式，等待串口命令。

要点：

- 顺序必须是 **BOOT 按住 → 点 RST → 松 BOOT**；先松 BOOT 会直接启动原固件，导致 esptool 连不上。
- 下载模式**没有视觉指示**（LED 不会变），不要以 LED 判断。
- 手动进下载模式后，**不要给板子断电、不要按 RST**，直到 esptool 烧完并自己复位。
- 如果 TTL 模块有 DTR/RTS 引脚且你的板子引出，也可以 `DTR→EN / RTS→BOOT` 实现自动下载，但手动按键最可靠。

---

## 5. 烧录方式 A：esptool 直烧（**推荐**）

原因：arduino-cli 的 `upload` 必须先按端口枚举出板子；esptool 只要有一个 COM 口就能烧，**不依赖板子的自动识别与 DTR/RTS 握手**。

### 5.1 先拿到 COM 口

插上 TTL 模块（**不要**接板子也能先测：只插 TTL，看有没有 COM 口），然后：

```powershell
# 方法一
[System.IO.Ports.SerialPort]::GetPortNames()

# 方法二（更详细，能看到是哪个芯片）
Get-PnpDevice -Class Ports -PresentOnly | Select-Object Status, FriendlyName, InstanceId

# 方法三
& "C:\Users\Administrator\Desktop\tentacle-hrv\arduino-cli.exe" board list
```

记下形如 **`COM5`** 的端口号，下文用 `COMx` 表示。

### 5.2 方式 A-1：一条命令烧 merged.bin（**最推荐**）

```powershell
$esp = "C:\Users\Administrator\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe"
$b   = "C:\Users\Administrator\Desktop\tentacle-hrv\esp32_firmware\build"

# 1) 先确认芯片能被识别（不写入任何东西，只读）
& $esp --chip esp32 --port COMx --baud 115200 chip-id

# 2) 擦除整片 Flash（可选；换了别人的板子/前次异常时才需要）
& $esp --chip esp32 --port COMx --baud 115200 erase-flash

# 3) 一把烧完整包（4MB 带 0xFF 填充，写 0x0）
& $esp --chip esp32 --port COMx --baud 921600 write-flash -z `
    --flash-mode dio --flash-freq 80m --flash-size 4MB `
    0x0 "$b\esp32_firmware.ino.merged.bin"
```

烧录成功会看到 `Hash of data verified.` 与 `Hard resetting via RTS pin...`。

### 5.3 方式 A-2：按分区逐个写（适合只想更新 app）

```powershell
$esp = "C:\Users\Administrator\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe"
$b   = "C:\Users\Administrator\Desktop\tentacle-hrv\esp32_firmware\build"

& $esp --chip esp32 --port COMx --baud 921600 write-flash -z `
    --flash-mode dio --flash-freq 80m --flash-size 4MB `
    0x1000  "$b\esp32_firmware.ino.bootloader.bin" `
    0x8000  "$b\esp32_firmware.ino.partitions.bin" `
    0xE000  "$b\boot_app0.bin" `
    0x10000 "$b\esp32_firmware.ino.bin"
```

> 偏移与 `build\flash_args` 完全一致。`write-flash` 支持一次给多组 `地址 文件`。
> 只有 `esp32_firmware.ino.bin`（app）编译后才变化，日常改代码只需烧 `0x10000` 这一个。

### 5.4 其他有用的只读/维护命令

```powershell
& $esp --chip esp32 --port COMx --baud 115200 flash-id          # 看 Flash 型号容量
& $esp --chip esp32 --port COMx --baud 115200 read-mac          # 读 MAC（可确认是不是同一块板）
& $esp --chip esp32 --port COMx --baud 115200 read-flash 0x8000 0x30 pt.bin   # 读回分区表自检
& $esp image-info "$b\esp32_firmware.ino.bin"                   # 本地校验镜像（不需要板子）
& $esp --chip esp32 --port COMx --baud 115200 verify-flash 0x10000 "$b\esp32_firmware.ino.bin"  # 烧后校验
```

### 5.5 常见坑：中文/emoji 乱码

固件里有 `⚠️`、`✅` 等字符。若串口输出乱码或 esptool 报编码错，先切 UTF-8 代码页：

```powershell
chcp 65001
```

---

## 6. 烧录方式 B：arduino-cli upload（有前提）

**前提**：Windows 已经认到 **TTL 模块自己的 COM 口**（本机 CH340/CH343 驱动已就位，插上通常即出现），并且你已经按第 4 节手动让板子进入下载模式。

```powershell
cd C:\Users\Administrator\Desktop\tentacle-hrv
.\arduino-cli.exe board list                      # 先确认端口与识别结果
.\arduino-cli.exe upload -p COMx --fqbn esp32:esp32:esp32 .\esp32_firmware
```

说明与限制：

- `-p COMx` 必须是 **TTL 模块**的端口（不是板子的，板子的根本不存在）。
- 若 `board list` 里端口显示为 “Unknown”，仍然可以 upload，只要 `-p` 写对端口号。
- arduino-cli 会自己调 esptool 完成烧录；**烧录偏移与选项由 core 自动带出**，不用手写。
- 你的手动下载模式会被 arduino-cli 的 DTR/RTS 复位动作打断的风险较低（纯 TTL 模块一般没有 DTR/RTS 线接板子），但**如果 upload 报 “Failed to connect … Wrong boot mode”，按住 BOOT 再重跑一次**。
- 只想用 arduino-cli 编译、用 esptool 烧录，是更稳的组合。

---

## 7. 烧录后验证

### 7.1 打开串口看日志（115200）

固件 `setup()` 里是 `Serial.begin(115200)`，所以监视器必须 115200。

**用 Arduino 串口监视器**：选 TTL 模块的 COM 口，波特率 **115200**，换行符用 “NL 或 两者”。

**或纯 PowerShell（推荐，无需装任何东西）** — 先让板子进下载模式烧完，然后复位板子（点一下 EN），再执行：

```powershell
$p = New-Object System.IO.Ports.SerialPort COMx,115200,None,8,one
$p.Encoding = [System.Text.Encoding]::UTF8
$p.ReadTimeout = 500
$p.Open()
$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 30) {
  try { $line = $p.ReadLine(); if ($line) { Write-Output $line } } catch { }
}
$p.Close()
```

### 7.2 怎么确认“固件确实在运行”

本固件会打印这些**确定性标志**（源码实测行号）：

| 输出 | 出处 | 含义 |
|---|---|---|
| `HR:xx,IBI:yy` | `esp32_firmware.ino:245` | ✅ **最硬证据**：心率手环已连上并收到心率通知 |
| `✅ 手环已连接` | 行 260 | 心率手环连接成功 |
| `✅ 玩具已连接` | 行 278 | 体感反馈设备连接成功 |
| `⚠️ 手环连接失败，稍后重试` | 行 251 | 固件在跑，但手环没连上（BLE 未发现/未开机） |
| `⚠️ 玩具未连接，指令已记录` | 行 83 | 固件在跑，等你发指令 |
| `📤 发送: XX XX` | 行 96 | 收到了上位机下发的指令字节 |
| `⚠️ 未知波形: …` | 行 181 | 指令参数不合法 |
| `✅ 执行: 强度N 时长N秒 波形N` | 行 188 | 指令解析成功 |

**判定标准（按强弱排序）**：

1. **串口 115200 能读到任意一行上述文本** → 固件已烧入并正在运行。**注意：本固件在 `setup()` 里没有打印 “启动/版本” 之类的 banner**（只做 `Serial.begin` + `Serial.setTimeout(50)` + `BLEDevice::init("ESP32_Bridge")` + 两次 BLE 扫描），所以**如果手环不在旁边，可能“看不到任何输出”却仍是正常运行**——这一点不要误判为失败。
2. 若手环已开机佩戴：应看到 `✅ 手环已连接` 然后持续 `HR:xx,IBI:yy`。**这是最可靠的运行证据**。
3. 想主动要输出：用串口发一条指令（项目支持的格式见 `handleSerialCommand`，如 `SET 强度 时长 波形`），应得到 `✅ 执行: …` 或 `⚠️ 玩具未连接，指令已记录`。
4. 想验证 BLE 侧是否真的活着：手机用 nRF Connect 扫描，应能扫到广播名 **`ESP32_Bridge`**（`BLEDevice::init("ESP32_Bridge")`）。
5. 校验一致性：`read-mac` 记录 MAC，下次插拔后比对是否为同一块板。

---

## 8. 故障排查表

| 现象 | 最可能原因 | 处理 |
|---|---|---|
| **没有 COM 口** | TTL 模块驱动未装 / 线只充电不传数据 / TTL 模块坏 | 本机 CH340+CH343 驱动已在 DriverStore，插上 CH340 应自动出 COM；仍无则换 TTL 模块或换 USB 口、换数据线。**不要买 CP2102**（本机无其驱动） |
| TTL 有 COM，但 `read-flash`/`write-flash` 报 **连接超时 / Failed to connect** | ① TX/RX 接反 ② 没进下载模式 ③ 波特率过高 ④ 未共地 | ① **先对调 TX/RX** ② 重做 BOOT+RST 序列 ③ 把 `--baud` 降到 **115200** ④ 确认 GND 已接 |
| 报 `Wrong boot mode detected (0x3)` / `Invalid head of packet` | 板子跑在正常启动模式而非下载模式 | 重做进下载模式：按住 BOOT → 点 EN → 松 BOOT，然后立刻重跑 esptool |
| esptool 报 **`No such option '--version'`** | esptool 5.x 语法变化 | 用 `esptool version`；命令名用连字符（`image-info` 而非 `image_info`） |
| 报 `This is not a valid image (invalid magic number: 0xff)` | 对 `merged.bin` 整体跑 `image-info` | 正常现象：整包头部是 0xFF 填充。改对 `esp32_firmware.ino.bin` 或 `.bootloader.bin` 单独做 `image-info` |
| 烧到一半失败 / 校验不过 | 波特率过高、线太长、接触不良 | 降到 `--baud 115200` 重烧；缩短杜邦线；必要时先 `erase-flash` |
| **烧录成功但串口无输出** | ① 供电不足/未复位 ② 波特率不是 115200 ③ 板子还停在下载模式 ④ 固件正常但手环不在旁（本固件无启动 banner） | ① 点 EN 复位或重新插 USB ② 监视器设 115200 ③ 按 EN 退出下载模式 ④ 用手机 nRF Connect 扫广播名 `ESP32_Bridge` 确认 BLE 活着 |
| 输出**全是乱码** | 波特率不匹配 / 未共地 / 时钟不匹配 | 确认 115200 与 GND；`chcp 65001` |
| `arduino-cli board list` 显示 **No boards found** | 正常——当前无端口 | 先按 5.1 拿到 TTL 的 COM 口，`upload` 时直接 `-p` 指定即可 |
| 串口输出里反复出现 `⚠️ 手环连接失败，稍后重试` | 固件正常，但心率手环没开/没广播 HR 服务/被别的设备占用 | 这是**固件在正常工作的证据**；检查手环开机、取掉手机 App 的抢占连接 |
| 板子插上后**仍然完全不枚举**（TTL 通路除外） | 板载 USB 芯片/U 座故障 | 见第 9 节诊断结论 |

---

## 9. 板子诊断结论（LED 亮但不枚举）

### 9.1 实测证据链

| 证据 | 结果 | 推论 |
|---|---|---|
| 红色电源 LED 亮 | 亮 | 5V 供电通路**正常**（LED 只吃电，不证明数据通路） |
| `Win32_SerialPort` / `Get-PnpDevice -Class Ports` | 全空 | **零**个 COM 口 |
| PnP 中 CH340/CP210x/乐鑫设备 | 无（连“未知设备/带感叹号设备”都没有） | Windows **连枚举都没发生**，不是“驱动缺失”问题 |
| `-PresentOnly` USB 设备清单 | 只有摄像头(1908:2311)、键盘鼠标(3151/25A7/1C4F…) | 板子**没有占用任何 USB 地址** |
| setupapi.dev.log 历史 | 从未出现 `VID_1A86/VID_10C4/VID_303A/FTDI` | 该板 USB 通路在本机**从未成功枚举过** |
| 系统安装日期 | 2022-10-01（非新系统） | 排除“新装系统还没插过”的解释 |
| 已试 2 根线 + 2 个 USB 口 | 均无反应 | 缩小到线材 / 板子 |

### 9.2 最可能原因排序

1. **数据线是“充电线”或劣质线（USB 线只有 VBUS+GND，D+/D- 未接或断裂）**——最常见，且**完全符合**“红灯亮但不枚举”。
2. **USB 口数据针脚接触不良**——USB-A 口的 D+/D- 弹片被压塌、氧化；或用了 USB3.0 蓝色口上的劣质延长线/HUB。
3. **板载 USB-串口芯片（CH340/CP2102）损坏或被静电打死**——供电仍能过，数据侧全死。
4. **USB 座虚焊/脱焊**——插线时手感松动、轻微晃动能瞬时识别（如果晃动能识别，基本确诊）。
5. 板载 USB 芯片的 **D+/D- 上拉电阻、或 ESD 器件损坏**。
6. （可能性较低）主机 USB 控制器/供电异常——但同机其他 USB 设备（摄像头、键鼠）工作正常，基本可排除。

> 注意：ESP32（经典款）**没有原生 USB**，它必须靠板载桥接芯片。所以桥接芯片一坏，就只剩 TTL 通路——这正是本指南的价值。

### 9.3 用户可自行验证的最小实验（按顺序做，10 分钟内出结论）

| # | 实验 | 判定 |
|---|---|---|
| 1 | **换到另一台电脑**，用**同一根线**插同一块板 | 另一台也完全不识别 → **板子 USB 通路（线或芯片）故障**；另一台能识别 → 问题在**本机**（USB 口/供电/策略） |
| 2 | 用**同一根线 + 另一台已知好用的设备**（手机/移动硬盘/读卡器）在本机插 | 好用 → 线是数据线、本机口也 OK → 问题在**板子**；不好用 → **线或本机口的问题** |
| 3 | 换一根**确认能传数据**的线（例如手机连电脑能传文件的那根） | 立刻识别 → 原来是**充电线** |
| 4 | 插上板的瞬间，另开一个 PowerShell 跑 `Get-PnpDevice -PresentOnly | Where-Object InstanceId -like 'USB\*'` 对比差异 | 有**任何**一次新增/抖动 → 说明 D+/D- 部分连通（接触不良/虚焊）；**零变化** → 数据线或芯片全断 |
| 5 | 用小台灯/万用表看 USB-A 的 D+/D- 是否导通（相对 GND 应有约 1.5kΩ 上拉特征，或万用表二极管档对比好线） | 断线可确诊是线 |
| 6 | 手指轻压/晃动 USB 座同时观察设备管理器 | 能闪现 COM/设备 → **USB 座虚焊**，补焊可救 |

### 9.4 结论与建议

- **无论实验 1 结果如何，TTL 通路都值得做**：它是唯一能绕过板载 USB 芯片的验证与烧录通路，能让你在不动板子的前提下确认“ESP32 主芯片本身是好的”。
- 若**实验 1 显示另一台电脑能识别**：说明板子完好，问题在本机 USB 口或线，优先换线/换口（尤其避开前置面板与 HUB，直插主板后置 USB 2.0 口）。
- 若**实验 1 也识别不了**：判定为 **板子（或线）USB 通路故障**。路线二选一：
  - **A. 走 TTL**（本指南第 3~7 节）：只要 ESP32 主控和 3V3 供电正常，TTL 就能正常烧录与看日志，继续用这块板做项目联调。
  - **B. 换板**：若 TTL 也连不上（说明不止 USB 侧有问题），换一块 ESP32 开发板。买板时优先选板载 **CH340/CH343** 的型号（与本机已有驱动匹配），**避开 CP2102**。
- **临时可行性**：即使板子 USB 完全不枚举，只要 TTL 通路打通，本项目（BLE 桥接 + `HR:xx,IBI:yy` 上报 + 串口指令）的功能验证**不受影响**——因为固件本身不依赖 USB，只依赖 3V3 供电与串口调试通路。

---

## 10. 一页速查（照抄即可）

```powershell
# 0) 编码与路径
chcp 65001
$esp = "C:\Users\Administrator\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe"
$b   = "C:\Users\Administrator\Desktop\tentacle-hrv\esp32_firmware\build"

# 1) 确认 TTL 模块的 COM 口
[System.IO.Ports.SerialPort]::GetPortNames()

# 2) 手动进下载模式：按住 BOOT → 点 EN → 松 BOOT
# 3) 只读握手测试
& $esp --chip esp32 --port COMx --baud 115200 chip-id

# 4) 一把烧完整包（推荐）
& $esp --chip esp32 --port COMx --baud 921600 write-flash -z --flash-mode dio --flash-freq 80m --flash-size 4MB 0x0 "$b\esp32_firmware.ino.merged.bin"

# 5) 复位板子（点一下 EN），用 115200 看日志，找 "HR:xx,IBI:yy" 或 "✅ 手环已连接"
```

---

## 附：相关文件与命令

- 烧录源目录：`C:\Users\Administrator\Desktop\tentacle-hrv\esp32_firmware\build\`
- 固件源码：`esp32_firmware.ino`（`Serial.begin(115200)`、`BLEDevice::init("ESP32_Bridge")`、`HR:%d,IBI:%d`）
- 编译配置：`build\build.options.json`（FQBN `esp32:esp32:esp32`）、`build\flash_args`（偏移与 flash 选项）、`build\partitions.csv`（分区布局）
- 项目 PlatformIO 备用配置：`platformio.ini`（`board = esp32dev`，`monitor_speed = 115200`）
- 备用烧录栈：`C:\Users\Administrator\.platformio\packages\tool-esptoolpy\`（源码形式，需 Python；本机已有 Arduino15 的 `esptool.exe`，**优先用后者**）
