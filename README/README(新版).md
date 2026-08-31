# Tentacle HRV — 多模态 AI 闭环体感控制系统

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

> 基于原作者 [ra1nyxin/tentacle-monster-roleplay-esp32](https://github.com/ra1nyxin/tentacle-monster-roleplay-esp32) 修改，核心改动在 **输入端**——增加了心率/HRV 实时监测和语音情绪识别，让 AI 能读懂你的生理状态和声音情绪，而不是只看画面猜状态。

## 📌 功能特性

- **多模态输入**：摄像头画面 + 心率/HRV + 语音内容 + 语音情绪特征
- **AI 实时决策**：基于 DeepSeek V4-Flash-Vision-Exp 多模态模型
- **安全熔断机制**：心率/HRV 自适应熔断，防止过度刺激
- **语音情绪理解**：通过 Web Audio API 提取音量、音调、变化率
- **实时闭环控制**：平板拍照上传 → AI 分析 → ESP32 蓝牙控制
- **完整日志记录**：心率、HRV、指令、音频特征全记录

## 🛠️ 硬件需求

| 设备 | 型号 | 用途 |
|------|------|------|
| 电脑 | Windows 10/11 | 运行 Flask 服务 |
| ESP32 开发板 | NodeMCU-32S | 蓝牙中转 + 心率接收 |
| 心率手环 | 支持 BLE 心率服务 (0x180D) | 心率/IBI 采集 |
| 蓝牙玩具 | 任意可逆向控制的 BLE 玩具 | 物理反馈执行器 |
| 平板/手机 | 带浏览器和摄像头 | 控制界面 |

## 📦 软件依赖

- Python 3.10+
- Flask + Flask-HTTPAuth
- OpenCV + NumPy
- pyserial
- Vosk（本地语音识别）
- pydub（音频处理）
- FFmpeg（音频格式转换）

## 🚀 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/Yhj147369/tentacle-hrv.git
cd tentacle-hrv

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 下载 Vosk 模型到 models/vosk-model-cn-0.22/
#    下载地址：https://alphacephei.com/vosk/models/vosk-model-cn-0.22.zip

# 5. 安装 FFmpeg 并配置环境变量

# 6. 启动服务
python server.py --port 8080