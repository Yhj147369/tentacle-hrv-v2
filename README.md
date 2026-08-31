# Tentacle HRV — 多模态 AI 闭环体感控制系统

基于 DeepSeek 多模态 API，整合 **平板摄像头拍照 + 心率手环 BLE 数据 + 网页控制面板 + ESP32 蓝牙中转 + 蓝牙玩具反馈** 的实时闭环控制系统。  
核心创新在 **输入端**：增加心率/HRV 实时监测和语音情绪识别（可选）。

## ✨ 功能特性

- 📷 平板摄像头每 3 秒拍照上传，AI 实时分析画面
- ❤️ 通过 ESP32 读取心率手环数据（HR/IBI），支持心率熔断与限幅
- 🧠 DeepSeek 多模态模型决策，输出 `SET 强度 时长 波形` 或 `STOP` 指令
- 🔊 可选语音识别（Vosk 中文模型），让 AI 理解玩家语音
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
