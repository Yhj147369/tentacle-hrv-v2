# Tentacle HRV — 多模态 AI 闭环体感控制系统

基于 DeepSeek 多模态 API，整合 **平板摄像头拍照 + 心率手环 BLE 数据 + 网页控制面板 + ESP32 蓝牙中转 + 蓝牙玩具反馈** 的实时闭环控制系统。  
核心创新在 **输入端**：增加心率/HRV 实时监测和语音情绪识别（可选）。

## ✨ 功能特性

- 📷 平板摄像头每 3 秒拍照上传，AI 实时分析画面
- ❤️ 通过 ESP32 读取心率手环数据（HR/IBI），支持心率熔断与限幅
- 🧠 DeepSeek 多模态模型决策，输出 `SET 强度 时长 波形` 或 `STOP` 指令
- 🔊 语音识别（Vosk 中文模型）与音频特征提取（音量、音调、音量变化率）
- 🗣️ AI 语音朗读（TTS），支持勾选开启，自动朗读 AI 回复
- 🎲 随机事件与剧情分支系统，玩家可通过弹窗选择行动，影响剧情走向
- 🎛️ 手动控制模式，网页端直接发送 SET/STOP 指令，可调强度、时长、波形
- 🎭 自定义角色接口，玩家可粘贴自己的 SYSTEM_PROMPT，随时切换人设
- 🎛️ 内网穿透支持，平板可通过 HTTPS 远程访问
- 🛡️ 安全策略：心率 >130 强制 STOP，110~130 限幅 50%，图片超时暂停 AI


- ## 🔧 硬件需求

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

额外可选依赖（用于语音识别）：
- `vosk`、`pydub`、`ffmpeg`（已包含在 `requirements.txt`，但需手动下载模型）

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
IMAGE_MAX_WIDTH=640
IMAGE_STALE_TIMEOUT=10
```

### 2. 启动后端服务

```bash
cd tentacle-hrv
python server.py --port 8080
```

看到 `服务启动: http://0.0.0.0:8080/` 即表示成功。

### 3. 启动内网穿透（可选，供平板外网访问）

使用 i996 或其他隧道工具，将本地 8080 端口映射到公网 HTTPS。  
示例（i996）：

```bash
ssh -o StrictHostKeyChecking=no -R 0:127.0.0.1:8080 ClothoUseServerInforaqo17875@v2.i996.me -p 8222
```

平板访问 i996 提供的固定域名即可。

### 4. 打开平板控制页面

- 本机测试：`http://127.0.0.1:8080`  
- 平板访问：`https://你的域名`（需 HTTPS）

登录账号：`admin` / `123456`（可在 `server.py` 中修改）

## 🎮 使用说明

1. 平板打开控制页面，点击「启动摄像头」
2. 确保心率手环已与 ESP32 连接，ESP32 通过串口上报 `HR:xx,IBI:yy`
3. AI 每 3 秒根据最新画面和心率数据自动决策，下发控制指令
4. 如需语音输入，确保平板麦克风开启，并已下载 Vosk 中文模型放入 `models/vosk-model-cn-0.22`
5. 随时点击「停止」可关闭摄像头和音频

## 🔌 ESP32 固件

固件位于 `esp32_firmware/esp32_firmware.ino`，使用 Arduino IDE 烧录。  
需自行填写玩具的 BLE 服务 UUID 和特征 UUID（在 `sendToToy()` 函数中），并确认心率手环的连接参数。

## ⚠️ 安全注意事项

- **不要将 `.env`、`certs/`、`models/`、`ffmpeg.exe` 提交到 Git**（已通过 `.gitignore` 忽略）
- 心率超过 130 会自动强制 STOP，请根据个人情况调整阈值
- 蓝牙玩具物理强度上限已在固件中限制为 60%

## 📚 常见问题

### 平板提示无法访问摄像头？

必须使用 HTTPS 访问控制页面（内网穿透通常自带证书）。

### 语音识别不可用？

检查 `vosk`、`pydub`、`ffmpeg` 是否安装，以及模型是否放在正确路径。  
若麦克风损坏，可注释掉 `index.html` 中的 `recordAndUploadAudio()` 调用。

### 串口连接失败？

确认 ESP32 已插入并查看设备管理器中的 COM 口，修改 `.env` 中的 `SERIAL_PORT`。

