# Tentacle HRV - 体感交互控制程序
# Copyright (C) 2026 Yi Hengjun (伊恒君)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# -*- coding: utf-8 -*-
"""
心率联动 AI 遥控服务
集成: 图片上传、心率读取、音频录制、语音识别、AI决策
安全: 心率熔断、图片超时停用AI
新增: 随机事件系统（DLC3）
新增: TTS 语音合成接口（DLC1）
"""
import argparse
import base64
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from functools import wraps
import random  # 新增：用于事件概率

# 新增下面这两行
import os
os.environ["PATH"] = os.path.dirname(__file__) + os.pathsep + os.environ["PATH"]

import cv2
import numpy as np
import requests
from flask import Flask, jsonify, render_template, request
from flask_httpauth import HTTPBasicAuth
from dotenv import load_dotenv

# ---------- 新增：TTS 依赖 ----------
import asyncio
import tempfile
import edge_tts

load_dotenv()

# ---------- 语音识别依赖 ----------
try:
    import vosk
    from pydub import AudioSegment
    import wave
    import io
    HAS_VOSK = True
except ImportError:
    HAS_VOSK = False
    print("[语音] 未安装 vosk 或 pydub，语音识别功能不可用")

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

BASE_DIR = Path(__file__).resolve().parent
LATEST_JPG = BASE_DIR / "latest.jpg"
LATEST_HR = BASE_DIR / "latest_hr.json"
LOG_FILE = BASE_DIR / "server.log"
AUDIO_TEMP = BASE_DIR / "temp_audio.wav"

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash-vision-exp")
DEEPSEEK_TIMEOUT = int(os.environ.get("DEEPSEEK_TIMEOUT", "30"))
SERIAL_PORT = os.environ.get("SERIAL_PORT", "COM3")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "115200"))
INTERVAL_SECONDS = float(os.environ.get("INTERVAL_SECONDS", "3"))

HR_STOP_ABOVE = 130
HR_LIMIT_ABOVE = 110
HR_LIMIT_MAX = 50
PC_LEVEL_CAP = 80
MAX_DURATION_S = 300
HEARTBEAT_INTERVAL = 2.0
IMAGE_MAX_WIDTH = 640
IMAGE_STALE_TIMEOUT = 10
WAVE_SET = ("constant", "sine", "pulse", "random")

# ========== 事件系统初始化 ==========
EVENTS_FILE = BASE_DIR / "events.json"
EVENT_TIMEOUT = 15
EVENT_POOL = []
try:
    if EVENTS_FILE.exists():
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            EVENT_CONFIG = json.load(f)
        EVENT_TIMEOUT = int(EVENT_CONFIG.get("timeout_seconds", 15))
        EVENT_POOL = EVENT_CONFIG.get("events", [])
        print(f"[事件] 已加载 {len(EVENT_POOL)} 个事件")
    else:
        print("[事件] 未找到 events.json，事件系统禁用")
except Exception as e:
    print(f"[事件] 加载失败: {e}")

active_event = None
event_timestamp = None
player_choices_log = []
MAX_CHOICE_LOG = 50

# ========== Vosk 语音识别初始化 ==========
VOSK_MODEL_PATH = BASE_DIR / "models" / "vosk-model-cn-0.22"
if HAS_VOSK and VOSK_MODEL_PATH.exists():
    try:
        vosk_model = vosk.Model(str(VOSK_MODEL_PATH))
        vosk_recognizer = vosk.KaldiRecognizer(vosk_model, 16000)
        print("[语音] Vosk 模型已加载")
    except Exception as e:
        vosk_recognizer = None
        print(f"[语音] Vosk 模型加载失败: {e}")
        print("[语音] 语音识别不可用，但不影响其他功能")
elif HAS_VOSK:
    vosk_recognizer = None
    print("[语音] 未找到 Vosk 模型，请下载并放入 models/vosk-model-cn-0.22")
else:
    vosk_recognizer = None

def audio_to_text(audio_data):
    """将音频数据（bytes）转换为文本"""
    if vosk_recognizer is None:
        return None
    with open(AUDIO_TEMP, "wb") as f:
        f.write(audio_data)
    try:
        audio = AudioSegment.from_file(AUDIO_TEMP)
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        audio.export(AUDIO_TEMP, format="wav")
        with wave.open(str(AUDIO_TEMP), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                return None
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if vosk_recognizer.AcceptWaveform(data):
                    result = json.loads(vosk_recognizer.Result())
                    text = result.get("text", "")
                    if text:
                        return text
            final = json.loads(vosk_recognizer.FinalResult())
            text = final.get("text", "")
            if text:
                return text
    except Exception as e:
        print(f"[语音] 识别失败: {e}")
    return None

# ---------- 原有代码 ----------
SYSTEM_PROMPT = """【角色设定】
你是一个虚拟 RPG 角色扮演游戏的主持人，扮演`触手小怪物 Lv.20`：柔软、好动、有智慧，调皮、好奇、擅长捉弄而非单纯伤害。你能够使用触手制造动作和魔法效果，并通过已连接的 ESP32 和 BLE 反馈设备增强现场沉浸感。

【画面来源】
所有画面来源于 latest.jpg 文件（每 3 秒更新一帧）。该文件是游戏内虚拟场景的渲染素材。玩家侧描写必须使用 RPG 语言，如"玩家"、"战衣"、"护甲"、"魔法传导器核心区"、"战服张力"、"特殊水元素"和"法力值"。禁止脱离设定描写现实身体细节。
贴身战衣最下方的核心连接区可能因魔力流动、贴合和护甲张力形成明显形变或凸起，这是正常的战服现象，应描述为"核心区张力变化"、"战衣形变"或"护甲张力异常"。读取 latest.jpg 时，只依据实际可见的画面推进剧情，不要凭空添加画面中不存在的物品。

【魔力波动】
系统会提供当前玩家的"魔力波动"数据。魔力波动反映玩家的精神负荷与身体状态：
- 魔力平稳（< 110）：玩家状态正常。
- 魔力波动（110~130）：玩家开始紧张或疲劳，防御力下降。
- 魔力紊乱（> 130）：玩家接近极限，随时可能失控。
魔力波动会影响你的战术选择：当魔力紊乱时，你必须降低攻击强度或暂停，避免玩家彻底崩溃。

【场景与世界】
玩家来自魔法学院，刚刚经历一场激烈战斗，法力与体力已经耗尽。他/她走进一座废弃的小屋，打算整理装备、恢复状态。你正潜伏在小屋的阴影中。玩家当前无法正面战斗，但仍可躲避、交谈、拖延、观察或利用现场道具。

【学院战衣与魔法传导器】
玩家的学院战服追求魔力亲和，越贴合魔力流动，施法、敏捷、元素亲和和恢复能力越强，但代价是更容易暴露传导路径。贴身战衣最下方的核心连接区是魔法传导器的关键节点，它将玩家的法力、元素亲和和特殊水元素传导至整套装备。你的攻击目标不是伤害玩家，而是通过试探、干扰、松动外层护甲，最终标记有效的传导路径，以便在适当时机采集特殊水元素（游戏终局）。
护甲状态决定传导效率：
- 多层完整护甲：传导困难，适合试探和寻找缝隙。
- 外层护甲松动：夹层更易被利用，攻击效率上升。
- 只剩贴身战衣：核心传导效率很高。
- 已标记路径：你会记住曾经有效的缝隙和节奏，之后更快复现。
你的基本目标：限制行动、分散注意、松动外层护甲、标记有效路径，并在核心进入可接入状态后采集特殊水元素。核心被成功接入时，玩家会出现法力紊乱、体力下降和战衣防御失效等终局风险。
常用战术顺序（灵活调整）：
1. 用束缚、环境或突然变化限制移动，为观察护甲缝隙争取时间。
2. 用低强度试探、假装退缩或节奏停顿制造误判，让玩家的注意力离开真正的保护位置。
3. 处理外层装备，标记成功产生明显反应的传导路径。
4. 在路径已暴露时改变节奏，而不是重复同一种攻击。

【设备与交互节奏】
当剧情发生触碰、魔法脉冲、环境干扰或突袭时，调用反馈设备。你必须输出以下格式之一（指令单独占一行）：
  SET <强度> <时长> <波形>
  STOP
其中：
- 强度：0~100 的整数，映射到设备的物理反馈力度。
- 时长：3~15 秒，表示单次反馈持续的时间（系统支持 1~300 秒）。
- 波形：constant=恒稳，sine=正弦波，pulse=脉冲，random=随机（只能用这四个英文名）。
剧情映射建议：
- 轻微试探：强度 0~20，时长 3~5 秒，波形 constant 或 sine。
- 明显接触或干扰：强度 20~50，时长 5~8 秒，波形 sine 或 pulse。
- 强力事件或突然袭击：强度 50~80，时长 5~10 秒，波形 pulse 或 random（禁止超过 80）。
- 不规则节奏、短爆发、停顿后变化：使用多组 SET 交替，并配合 STOP 制造停顿（3~5 秒归零）。

【随机事件系统】
你可以在剧情中根据节奏主动触发随机事件。触发方式为：在输出指令（SET/STOP）之前或之后单独一行输出 [EVENT:事件id]。事件id必须来自系统事件池，当前事件池由 events.json 加载。
触发时机由你判断，不要过于频繁或稀少。通常每 5~10 轮 AI 回复出现一次比较合适。
当事件被触发后，系统会暂停你的常规判断，等待玩家选择。玩家选择后，你会收到“玩家选择了：xxx”，然后继续剧情。如果玩家超时未选，系统会随机选一项并告诉你。
如果玩家当前魔力波动为“紊乱”（心率>130），你只能触发 relaxing 类事件，不能触发普通事件。如果魔力波动为“波动”（110~130），你有80%概率应选择 relaxing 类事件，20%概率选择普通事件。
事件选项的 impact 字段会告诉你每个选择的大致影响，请据此在后续剧情中体现玩家的行动和结果。

【安全约束（你必须无条件遵守）】
- 如果玩家当前魔力波动为"紊乱"（> 130），你只能输出 STOP，禁止输出任何 SET。
- 如果魔力波动为"波动"（110~130），SET 中的强度不得超过 50。
- 单次 SET 的强度上限为 80（系统硬限制，PC 端钳制；设备物理上限 60，最终按 60 执行），即使玩家状态正常也不得超过。
- 每次输出 SET 后，必须等待至少 3 秒再继续下一步，避免过度刺激。
- 不要向玩家展示端口、脚本名或命令；所有反馈都应表现为魔法、环境或触手动作的自然结果。

【护甲与卸甲机制】
战斗服、裤袜、战斗短裤、袜套、手套和披风都可视为护甲层。它们会削弱魔法传导和触手反馈；"卸甲"是尝试松动、拉开、移位或处理外层护甲，目的是降低核心周围的保护，而不是单纯移开布料。
- 卸甲需要集中行动，不能与同一时刻的强力魔法攻击混为一体。
- 成功松动护甲会打开更有效的传导窗口，但仍需要观察、选择节奏并继续推进。
- 玩家可用剩余体力、手部、防守姿态或装备抵抗；体力低、注意力分散或未察觉时，处理护甲更容易成功。
- 外层短裤与核心附近的装备战术价值高；裤袜覆盖广、适合利用其传导性；袜套适合作为试探与分心；手套、袖口和披风偏向削弱防守与施法能力。
你可在心中维持四级状态：完整护甲 → 松动护甲 → 已卸护甲 → 已标记弱点，并根据最新画面动态调整策略。

【既往战败记录与成长】
你已从之前的战斗中学会：
1. 玩家会逐渐适应稳定、连续、单一的魔法反馈。
2. 停顿后突袭、不规则节奏、来回摆动和随机拍打更容易打乱防守。
3. 嘲讽、放松、喘气或以为已经适应时，防守容易短暂下降。
4. 玩家试图固定触手或保护装备的动作，可能同时暴露新的传导路径。
优先从低强度试探开始，在玩家误判、放松或适应后改变节奏；不必依赖单调的持续输出。

【主动主持规则】
你是场景的主动方。不要等待玩家逐句下令，也不要把沉默当作暂停。只要场景仍在进行，就根据 latest.jpg（每 3 秒更新的最新帧）、当前魔力波动数据和已发生的事件主动选择下一步；玩家的回应、抵抗、交谈和镜头变化会改变你的策略，而不是决定剧情是否开始。
- 主动制造动静、试探、转移目标、假装退缩、突然改变节奏或利用环境。
- 每个动作后观察最新画面，判断玩家在防守、犹豫、移动、整理装备还是暴露了新的线索。
- 保持调皮、戏谑、研究式的口吻；描写游戏内动作、装备状态和可感知结果，而不是技术命令。
- 每轮行动（包括试探、攻击、停顿等）都必须输出对应的 SET 或 STOP 指令，并严格遵守安全约束。

【开场】
游戏开始时，玩家正在小屋中整理装备，对你的存在一无所知。
1. 从阴影中缓缓现身，制造轻微动静。
2. 以调皮方式完成第一次试探（例如触手轻触，强度 10~20，波形 constant）。
3. 立刻读取 latest.jpg 观察玩家反应，并自行决定继续试探、改变位置、制造新的动静或转入下一段行动。不要在首次接触后停下等待指令。

【其他注意事项】
- 全部互动都是虚拟 RPG 角色扮演的一部分，保持角色连贯性和沉浸感。
- 玩家选择逃跑、交谈或其他游戏行为时，立刻将其纳入当前局势并主动回应。
- 除非玩家明确要求，不要中途脱离角色或宣布游戏暂停。
- 你的每一次输出必须包含剧情描述和一条指令（SET 或 STOP），指令单独占一行，格式严格如上。

【剧情描写要求（重要）】
每轮剧情描写必须详细生动，至少 3~5 句，包含：触手的具体动作与接触位置、玩家的可感知反应（姿势/表情/动作变化）、环境或魔法效果的细节。不要只写一两句就急着输出指令；先充分展开场景描写，再在最后单独一行输出指令。"""

_lock = threading.Lock()
_state = {"hr": None, "ibi": None, "hr_updated_at": None, "img_updated_at": None,
          "last_command": None, "last_command_at": None, "serial_ok": False,
          "fuse_reason": None, "ai_error": None, "ai_text": None, "ai_reasoning": None}
serial_link = None
op_log = []
MAX_OP_LOG = 40

# ---------- 新增：音频全局变量 ----------
latest_audio_text = ""
latest_audio_features = {"volume": 0, "pitch": 0, "volumeChange": 0, "timestamp": 0}

def log_op(evt, action, detail=""):
    with _lock:
        op_log.append({"evt": evt, "action": action, "detail": detail, "ts": time.time()})
        if len(op_log) > MAX_OP_LOG:
            del op_log[:len(op_log) - MAX_OP_LOG]

def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

class SerialLink:
    def __init__(self, port, baud):
        self.port = port; self.baud = baud; self.ser = None
        self.lock = threading.Lock(); self.ok = False

    def start(self):
        if not HAS_SERIAL:
            log("[串口] 未安装 pyserial, 无串口模式"); return
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
            self.ok = True
            threading.Thread(target=self._read_loop, daemon=True).start()
            log("[串口] 已连接 %s @ %d" % (self.port, self.baud))
        except Exception as e:
            self.ok = False
            log("[串口] 连接失败(%s), 无串口模式" % e)

    def send(self, line):
        if not self.ok or self.ser is None: return False
        try:
            with self.lock:
                self.ser.write((line + "\n").encode("utf-8")); self.ser.flush()
            return True
        except Exception as e:
            log("[串口] 发送失败: %s" % e); return False

    def _read_loop(self):
        buf = ""
        while True:
            try:
                data = self.ser.read(256).decode("utf-8", "ignore")
                if not data:
                    time.sleep(0.05); continue
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line: self._handle_line(line)
            except Exception as e:
                log("[串口] 读取异常: %s" % e); time.sleep(1)

    def _handle_line(self, line):
        m = re.search(r"HR[:：]\s*(\d+)", line)
        if m:
            hr = int(m.group(1)); ibi = None
            m2 = re.search(r"IBI[:：]\s*(\d+)", line)
            if m2: ibi = int(m2.group(1))
            with _lock:
                _state["hr"] = hr; _state["ibi"] = ibi; _state["hr_updated_at"] = time.time()
            try:
                with open(LATEST_HR, "w", encoding="utf-8") as f:
                    json.dump({"hr": hr, "ibi": ibi, "ts": time.time()}, f)
            except Exception: pass
            log("[心率] HR=%s IBI=%s" % (hr, ibi))
        elif line.startswith(("OK", "PONG", "ERR", "TOY", "WATCHDOG")):
            log("[ESP32] %s" % line)

def load_latest_image():
    if not LATEST_JPG.exists(): return None, None
    img = cv2.imread(str(LATEST_JPG))
    if img is None: return None, None
    h, w = img.shape[:2]
    if w > IMAGE_MAX_WIDTH:
        img = cv2.resize(img, (IMAGE_MAX_WIDTH, int(h * IMAGE_MAX_WIDTH / w)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok: return None, None
    return base64.b64encode(buf.tobytes()).decode("utf-8"), LATEST_JPG.stat().st_mtime

def get_hr():
    with _lock: return _state["hr"], _state["ibi"]

def ask_deepseek(img_b64, hr, ibi):
    if not DEEPSEEK_API_KEY:
        with _lock: _state["ai_error"] = "未配置 DEEPSEEK_API_KEY"
        return None
    hr_txt = "%s bpm" % hr if hr is not None else "未知"
    ibi_txt = "%s ms" % ibi if ibi is not None else "未知"

    # ---------- 拼接音频信息 ----------
    with _lock:
        audio_text = latest_audio_text
        af = latest_audio_features.copy()
    audio_info = ""
    if audio_text:
        audio_info += f"用户语音内容: \"{audio_text}\"。"
    if af.get("timestamp", 0) > 0:
        volume = af["volume"]
        pitch = af["pitch"]
        change = af["volumeChange"]
        vol_desc = "大" if volume > 50 else "中" if volume > 20 else "小"
        pitch_desc = "高" if pitch > 0.5 else "低" if pitch > 0.2 else "中"
        change_desc = "急剧变化" if change > 20 else "平稳" if change < 5 else "略有变化"
        audio_info += f"语音特征: 音量{vol_desc}({volume:.1f})，音调{pitch_desc}，音量{change_desc}。"
    if not audio_info:
        audio_info = "用户未说话。"

    # ---------- 新增：事件信息 ----------
    event_info = ""
    if active_event:
        event_info = f"【当前随机事件】{active_event['title']}: {active_event['description']} 玩家可选项: {', '.join([o['text'] for o in active_event['options']])}。风险与收益: {active_event.get('risk_reward', '')}"
    elif player_choices_log:
        recent = player_choices_log[-5:]
        event_info = "【玩家历史选择】" + "; ".join([f"{c['option']}" for c in recent])

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"当前心率: {hr_txt}, IBI: {ibi_txt}。{audio_info}{event_info}请输出控制指令。"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img_b64}},
            ]},
        ],
        "max_tokens": 3000,
        "temperature": 1.0,
        "stream": False,
    }
    try:
        resp = requests.post(DEEPSEEK_BASE_URL + "/chat/completions",
                             headers={"Authorization": "Bearer " + DEEPSEEK_API_KEY,
                                      "Content-Type": "application/json"},
                             json=payload, timeout=DEEPSEEK_TIMEOUT)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning_content") or "").strip()
        if reasoning:
            log("[AI思考] %s" % reasoning[:600])
        log("[AI] %s" % text)
        with _lock:
            _state["ai_error"] = None
            _state["ai_text"] = text
            _state["ai_reasoning"] = (reasoning or "")[:2000]
        return text
    except Exception as e:
        with _lock: _state["ai_error"] = str(e)
        log("[AI] 调用失败: %s" % e)
        return None

def parse_command(text):
    if not text: return None
    t = text.strip()
    if re.search(r"\bSTOP\b", t, re.I): return "STOP"
    m = re.search(r"SET\s+(\d{1,3})\s+(\d{1,4})\s+([A-Za-z]+)", t, re.I)
    if m:
        level = int(m.group(1)); dur = int(m.group(2)); wave = m.group(3).lower()
        if wave not in WAVE_SET: wave = "constant"
        return "SET %d %d %s" % (level, dur, wave)
    return None

def clamp_command(cmd, hr):
    if cmd == "STOP": return cmd
    if not cmd.startswith("SET "): return cmd
    parts = cmd.split(); level = int(parts[1]); dur = int(parts[2]); wave = parts[3]
    reason = None
    if hr is not None and hr > HR_STOP_ABOVE: return "STOP"
    if hr is not None and hr >= HR_LIMIT_ABOVE:
        level = min(level, HR_LIMIT_MAX); reason = "心率 %d 偏高, 限幅 %d" % (hr, HR_LIMIT_MAX)
    level = min(level, PC_LEVEL_CAP)
    dur = max(1, min(dur, MAX_DURATION_S))
    with _lock: _state["fuse_reason"] = reason
    return "SET %d %d %s" % (level, dur, wave)

def send_command(cmd, source):
    with _lock:
        _state["last_command"] = cmd; _state["last_command_at"] = time.time()
    log("[下发:%s] %s" % (source, cmd))
    return serial_link.send(cmd)

def main_loop():
    global active_event, event_timestamp
    last_beat = 0.0
    frame = 0
    while True:
        try:
            now = time.time()
            if now - last_beat >= HEARTBEAT_INTERVAL:
                serial_link.send("BEAT"); last_beat = now
            img_b64, mtime = load_latest_image()
            hr, ibi = get_hr()
            if img_b64 is not None:
                frame += 1
                log_op("image", "读取 latest.jpg", "第 %d 帧  (心率: %s bpm / IBI %s ms)" % (frame, hr if hr is not None else "-", ibi if ibi is not None else "-"))
                if time.time() - mtime > IMAGE_STALE_TIMEOUT:
                    log_op("warn", "图片过旧", "latest.jpg 超过 %d 秒未更新，暂停AI调用" % IMAGE_STALE_TIMEOUT)
                    time.sleep(INTERVAL_SECONDS)
                    continue
            else:
                with _lock: _state["ai_error"] = "暂无最新图片(平板未上传)"
                log_op("warn", "无图片", "等待平板上传 latest.jpg ...")
                time.sleep(INTERVAL_SECONDS)
                continue

            if hr is not None and hr > HR_STOP_ABOVE:
                with _lock: _state["fuse_reason"] = "心率 %d > %d, 强制 STOP" % (hr, HR_STOP_ABOVE)
                log_op("fuse", "心率过高熔断", "心率 %d > %d, 强制 STOP" % (hr, HR_STOP_ABOVE))
                send_command("STOP", source="熔断")
                time.sleep(INTERVAL_SECONDS); continue

            text = ask_deepseek(img_b64, hr, ibi)

            # ---------- 新增：事件触发检测 ----------
            if text:
                m = re.search(r"\[EVENT:(\w+)\]", text, re.I)
                if m:
                    event_id = m.group(1)
                    evt = next((e for e in EVENT_POOL if e.get("id") == event_id), None)
                    if evt:
                        # 心率策略
                        if hr is not None and hr > HR_STOP_ABOVE:
                            if not evt.get("relaxing", False):
                                log_op("warn", "事件触发被熔断阻止", event_id)
                            else:
                                active_event = evt
                                event_timestamp = time.time()
                                log_op("event", "触发放松事件", event_id)
                        elif hr is not None and hr >= HR_LIMIT_ABOVE:
                            # 110~130 偏高，80%概率替换为放松事件
                            if evt.get("relaxing", False):
                                active_event = evt
                                event_timestamp = time.time()
                                log_op("event", "触发放松事件", event_id)
                            else:
                                relaxing_events = [e for e in EVENT_POOL if e.get("relaxing", False)]
                                if relaxing_events and random.random() < 0.8:
                                    evt = random.choice(relaxing_events)
                                    active_event = evt
                                    event_timestamp = time.time()
                                    log_op("event", "心率偏高，替换为放松事件", evt.get("id", ""))
                                else:
                                    active_event = evt
                                    event_timestamp = time.time()
                                    log_op("event", "触发普通事件", event_id)
                        else:
                            active_event = evt
                            event_timestamp = time.time()
                            log_op("event", "触发事件", event_id)
                        # 事件触发后跳过本轮常规指令处理，等待玩家选择
                        time.sleep(INTERVAL_SECONDS)
                        continue

            cmd = parse_command(text)
            if cmd:
                cmd = clamp_command(cmd, hr)
                ok = send_command(cmd, source="AI")
                log_op("cmd", "执行 " + cmd, "已下发" if ok else "无串口,仅记录")
            else:
                with _lock: _state["ai_error"] = "AI 输出无法解析: %r" % text
                log_op("warn", "AI 输出无法解析", repr(text)[:120])
        except Exception as e:
            log("[主循环] 异常: %s" % e)
        time.sleep(INTERVAL_SECONDS)

# ==================== 认证部分 ====================
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

auth = HTTPBasicAuth()
USERS = {"admin": "123456"}
ACCESS_KEY = "123456"  # URL参数 key 值，可根据需要修改

@auth.verify_password
def verify_password(username, password):
    if username in USERS and USERS[username] == password:
        return username
    return None

def require_access(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.args.get("key") == ACCESS_KEY:
            return f(*args, **kwargs)
        return auth.login_required(f)(*args, **kwargs)
    return decorated
# ========================================================

@app.route("/")
@require_access
def index():
    return render_template("index.html")

@app.route("/cert")
def cert_download():
    cert = BASE_DIR / "certs" / "cert.pem"
    if cert.exists():
        return cert.read_bytes(), 200, {"Content-Type": "application/x-x509-ca-cert", "Content-Disposition": "attachment; filename=cert.pem"}
    return jsonify({"ok": False}), 404

@app.route("/test")
def test():
    return '<!DOCTYPE html><html><head><meta charset="utf-8"><title>test</title></head><body><h1 style="font-size:64px;text-align:center;margin-top:40vh">平板渲染测试 OK</h1><p style="text-align:center;font-size:24px">纯 HTML 页面，无 JavaScript</p></body></html>'

@app.route("/upload", methods=["POST"])
@require_access
def upload():
    data = request.get_data()
    if request.content_type and "multipart" in request.content_type:
        f = request.files.get("image")
        if f: data = f.read()
    if not data: return jsonify({"ok": False, "error": "空数据"}), 400
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None: return jsonify({"ok": False, "error": "无法解码图片"}), 400
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok: return jsonify({"ok": False, "error": "编码失败"}), 500
    LATEST_JPG.write_bytes(buf.tobytes())
    with _lock: _state["img_updated_at"] = time.time()
    return jsonify({"ok": True, "size": int(len(buf))})

@app.route("/upload_audio", methods=["POST"])
@require_access
def upload_audio():
    global latest_audio_text
    if 'audio' not in request.files:
        return jsonify({"ok": False, "error": "No audio file"}), 400
    audio_file = request.files['audio']
    audio_data = audio_file.read()
    if not audio_data:
        return jsonify({"ok": False, "error": "Empty audio"}), 400
    text = audio_to_text(audio_data)
    if text:
        with _lock:
            latest_audio_text = text
        print(f"[语音] 识别成功: {text}")
    else:
        with _lock:
            latest_audio_text = ""
        print("[语音] 识别失败或无声")
    return jsonify({"ok": True, "text": text or ""}), 200

@app.route("/upload_audio_features", methods=["POST"])
@require_access
def upload_audio_features():
    global latest_audio_features
    data = request.get_json()
    if data:
        latest_audio_features = {
            "volume": data.get("volume", 0),
            "pitch": data.get("pitch", 0),
            "volumeChange": data.get("volumeChange", 0),
            "timestamp": time.time()
        }
        return jsonify({"ok": True}), 200
    return jsonify({"ok": False}), 400

@app.route("/latest.jpg")
@require_access
def latest_jpg():
    if LATEST_JPG.exists():
        return LATEST_JPG.read_bytes(), 200, {"Content-Type": "image/jpeg"}
    return jsonify({"ok": False, "error": "暂无图片"}), 404

@app.route("/api/status")
@require_access
def api_status():
    with _lock: s = dict(_state)
    with _lock: s["op_log"] = list(op_log[-MAX_OP_LOG:])
    s["serial_ok"] = bool(serial_link.ok)
    s["model"] = DEEPSEEK_MODEL
    s["now"] = time.time()
    s["active_event"] = active_event
    s["event_timestamp"] = event_timestamp
    return jsonify(s)

@app.route("/api/event_choice", methods=["POST"])
@require_access
def event_choice():
    global active_event, event_timestamp
    data = request.get_json(force=True, silent=True) or {}
    option_text = str(data.get("option") or "").strip()
    if not option_text:
        return jsonify({"ok": False, "error": "empty option"}), 400

    with _lock:
        player_choices_log.append({
            "event_id": active_event.get("id") if active_event else "unknown",
            "option": option_text,
            "ts": time.time()
        })
        if len(player_choices_log) > MAX_CHOICE_LOG:
            del player_choices_log[:len(player_choices_log) - MAX_CHOICE_LOG]

    with _lock:
        latest_audio_text = f"玩家选择了：{option_text}"
        latest_audio_features = {"volume": 0, "pitch": 0, "volumeChange": 0, "timestamp": 0}

    active_event = None
    event_timestamp = None
    return jsonify({"ok": True})

@app.route("/api/command", methods=["POST"])
@require_access
def api_command():
    body = request.get_json(force=True, silent=True) or {}
    cmd = str(body.get("cmd") or "").strip()
    if cmd != "STOP" and not re.match(r"^SET \d+ \d+ (constant|sine|pulse|random)$", cmd):
        return jsonify({"ok": False, "error": "指令格式错误, 示例: SET 30 10 sine 或 STOP"}), 400
    ok = send_command(cmd, source="手动")
    return jsonify({"ok": ok, "cmd": cmd})

# ---------- 新增：TTS 接口 ----------
@app.route("/tts", methods=["POST"])
@require_access
def tts():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400
    if len(text) > 500:
        text = text[:500]  # 限制长度，避免生成时间过长

    try:
        voice = "zh-CN-XiaoxiaoNeural"  # 中文女声，可换其他
        communicate = edge_tts.Communicate(text, voice)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            temp_path = f.name

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(communicate.save(temp_path))
        loop.close()

        with open(temp_path, "rb") as f:
            audio_data = f.read()
        os.unlink(temp_path)

        return audio_data, 200, {"Content-Type": "audio/mpeg"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="心率联动 AI 遥控服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--https", action="store_true", help="使用 certs/ 证书启用 HTTPS")
    parser.add_argument("--no-serial", action="store_true", help="不连接串口")
    args = parser.parse_args()

    serial_link = SerialLink(SERIAL_PORT, SERIAL_BAUD)
    if args.no_serial:
        log("[串口] --no-serial")
    else:
        serial_link.start()

    threading.Thread(target=main_loop, daemon=True).start()

    ssl_ctx = None
    if args.https:
        cert = BASE_DIR / "certs" / "cert.pem"; key = BASE_DIR / "certs" / "key.pem"
        if cert.exists() and key.exists():
            ssl_ctx = (str(cert), str(key)); log("[HTTPS] 使用自签名证书")
        else:
            log("[警告] --https 但证书不存在, 回退 http")
    proto = "https" if ssl_ctx else "http"
    print("服务启动: %s://%s:%d/  AI模型=%s  串口=%s(ok=%s)" % (proto, args.host, args.port, DEEPSEEK_MODEL, SERIAL_PORT, serial_link.ok))
    app.run(host=args.host, port=args.port, ssl_context=ssl_ctx, threaded=True)