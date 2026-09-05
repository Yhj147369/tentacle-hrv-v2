# Tentacle HRV — 多模态 AI 闭环体感控制系统

基于 DeepSeek 多模态 API，整合 **平板摄像头拍照 + 心率手环 BLE 数据 + 网页控制面板 + ESP32 蓝牙中转 + 蓝牙玩具反馈** 的实时闭环控制系统。

> **本项目采用 DLC 模块化架构**，核心系统负责基础闭环，九个 DLC 分别扩展不同能力，可独立启用或禁用。

## 📦 DLC 模块总览

| DLC | 名称 | 功能 | 依赖 |
|-----|------|------|------|
| 核心 | 基础闭环 | 摄像头 + 心率 + AI 决策 + 指令下发 | 必选 |
| DLC1 | AI 语音朗读 | TTS 语音合成，AI 回复自动朗读 | edge-tts |
| DLC3 | 随机事件系统 | 弹窗事件 + 玩家选项 + 心率联动策略 | events.json |
| DLC4 | 手动控制模式 | 网页端滑块调节强度/时长/波形，手动发送指令 | 无 |
| DLC5 | 自定义角色接口 | 网页端粘贴 SYSTEM_PROMPT，自由创造角色 | 无 |
| DLC6 | 对话记忆增强 | AI 记住近期互动历史，角色更连贯 | 无 |
| DLC7 | 数据可视化面板 | 心率曲线 + 指令强度柱状图 | Chart.js |
| DLC8 | 声音氛围系统 | 根据心率自动生成背景音效 | Web Audio API |
| DLC9 | Web 配置后台 | 网页端修改安全阈值、重载事件池 | 无 |

> **注**：DLC2 未使用，编号从 DLC1 跳到 DLC3 是有意设计。

## ✨ 核心功能特性

- 📷 平板摄像头每 3 秒拍照上传，AI 实时分析画面
- ❤️ 通过 ESP32 读取心率手环数据（HR/IBI），支持心率熔断与限幅
- 🧠 DeepSeek 多模态模型决策，输出 `SET 强度 时长 波形` 或 `STOP` 指令
- 🛡️ 安全策略：心率 >130 强制 STOP，110~130 限幅 50%，图片超时暂停 AI

## 🎮 DLC 详细说明

### DLC1 — AI 语音朗读
- 勾选后，AI 回复自动朗读（中文女声，可换音色）
- 依赖 `edge-tts`，需联网

### DLC3 — 随机事件系统
- AI 适时触发事件，弹窗展示，限时选择
- 玩家选择直接作为行动，影响后续剧情
- 心率 >130 仅允许放松事件；110~130 有 80% 概率替换为放松事件
- 事件池由 `events.json` 配置，可自行增删

### DLC4 — 手动控制模式
- 网页端滑块调节强度（0-100）、时长（1-60秒）、波形
- 一键发送 SET / STOP 指令

### DLC5 — 自定义角色接口
- 网页端粘贴自定义 SYSTEM_PROMPT，保存后 AI 换人设
- 留空保存即可恢复默认角色

### DLC6 — 对话记忆增强
- 后端维护最近 20 条交互记录
- 每次调用 AI 时注入最近 5 条历史，增强连贯性

### DLC7 — 数据可视化面板
- 实时心率折线图
- 指令强度柱状图
- 记录最近 200 个数据点

### DLC8 — 声音氛围系统
- 根据心率自动生成不同频率和音量的背景音效
- 心率正常低频柔和，偏高紧张，危险高频急促
- 勾选「氛围音效」开启，纯浏览器端实现

### DLC9 — Web 配置后台
- 网页端修改心率熔断阈值、限幅阈值、强度上限等
- 可重新加载事件池
- 运行时生效，重启后恢复默认（当前未持久化）

## 🔧 硬件需求

| 设备 | 说明 |
|------|------|
| 平板（Android/iOS） | 用于拍照、录音，浏览器需支持 WebRTC |
| ESP32 开发板（NodeMCU-32S） | 蓝牙中转：接收心率广播 + 控制玩具 |
| 心率手环 | 支持标准 BLE 心率服务 `0x180D` |
| 蓝牙玩具 | 支持 BLE 控制（需自行逆向协议） |
| 电脑 | 运行 Flask 后端，需 Python 3.10+ |

## 📦 软件依赖

```bash
pip install -r requirements.txt
```

| 依赖 | 用途 | 所属 DLC |
|------|------|----------|
| flask / flask-httpauth | 后端服务与认证 | 核心 |
| opencv-python / numpy | 图像处理 | 核心 |
| requests / python-dotenv | API 调用与环境变量 | 核心 |
| pyserial | 串口通信（ESP32） | 核心 |
| vosk / pydub | 语音识别 | 核心 |
| edge-tts | AI 语音朗读 | DLC1 |

## 🚀 快速开始

### 1. 配置环境变量

复制 `.env.example` 为 `.env`（或手动创建），填写以下内容：

```ini
DEEPSEEK_API_KEY=你的DeepSeek密钥
DEEPSEEK_MODEL=deepseek-v4-flash-vision-exp
DEEPSEEK_BASE_URL=https://api.deepseek.com
SERIAL_PORT=COM3
SERIAL_BAUD=115200
INTERVAL_SECONDS=3
```

### 2. 启动后端服务

```bash
cd tentacle-hrv
python server.py --port 8080
```

### 3. 启动内网穿透（可选，供平板外网访问）

```bash
ssh -o StrictHostKeyChecking=no -R 0:127.0.0.1:8080 ClothoUseServerInforaqo17875@v2.i996.me -p 8222
```

### 4. 打开控制页面

- 本机：`http://127.0.0.1:8080/?key=123456`
- 平板：`https://你的域名/?key=123456`

## 🎮 使用说明

### 核心操作

1. 点击「启动摄像头」
2. 确保心率手环与 ESP32 连接
3. AI 自动决策并下发指令

### DLC1 AI语音朗读

- 勾选「AI语音朗读」复选框即可

### DLC3 随机事件

- 事件触发时弹窗显示选项，点击选择或等待超时随机

### DLC4 手动控制

- 在手动控制区域调滑块，点击发送

### DLC5 自定义角色

- 点击「编辑角色设定」粘贴 SYSTEM_PROMPT，保存

### DLC7 数据可视化

- 图表自动显示在页面中

### DLC8 氛围音效

- 勾选「氛围音效」开启

### DLC9 管理后台

- 点击「⚙️ 管理后台」修改阈值

## 🔌 ESP32 固件

固件位于 `esp32_firmware/esp32_firmware.ino`，使用 Arduino IDE 烧录。  
需自行填写玩具的 BLE 服务 UUID 和特征 UUID（在 `sendToToy()` 函数中），并确认心率手环的连接参数。

## ⚠️ 安全注意事项

- 不要将 `.env`、`certs/`、`models/`、`ffmpeg.exe` 提交到 Git
- 心率超过 130 自动强制 STOP（可在 DLC9 管理后台修改）
- 蓝牙玩具物理强度上限已在固件中限制为 60%
- DLC3 随机事件在心率偏高时自动降级为放松事件

## 📚 常见问题

### 平板提示无法访问摄像头？

必须使用 HTTPS 访问。

### 语音识别不可用？

检查 vosk、pydub、ffmpeg 是否安装，模型是否放在正确路径。

### AI 语音朗读没有声音？

确保已安装 edge-tts 并联网，勾选「AI语音朗读」。

### 管理后台修改的阈值没生效？

运行时生效，重启后恢复默认（DLC9 当前未持久化）。

## 📄 许可证

GPL-3.0，详见 LICENSE。

## 🙏 致谢

- 原作者：ra1nyxin/tentacle-monster-roleplay-esp32
- DeepSeek API / Vosk / edge-tts / Chart.js
