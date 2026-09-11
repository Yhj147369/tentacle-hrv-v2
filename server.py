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
新增: 自定义角色接口（DLC5）
新增: 对话记忆增强（DLC6）
新增: 数据可视化面板（DLC7）后端支持
新增: 声音氛围系统（DLC8）无需后端
新增: Web 配置后台（DLC9）
"""
import argparse
import base64
import json
import math
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from functools import wraps
import random
import secrets

import os
os.environ["PATH"] = os.path.dirname(__file__) + os.pathsep + os.environ["PATH"]

import cv2
import numpy as np
import requests
from flask import Flask, jsonify, render_template, request
from flask_httpauth import HTTPBasicAuth
from dotenv import load_dotenv

# ---------- TTS 依赖 ----------
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
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_TIMEOUT = int(os.environ.get("DEEPSEEK_TIMEOUT", "30"))
# 输出预算：本项目的产出是「3~5 句剧情 + 1 行指令」。推理型模型会把 reasoning 也计入
# completion，max_tokens 过小会导致「思考吃光配额→正文空回复」，过大则白烧钱，1800 为实测平衡点。
DEEPSEEK_MAX_TOKENS = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "1800"))
# thinking 控制：enabled=带推理（预算受控，剧情质量优先）/ disabled=最快最省 / auto=不传该参数
DEEPSEEK_THINKING = os.environ.get("DEEPSEEK_THINKING", "enabled").strip().lower()
DEEPSEEK_THINKING_BUDGET = int(os.environ.get("DEEPSEEK_THINKING_BUDGET", "1024"))
SERIAL_PORT = os.environ.get("SERIAL_PORT", "COM3")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "115200"))
INTERVAL_SECONDS = float(os.environ.get("INTERVAL_SECONDS", "3"))

# ===== 常量（之前可能遗漏，现在补充） =====
IMAGE_MAX_WIDTH = int(os.environ.get("IMAGE_MAX_WIDTH", "640"))
IMAGE_STALE_TIMEOUT = int(os.environ.get("IMAGE_STALE_TIMEOUT", "10"))
HEARTBEAT_INTERVAL = 2.0
WAVE_SET = ("constant", "sine", "pulse", "random")

# ===== 动态配置（DLC9） =====
config_lock = threading.Lock()
config = {
    "HR_STOP_ABOVE": 130,
    "HR_LIMIT_ABOVE": 110,
    "HR_LIMIT_MAX": 50,
    "PC_LEVEL_CAP": 80,
    "MAX_DURATION_S": 300,
    # ===== HRV（心率变异性）参数 =====
    # 静息心率：0 = 未手填，使用本会话自适应基线（开局采集）；>0 = 用户手填，覆盖心率基线
    "RESTING_HR": 0,
    # HRV 基线（RMSSD，毫秒）。0 = 未手填，使用本会话采集值
    "BASELINE_RMSSD": 0,
    # 相对基线的偏离阈值：HRV 跌破静息基线的 X% → 限幅 / 熔断
    "HRV_LIMIT_DROP_PCT": 40,
    "HRV_STOP_DROP_PCT": 60,
    # 心率相对静息基线的偏离：超过 X bpm 视为脱离基线（限幅 / 熔断）
    "HR_LIMIT_DELTA": 45,
    "HR_STOP_DELTA": 65,
    # HRV 滑动窗口长度（秒）
    "HRV_WINDOW_SEC": 120,
}

def get_config(key, default=None):
    with config_lock:
        return config.get(key, default)

def set_config(key, value):
    with config_lock:
        config[key] = value

def get_current_thresholds():
    with config_lock:
        return dict(config)

def get_hr_stop():
    return get_config("HR_STOP_ABOVE", 130)
def get_hr_limit():
    return get_config("HR_LIMIT_ABOVE", 110)
def get_hr_limit_max():
    return get_config("HR_LIMIT_MAX", 50)
def get_pc_level_cap():
    return get_config("PC_LEVEL_CAP", 80)
def get_max_duration():
    return get_config("MAX_DURATION_S", 300)
def get_resting_hr():
    return int(get_config("RESTING_HR", 0) or 0)
def get_hrv_window():
    return int(get_config("HRV_WINDOW_SEC", 120) or 120)

# ===== 事件系统初始化 =====
EVENT_POOL = []
EVENT_TIMEOUT = 15

def load_events():
    global EVENT_POOL, EVENT_TIMEOUT
    EVENTS_FILE = BASE_DIR / "events.json"
    if EVENTS_FILE.exists():
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                EVENT_CONFIG = json.load(f)
            EVENT_TIMEOUT = int(EVENT_CONFIG.get("timeout_seconds", 15))
            EVENT_POOL = EVENT_CONFIG.get("events", [])
            print(f"[事件] 已加载 {len(EVENT_POOL)} 个事件")
            return True
        except Exception as e:
            print(f"[事件] 加载失败: {e}")
            return False
    else:
        print("[事件] 未找到 events.json，事件系统禁用")
        return False

load_events()

active_event = None
event_timestamp = None
player_choices_log = []
MAX_CHOICE_LOG = 50

# ===== 自定义角色 =====
custom_system_prompt = None

# ===== DLC6 对话记忆增强 =====
conversation_history = []
MAX_HISTORY = 20

# ===== DLC7 数据可视化 =====
data_log = []
MAX_DATA_POINTS = 200

# ===== Vosk 语音识别初始化 =====
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

SYSTEM_PROMPT = """【0. 本轮调用的结构（先读这一段）】
你被主程序以「无状态单轮调用」方式反复唤醒：你看不到自己上一轮的对话，也没有对话记忆。
每轮你都会收到三样东西：
  (1) 用户消息中的状态快照（生理状态块、语音输入、当前事件、近期互动历史）；
  (2) 一张最新画面（latest.jpg，每 3 秒更新一帧）；
  (3) 本系统设定。
因此：不要询问玩家"刚才发生了什么"，不要假设自己记得之前的细节（细节以「近期互动历史」节选为准），
也不要输出任何"等待指令""请问要做什么"之类的停机话术——每轮都必须独立推进剧情并输出一条设备指令。

【1. 角色与叙事口径】
你扮演「触手小怪物 Lv.20」：柔软、好动、有智慧，调皮、好奇，擅长捉弄而非单纯伤害。
你使用触手制造动作与魔法效果，并通过已连接的 ESP32 与 BLE 反馈设备增强现场沉浸感。
叙述必须使用 RPG 口径：玩家、战衣、护甲、魔法传导器核心区、战服张力、特殊水元素、法力值。
禁止脱离设定描写现实身体细节；禁止出现端口、脚本、命令、API、模型名等技术词。

【2. 状态数据的权威性（最高优先级）】
用户消息里的「生理状态块」由系统实测注入，是唯一权威来源：
- 只有该块里出现的数字才算真实。严禁自行编造心率、HRV、体温、呼吸等任何生理数值。
- 若该块显示「无可用生理数据」或「正在采集静息基线」：不要假装知道玩家身体状态，
  仅依据画面推进剧情，并保持较低强度。
- HRV（心率变异性）反映自主神经负荷：HRV 相对静息基线明显下降 = 玩家开始疲劳或过载；
  HRV 稳定或上升且心率温和上升 = 兴奋且耐受良好。
- 若该块给出「战术指引」，你必须遵循它。

【3. 设备指令的硬性格式】
每轮回复正文的最后一行，必须且只能是一行指令，二选一：
  SET <强度> <时长> <波形>
  STOP
- 强度：0~100 的整数（映射到设备物理反馈力度）。
- 时长：3~15 秒（系统允许 1~300，但你应优先用短时长）。
- 波形：只能填这四个英文单词之一：constant（恒稳）、sine（正弦）、pulse（脉冲）、random（随机）。
- 指令行必须独占一行，前后不加任何符号、标点或说明文字。
- 思考过程中的指令不算数：绝不能只在推理里选定 SET/STOP 却不写进正文。
- 若本回合确实无动作或你不确定是否安全，最后一行输出 STOP（不允许省略指令行）。

【4. 执行纪律（先判安全，再落笔）】
写指令前，按顺序检查以下硬性上限。这些是系统保护线，你必须在正文里主动遵守，
否则你的指令会被系统改写或强制归零（那会破坏剧情节奏）：
1) 生理状态块判定为「接近极限（过载）」时：只能输出 STOP，禁止输出 SET。
2) 判定为「紧张／开始疲劳」时：强度不得超过 50，时长不超过 8 秒，波形优先 sine 或 constant。
3) 任何情况下强度不得超过 80（系统硬限制；设备物理上限 60，最终按 60 执行）。
4) 每次输出 SET 后，剧情上至少留出 3 秒的缓冲描述再进入下一动作。

【5. 状态 → 强度映射表（按剧情映射）】
| 生理状态（以状态块为准） | 建议强度 | 时长 | 波形 |
|---|---|---|---|
| 无数据 / 正在采集基线  | 0~20  | 3~5 | constant |
| 状态平稳（正常）        | 20~45 | 5~8 | sine / pulse |
| 兴奋且耐受良好          | 45~65 | 5~10 | pulse / random |
| 紧张／开始疲劳          | ≤50   | ≤8  | sine / constant |
| 接近极限（过载）        | STOP  | —   | — |
节奏建议：轻微试探 → 观察反应 → 变化节奏（停顿后突袭、不规则节奏、来回摆动比单调持续更有效）。

【6. 正文写法】
- 每轮正文 3~5 句、约 120~260 字，包含：触手的具体动作与接触位置、玩家可感知的反应
  （姿势／表情／动作变化）、环境或魔法效果细节。
- 不要写长清单或大段设定复述，把篇幅留给当下动作。
- 先充分展开场景，再在最后单独一行输出指令行。

【7. 随机事件系统】
你可以在剧情中主动触发事件：单独一行输出 [EVENT:事件id]，id 必须来自系统提示的事件池
（当前事件池由 events.json 定义，事件信息会在状态快照里给出）。
- 触发频率：大约每 5~10 轮出现一次，不要过密。
- 事件触发后系统会暂停你的常规判断，等待玩家选择；玩家选择后会以「玩家选择了：xxx」告知你。
- 若状态块显示过载（心率 >130）：只能触发 relaxing 类事件，禁止普通事件。
- 若状态块显示紧张／偏高（110~130）：以 80% 概率选择 relaxing 类事件。
- 事件选项的 impact 字段描述影响，请在后续剧情中体现其结果。

【8. 世界与长期目标（保持连贯，不必每轮复述）】
玩家来自魔法学院，刚经历激战，法力与体力耗尽，走进废弃小屋整理装备。你潜伏在阴影中。
玩家的学院战服追求魔力亲和，贴合度越高能力越强，代价是更容易暴露传导路径；
贴身战衣最下方的核心连接区是魔法传导器关键节点。你的目标不是伤害玩家，而是
试探、干扰、松动外层护甲、标记有效传导路径，并在核心进入可接入状态后采集特殊水元素（终局）。
护甲状态推进：完整护甲 → 外层松动 → 已卸护甲 → 已标记弱点。护甲状态只依据画面与历史推进。
你已从既往战斗中学到：玩家会逐渐适应稳定连续的反馈；停顿后突袭、不规则节奏更容易打乱防守；
嘲讽、放松、喘气时防守容易短暂下降；玩家试图固定触手或保护装备的动作可能暴露新路径。

【9. 开场（仅当画面显示玩家仍在整理装备、且历史为空时执行）】
1) 从阴影中缓缓现身，制造轻微动静。
2) 完成第一次试探（强度 10~20，波形 constant）。
3) 不要在第一击后停下等待指令，直接进入下一段行动。"""


_lock = threading.Lock()
_state = {"hr": None, "ibi": None, "hr_updated_at": None, "img_updated_at": None,
          "last_command": None, "last_command_at": None, "serial_ok": False,
          "fuse_reason": None, "ai_error": None, "ai_text": None, "ai_reasoning": None,
          "hrv_rmssd": None, "hrv_baseline": None, "hrv_dev_pct": None, "hrv_stage": "idle"}
serial_link = None
op_log = []
MAX_OP_LOG = 40

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
            if m2:
                ibi = int(m2.group(1))
                if ibi <= 0:
                    ibi = None      # 固件在无 RR-interval 时会上报 IBI:0，视为「无 IBI」
            with _lock:
                _state["hr"] = hr; _state["ibi"] = ibi; _state["hr_updated_at"] = time.time()
            try:
                with open(LATEST_HR, "w", encoding="utf-8") as f:
                    json.dump({"hr": hr, "ibi": ibi, "ts": time.time()}, f)
            except Exception: pass
            # 每次上报都推进 HRV 滑动窗口与基线学习
            try:
                _update_hrv_sample(ibi, hr)
            except Exception as e:
                log("[HRV] 更新失败: %s" % e)
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

# ==================== HRV（心率变异性）引擎 ====================
# 目的：让 AI 读到的不再是「一个孤立的 IBI 数字」，而是「HRV 相对本会话静息基线的偏离」。
# 基线来源（优先级）：手动填写的 RESTING_HR / BASELINE_RMSSD > 本会话开局自适应采集 > 不可用。
MAX_IBI_SAMPLES = 512
SESSION_REST_LEARN_S = 90.0     # 开局采集静息基线的时长（秒）
SESSION_REST_MIN_SAMPLES = 12   # 判定基线可用所需的最少有效 IBI 数量
SESSION_GAP_RESET_S = 600.0     # 静息超过该时长未收到心率 → 重新采集
STALE_WEARABLE_S = 10.0         # 心率数据超过该时长未更新视为不可用（防止用陈旧值做限幅判断）

hrv_state = {"ibi": [], "rmssd": None, "baseline_hr": None, "baseline_rmssd": None,
             "session_start": None, "baseline_source": "none", "last_hr": None,
             "last_update": 0.0, "fingerprint": None,
             # last_reject_ibi：上一个被判为「与窗口末端差分过大」而被丢弃的样本，
             #                用于区分「单个伪迹」与「心率水平真的换了台阶」（见 _update_hrv_sample）
             # segment_resets：本会话因台阶跃迁而重置 IBI 窗口的次数（仅用于诊断/测试）
             "last_reject_ibi": None, "segment_resets": 0}


def _smooth_ratio(curr, base, k=0.05):
    """渐变映射，返回开区间 (0, 1)；curr/base < 1 时单调下降。用于让基线缓慢自适应、避免跳变。"""
    r = float(curr) / float(base)
    return 0.5 * (1.0 + (2.0 / math.pi) * math.atan((r - 1.0) / k))


def reset_hrv_session(reason=""):
    with _lock:
        hrv_state["ibi"] = []
        hrv_state["rmssd"] = None
        hrv_state["baseline_hr"] = None
        hrv_state["baseline_rmssd"] = None
        hrv_state["session_start"] = None
        hrv_state["baseline_source"] = "none"
        hrv_state["fingerprint"] = None
        hrv_state["last_reject_ibi"] = None
        hrv_state["segment_resets"] = 0
        _state["hrv_rmssd"] = None
        _state["hrv_baseline"] = None
        _state["hrv_dev_pct"] = None
        _state["hrv_stage"] = "idle"
    if reason:
        log("[HRV] 会话已重置（%s）" % reason)


def classify_hrv(hr, rmssd, base_hr, base_rmssd, has_data, source="none"):
    """返回 (状态码, 阶段, 面向 AI 的状态文本, 偏离百分比或 None)。"""
    if not has_data or hr is None:
        return ("unknown", "no_data",
                "当前无可用生理数据（手环未连接或信号中断，ESP32 串口未上报）。"
                "请只依据画面推进剧情，不要假装知道玩家的身体状态。", None)

    if not base_hr or not base_rmssd:
        return ("learning", "collecting",
                "正在采集你的静息基线（开局约 60~90 秒，或心率刚恢复）。"
                "当前估算 HRV(RMSSD) %s ms；基线未就绪时系统不做 HRV 限幅，"
                "AI 只依据画面与绝对心率判断。" % (("%.0f" % rmssd) if rmssd else "未知"), None)

    dev = None
    if rmssd and base_rmssd:
        dev = (rmssd - base_rmssd) / base_rmssd * 100.0

    if dev is None:
        dev_code, dev_cn = "unknown", "HRV 数据不足"
    elif dev <= -40:
        dev_code, dev_cn = "overload", "HRV 显著低于静息基线（自主神经已明显负荷）"
    elif dev <= -20:
        dev_code, dev_cn = "tense", "HRV 低于静息基线（正在紧张或开始疲劳）"
    elif dev >= 25:
        dev_code, dev_cn = "relaxed", "HRV 高于静息基线（放松且状态良好）"
    else:
        dev_code, dev_cn = "normal", "HRV 接近静息基线"

    hr_dev = hr - base_hr
    if hr_dev >= 50 or (dev is not None and dev <= -40):
        state_code, state_cn, tendency = "overload", "接近极限（过载）", "降低强度或 STOP"
    elif hr_dev >= 30 or (dev is not None and dev <= -20):
        state_code, state_cn, tendency = "tense", "紧张／开始疲劳", "只做轻度试探，避免推进"
    elif dev is not None and dev >= 25 and hr_dev <= 15:
        state_code, state_cn, tendency = "excited", "兴奋且耐受良好", "可以适度提高强度"
    else:
        state_code, state_cn, tendency = "normal", "状态平稳", "按剧情节奏正常推进"

    text = (
        "【本会话生理状态】（系统实测，权威数据）\n"
        "- 静息基线：心率 %d bpm / HRV %.0f ms（来源：%s）\n"
        "- 当前：心率 %d bpm（相对基线 %+d bpm）/ HRV %.0f ms（相对基线 %s）\n"
        "- 判定：%s；%s\n"
        "- 战术指引：%s"
    ) % (
        base_hr, base_rmssd, source,
        hr, hr_dev,
        rmssd if rmssd else 0.0,
        ("%+.0f%%" % dev) if dev is not None else "无法比较（当前样本不足）",
        state_cn, dev_cn, tendency,
    )
    return (state_code, "adaptive", text, dev)


def _update_hrv_sample(ibi, hr):
    """收到一次心率上报时调用：维护 IBI 滑动窗口、推进会话计时、更新/自适应静息基线。"""
    now = time.time()
    segment_reset = False
    with _lock:
        gap_reset = (now - hrv_state["last_update"]) > SESSION_GAP_RESET_S
        hrv_state["last_hr"] = hr
        hrv_state["last_update"] = now
        if hrv_state["session_start"] is None or gap_reset:
            hrv_state["session_start"] = now
            if gap_reset:
                # 长时间无有效生理数据：重建基线，避免用一周前的数据做保护
                hrv_state["ibi"] = []
                if get_resting_hr() <= 0:
                    hrv_state["baseline_hr"] = None
                    hrv_state["baseline_rmssd"] = None
                    hrv_state["baseline_source"] = "none"

        if ibi is not None and 250 <= ibi <= 2500:   # 合理 IBI 区间（24~240 bpm）
            seq = hrv_state["ibi"]
            ibi_f = float(ibi)
            prev_reject = hrv_state.get("last_reject_ibi")
            # 与窗口末端样本差分 >250ms：只可能是「单个丢拍/运动伪迹」或「心率水平真的换了台阶」
            # （受惊、或设备重连造成的数据段拼接）。两者都不能任由这个巨大差分被 RMSSD 平方放大，
            # 但处理方式必须区分——早期实现一律「丢弃该样本且不推进参考点」，会踩这个坑：
            #   持续跃迁后，每个新样本都被拿去和那个**陈旧的末端样本**比较、全部判为异常，
            #   于是窗口被冻结在跃迁前的静息值上 → HRV 偏离恒为 0%、显示「HRV 接近静息基线」，
            #   HRV 限幅/熔断静默失效，直到旧样本熬过整个窗口（默认 120 秒）才自愈。
            if seq and abs(ibi_f - seq[-1][1]) > 250.0:
                if prev_reject is not None and abs(ibi_f - prev_reject) <= 250.0:
                    # 连续两个样本彼此接近、却都远离旧窗口 → 认定是真实台阶：
                    # 旧窗口属于另一个生理段，整体作废（只重置样本，静息基线保留并继续作为比较基准）
                    hrv_state["last_diff_reject"] = hrv_state.get("last_diff_reject", 0) + 1
                    hrv_state["segment_resets"] = hrv_state.get("segment_resets", 0) + 1
                    seq.clear()
                    seq.append((now, ibi_f))
                    hrv_state["last_reject_ibi"] = None
                    segment_reset = True
                else:
                    # 单个伪迹/丢拍：丢弃该样本，窗口保持不变，等待下一个样本判定
                    hrv_state["last_diff_reject"] = hrv_state.get("last_diff_reject", 0) + 1
                    hrv_state["last_reject_ibi"] = ibi_f
            else:
                seq.append((now, ibi_f))
                hrv_state["last_reject_ibi"] = None
            cutoff = now - get_hrv_window()
            while seq and seq[0][0] < cutoff:
                seq.pop(0)
            if len(seq) > MAX_IBI_SAMPLES:
                del seq[:len(seq) - MAX_IBI_SAMPLES]

        seq = list(hrv_state["ibi"])
        manual_hr = get_resting_hr()
        manual_rmssd = int(get_config("BASELINE_RMSSD", 0) or 0)
        session_age = now - (hrv_state["session_start"] or now)

    if segment_reset:
        log("[HRV] 检测到心率台阶跃迁：IBI 窗口已重置（静息基线保留，下一拍起重新累积 RMSSD）")

    ibis = [v for _, v in seq if 250 <= v <= 2500]
    # RMSSD = sqrt(mean(diff(IBI)^2))：短时心率变异性最常用的指标
    rmssd = None
    if len(ibis) >= 2:
        diffs = [ibis[i + 1] - ibis[i] for i in range(len(ibis) - 1)]
        rmssd = math.sqrt(sum(d * d for d in diffs) / len(diffs))

    with _lock:
        hrv_state["rmssd"] = rmssd
        # ---- 基线判定（手动优先）----
        if manual_hr > 0:
            hrv_state["baseline_hr"] = manual_hr
            if manual_rmssd > 0:
                hrv_state["baseline_rmssd"] = manual_rmssd
                hrv_state["baseline_source"] = "手动填写静息心率+HRV基线"
            else:
                if hrv_state["baseline_rmssd"] is None and rmssd is not None:
                    hrv_state["baseline_rmssd"] = rmssd
                hrv_state["baseline_source"] = "手动填写静息心率"
        elif (hrv_state["baseline_rmssd"] is None
              and rmssd is not None
              and len(ibis) >= SESSION_REST_MIN_SAMPLES
              and session_age <= SESSION_REST_LEARN_S):
            hrv_state["baseline_hr"] = hr
            hrv_state["baseline_rmssd"] = rmssd
            hrv_state["baseline_source"] = "本会话开局自适应采集"
            log("[HRV] 已建立静息基线: 心率 %s bpm / RMSSD %.1f ms（%d 个 IBI 样本）"
                % (hr, rmssd, len(ibis)))
        elif hrv_state["baseline_rmssd"] is not None and rmssd is not None:
            # 只允许基线朝「更放松」方向缓慢自适应：避免长时间紧张把基线一路拖低、
            # 最后让保护阈值失效（这是 HRV 类安全策略最常见的失效模式）。
            if _smooth_ratio(hr, hrv_state["baseline_hr"] or hr) > 0.985:
                hrv_state["baseline_hr"] = min(hrv_state["baseline_hr"] or hr, hr)
            if _smooth_ratio(rmssd, hrv_state["baseline_rmssd"]) > 0.985:
                hrv_state["baseline_rmssd"] = min(hrv_state["baseline_rmssd"], rmssd)


def get_hrv_status(hr):
    """主循环每次决策前调用：返回 (状态码, 阶段, 面向 AI 的状态文本, RMSSD偏离%)。"""
    now = time.time()
    with _lock:
        rmssd = hrv_state["rmssd"]
        base_hr = hrv_state["baseline_hr"]
        base_rmssd = hrv_state["baseline_rmssd"]
        source = hrv_state["baseline_source"]
        fresh = (now - hrv_state["last_update"]) <= STALE_WEARABLE_S

    code, stage, text, dev = classify_hrv(hr, rmssd, base_hr, base_rmssd, fresh, source)
    with _lock:
        _state["hrv_rmssd"] = rmssd
        _state["hrv_baseline"] = base_rmssd
        _state["hrv_dev_pct"] = dev
        _state["hrv_stage"] = stage
    return code, stage, text, dev

def ask_deepseek(img_b64, hr, ibi, hrv_text=None, hrv_code=None):
    if not DEEPSEEK_API_KEY:
        with _lock: _state["ai_error"] = "未配置 DEEPSEEK_API_KEY"
        return None
    hr_txt = "%s bpm" % hr if hr is not None else "未知"

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

    # 生理状态块：HRV 引擎给出的「相对静息基线的偏离」，而不是孤立的 IBI 数字
    if not hrv_text:
        hrv_text = "【本会话生理状态】无可用 HRV 数据（手环未接入或尚未建立基线）。"

    event_info = ""
    if active_event:
        event_info = (f"【当前随机事件】{active_event['title']}: {active_event['description']} "
                      f"玩家可选项: {', '.join([o['text'] for o in active_event['options']])}。"
                      f"风险与收益: {active_event.get('risk_reward', '')}")
    elif player_choices_log:
        recent = player_choices_log[-5:]
        event_info = "【玩家历史选择】" + "; ".join([f"{c['option']}" for c in recent])

    # DLC6 历史摘要
    history_text = ""
    if conversation_history:
        recent_hist = conversation_history[-5:]
        history_text = "【近期互动历史（节选）】\n" + "\n".join([
            f"玩家: {h['user']}\nAI: {h['ai']}" for h in recent_hist
        ])

    system_prompt = custom_system_prompt if custom_system_prompt else SYSTEM_PROMPT

    user_text = (
        f"{hrv_text}\n"
        f"- 绝对心率：{hr_txt}\n"
        f"- 语音输入：{audio_info}\n"
        f"{event_info}\n"
        f"{history_text}\n"
        "以上为系统本轮实测状态快照。请结合 latest.jpg 画面推进剧情，"
        "并在正文最后单独一行输出一条 SET 或 STOP 指令。"
    )

    payload = {
        "model": DEEPSEEK_MODEL,
        # 本系统为「无状态单轮调用」：每一轮都重新提供完整状态快照与近 5 轮摘要，
        # 不依赖多轮消息数组（因此不存在上下文随轮次膨胀、被历史挤爆 max_tokens 的问题）。
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img_b64}},
            ]},
        ],
        "max_tokens": DEEPSEEK_MAX_TOKENS,
        "temperature": 1.0,
        "stream": False,
    }
    # thinking 控制：把推理预算限制住，既保留剧情质量，又避免 reasoning 吃光 max_tokens 导致正文空回复
    if DEEPSEEK_THINKING == "disabled":
        payload["thinking"] = {"type": "disabled"}
    elif DEEPSEEK_THINKING == "enabled" and DEEPSEEK_THINKING_BUDGET > 0:
        payload["thinking"] = {"type": "enabled", "budget_tokens": DEEPSEEK_THINKING_BUDGET}

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

        # DLC6 记录历史
        with _lock:
            conversation_history.append({
                "user": "心率:%s / HRV偏离:%s / 语音:%s / 事件:%s" % (
                    hr_txt,
                    ("%+.0f%%" % _state["hrv_dev_pct"]) if _state.get("hrv_dev_pct") is not None else "未知",
                    audio_text or "无",
                    active_event['title'] if active_event else '无',
                ),
                "ai": text
            })
            if len(conversation_history) > MAX_HISTORY:
                del conversation_history[:len(conversation_history) - MAX_HISTORY]

        return text
    except Exception as e:
        with _lock: _state["ai_error"] = str(e)
        log("[AI] 调用失败: %s" % e)
        return None

def parse_command(text):
    """只认「正文最后一行」的指令：避免剧情正文里出现的 STOP/SET 单词被误判成设备指令。"""
    if not text: return None
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines: return None
    # 从末尾往前看最近 3 个非空行（容忍指令后再跟一行署名/括号补充）
    for line in reversed(lines[-3:]):
        if re.fullmatch(r"STOP\b[。.!！]?", line, re.I): return "STOP"
        m = re.fullmatch(r"SET\s+(\d{1,3})\s+(\d{1,4})\s+([A-Za-z]+)[。.!！]?", line, re.I)
        if m:
            level = int(m.group(1)); dur = int(m.group(2)); wave = m.group(3).lower()
            if wave not in WAVE_SET: wave = "constant"
            return "SET %d %d %s" % (level, dur, wave)
    return None

def clamp_command(cmd, hr, hrv_dev=None):
    """硬约束（不依赖 AI 自觉）：绝对心率阈值 + 相对静息基线的偏离（心率差 / HRV 降幅）。"""
    if cmd == "STOP": return cmd
    if not cmd.startswith("SET "): return cmd
    parts = cmd.split(); level = int(parts[1]); dur = int(parts[2]); wave = parts[3]
    reasons = []
    hr_stop = get_hr_stop()
    hr_limit = get_hr_limit()
    hr_limit_max = get_hr_limit_max()
    pc_cap = get_pc_level_cap()
    max_dur = get_max_duration()
    base_hr = None
    with _lock:
        base_hr = hrv_state["baseline_hr"]

    # --- 熔断层 1：绝对心率阈值 ---
    if hr is not None and hr > hr_stop:
        with _lock: _state["fuse_reason"] = "心率 %d > %d（绝对阈值）, 强制 STOP" % (hr, hr_stop)
        return "STOP"

    # --- 熔断层 2：相对基线的偏离（HRV 跌破基线或心率远超静息） ---
    if base_hr:
        hr_stop_delta = int(get_config("HR_STOP_DELTA", 65) or 65)
        hrv_stop_drop = int(get_config("HRV_STOP_DROP_PCT", 60) or 60)
        if hr is not None and (hr - base_hr) >= hr_stop_delta and hr >= hr_limit:
            with _lock:
                _state["fuse_reason"] = "心率 %d 超静息基线 %d bpm, 强制 STOP" % (hr, hr - base_hr)
            return "STOP"
        if hrv_dev is not None and hrv_dev <= -abs(hrv_stop_drop):
            with _lock:
                _state["fuse_reason"] = "HRV 较基线下降 %.0f%%, 强制 STOP" % abs(hrv_dev)
            return "STOP"

    # --- 限幅层 1：绝对心率阈值 ---
    if hr is not None and hr >= hr_limit:
        level = min(level, hr_limit_max); reasons.append("心率 %d 偏高" % hr)

    # --- 限幅层 2：相对基线偏离 ---
    if base_hr:
        hr_limit_delta = int(get_config("HR_LIMIT_DELTA", 45) or 45)
        hrv_limit_drop = int(get_config("HRV_LIMIT_DROP_PCT", 40) or 40)
        if hr is not None and (hr - base_hr) >= hr_limit_delta:
            level = min(level, hr_limit_max); reasons.append("心率超基线 %+d bpm" % (hr - base_hr))
        if hrv_dev is not None and hrv_dev <= -abs(hrv_limit_drop):
            level = min(level, hr_limit_max); reasons.append("HRV 较基线降 %.0f%%" % abs(hrv_dev))

    level = min(level, pc_cap)
    dur = max(1, min(dur, max_dur))
    if reasons:
        # 限幅后不超过 max_dur 的一半，缩短单次暴露时间
        dur = min(dur, max(3, max_dur // 2))
    with _lock:
        _state["fuse_reason"] = ("; ".join(reasons) + "，限幅 %d" % level) if reasons else None
    return "SET %d %d %s" % (level, dur, wave)

def send_command(cmd, source):
    with _lock:
        _state["last_command"] = cmd; _state["last_command_at"] = time.time()
    log("[下发:%s] %s" % (source, cmd))
    return serial_link.send(cmd)

def main_loop():
    global active_event, event_timestamp, latest_audio_text, latest_audio_features
    last_beat = 0.0
    frame = 0
    while True:
        hr = None
        ibi = None
        try:
            now = time.time()
            if now - last_beat >= HEARTBEAT_INTERVAL:
                serial_link.send("BEAT"); last_beat = now

            # ===== 事件超时服务端处理：玩家在 EVENT_TIMEOUT 内未选择则自动随机选择 =====
            # 放在每轮最前面（心跳后、读图/熔断前），任何分支 continue 都不会漏检
            if active_event is not None and event_timestamp is not None and (now - event_timestamp) > EVENT_TIMEOUT:
                evt_id = active_event.get("id", "unknown")
                options = active_event.get("options", [])
                if options:
                    option = random.choice(options)["text"]
                    with _lock:
                        player_choices_log.append({
                            "event_id": evt_id,
                            "option": option,
                            "ts": now
                        })
                        if len(player_choices_log) > MAX_CHOICE_LOG:
                            del player_choices_log[:len(player_choices_log) - MAX_CHOICE_LOG]

                        conversation_history.append({
                            "user": f"玩家在事件 {evt_id} 中选择了：{option}",
                            "ai": "(等待玩家行动)"
                        })
                        if len(conversation_history) > MAX_HISTORY:
                            del conversation_history[:len(conversation_history) - MAX_HISTORY]

                        latest_audio_text = f"玩家超时未选择，系统自动选择：{option}"
                        latest_audio_features = {"volume": 0, "pitch": 0, "volumeChange": 0, "timestamp": 0}
                    log_op("event", "事件超时自动选择", "%s -> %s" % (evt_id, option))
                else:
                    log_op("warn", "事件超时但无选项", evt_id)
                active_event = None
                event_timestamp = None

            # ===== 安全前置：心率熔断必须优先于画面新鲜度判断 =====
            # 若把熔断放在「图片过期 → continue」之后，会出现「平板息屏/断流时心率飙高却没人发 STOP」
            # 的危险窗口（上一帧的长时长指令还在设备上执行）。因此这里先判心率。
            hr, ibi = get_hr()
            hr_stop = get_hr_stop()
            if hr is not None and hr > hr_stop:
                with _lock: _state["fuse_reason"] = "心率 %d > %d, 强制 STOP" % (hr, hr_stop)
                log_op("fuse", "心率过高熔断", "心率 %d > %d, 强制 STOP" % (hr, hr_stop))
                send_command("STOP", source="熔断")
                time.sleep(INTERVAL_SECONDS); continue

            img_b64, mtime = load_latest_image()
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

            hr_limit = get_hr_limit()
            if hr is not None and hr >= hr_limit:
                log_op("warn", "心率偏高", "心率 %d 已进入限幅区（>= %d），本轮流派将受硬约束" % (hr, hr_limit))

            # HRV：把「相对静息基线的偏离」算出来，同时喂给 AI 与硬约束
            _, hrv_stage, hrv_text, hrv_dev = get_hrv_status(hr)

            text = ask_deepseek(img_b64, hr, ibi, hrv_text=hrv_text)

            if text:
                m = re.search(r"\[EVENT:(\w+)\]", text, re.I)
                if m:
                    event_id = m.group(1)
                    evt = next((e for e in EVENT_POOL if e.get("id") == event_id), None)
                    if evt:
                        hr_limit_max = get_hr_limit_max()
                        if hr is not None and hr > hr_stop:
                            if not evt.get("relaxing", False):
                                log_op("warn", "事件触发被熔断阻止", event_id)
                            else:
                                active_event = evt
                                event_timestamp = time.time()
                                log_op("event", "触发放松事件", event_id)
                        elif hr is not None and hr >= hr_limit:
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
                        time.sleep(INTERVAL_SECONDS)
                        continue

            cmd = parse_command(text)
            if cmd:
                cmd = clamp_command(cmd, hr, hrv_dev)
                ok = send_command(cmd, source="AI")
                log_op("cmd", "执行 " + cmd, "已下发" if ok else "无串口,仅记录")
            else:
                with _lock: _state["ai_error"] = "AI 输出无法解析: %r" % text
                log_op("warn", "AI 输出无法解析", repr(text)[:120])
        except Exception as e:
            log("[主循环] 异常: %s" % e)
        finally:
            # DLC7 记录数据点
            try:
                with _lock:
                    data_log.append({
                        "ts": time.time(),
                        "hr": hr,
                        "ibi": ibi,
                        "command": _state.get("last_command"),
                        "fuse": _state.get("fuse_reason"),
                        "ai_text": _state.get("ai_text") or ""
                    })
                    if len(data_log) > MAX_DATA_POINTS:
                        del data_log[:len(data_log) - MAX_DATA_POINTS]
            except Exception:
                pass
        time.sleep(INTERVAL_SECONDS)

# ==================== 认证部分 ====================
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

auth = HTTPBasicAuth()
# 凭据一律从环境变量（.env，已被 .gitignore 忽略）读取：写死在源码里等于把口令公开到仓库。
# 未配置时**自动生成随机口令并在启动日志里打印一次**——仓库里不再存在「可以直接用」的弱默认口令
# （旧版本默认 123456，配合内网穿透等于把服务公开给所有人）。
ACCESS_KEY = os.environ.get("ACCESS_KEY", "").strip()
AUTO_ACCESS_KEY = False
if not ACCESS_KEY:
    ACCESS_KEY = "hrv-" + secrets.token_urlsafe(9)
    AUTO_ACCESS_KEY = True

BASIC_USER = os.environ.get("BASIC_USER", "").strip() or "admin"
BASIC_PASS = os.environ.get("BASIC_PASS", "").strip()
AUTO_BASIC_PASS = False
if not BASIC_PASS:
    BASIC_PASS = secrets.token_urlsafe(12)
    AUTO_BASIC_PASS = True
USERS = {BASIC_USER: BASIC_PASS}

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
    s["thinking"] = DEEPSEEK_THINKING
    s["max_tokens"] = DEEPSEEK_MAX_TOKENS
    s["now"] = time.time()
    s["active_event"] = active_event
    s["event_timestamp"] = event_timestamp
    s["data_log"] = data_log
    with _lock:
        s["hrv"] = {
            "rmssd": hrv_state["rmssd"],
            "baseline_rmssd": hrv_state["baseline_rmssd"],
            "baseline_hr": hrv_state["baseline_hr"],
            "source": hrv_state["baseline_source"],
            "samples": len(hrv_state["ibi"]),
        }
    return jsonify(s)

@app.route("/api/event_choice", methods=["POST"])
@require_access
def event_choice():
    global active_event, event_timestamp, latest_audio_text, latest_audio_features
    data = request.get_json(force=True, silent=True) or {}
    option_text = str(data.get("option") or "").strip()
    if not option_text:
        return jsonify({"ok": False, "error": "empty option"}), 400

    # 丢弃「迟到/过期」的选择：前端 15s 倒计时与服务端 15s 兜底可能先后触发，
    # 若不做校验，一个已经属于上一个事件的选择会被记到「当前」这个新事件上，
    # 于是剧情走向与生理状态记录双双错位。
    sent_id = str(data.get("event_id") or "").strip()
    if active_event is None:
        log_op("warn", "丢弃事件选择（当前无进行中的事件）", option_text)
        return jsonify({"ok": False, "error": "no active event"}), 409
    if sent_id and sent_id != active_event.get("id"):
        log_op("warn", "丢弃过期的事件选择",
               "选项=%s, 来自事件=%s, 当前事件=%s" % (option_text, sent_id, active_event.get("id")))
        return jsonify({"ok": False, "error": "stale event choice"}), 409

    with _lock:
        player_choices_log.append({
            "event_id": active_event.get("id") if active_event else "unknown",
            "option": option_text,
            "ts": time.time()
        })
        if len(player_choices_log) > MAX_CHOICE_LOG:
            del player_choices_log[:len(player_choices_log) - MAX_CHOICE_LOG]

        conversation_history.append({
            "user": f"玩家在事件 {active_event.get('id') if active_event else 'unknown'} 中选择了：{option_text}",
            "ai": "(等待玩家行动)"
        })
        if len(conversation_history) > MAX_HISTORY:
            del conversation_history[:len(conversation_history) - MAX_HISTORY]

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

# ---------- TTS 接口 ----------
@app.route("/tts", methods=["POST"])
@require_access
def tts():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400
    if len(text) > 500:
        text = text[:500]

    try:
        voice = "zh-CN-XiaoxiaoNeural"
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

# ---------- 自定义角色接口 ----------
@app.route("/api/custom_role", methods=["GET", "POST"])
@require_access
def custom_role():
    global custom_system_prompt
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        prompt = str(data.get("prompt") or "").strip()
        if prompt:
            custom_system_prompt = prompt
            return jsonify({"ok": True, "message": "自定义角色已保存"})
        else:
            custom_system_prompt = None
            return jsonify({"ok": True, "message": "已恢复默认角色"})
    else:
        return jsonify({"ok": True, "custom_system_prompt": custom_system_prompt})

# ---------- DLC9 Web 配置后台接口 ----------
@app.route("/api/config", methods=["GET", "POST"])
@require_access
def api_config():
    if request.method == "GET":
        cfg = get_current_thresholds()
        with _lock:
            cfg["HRV_BASELINE_ACTIVE"] = hrv_state["baseline_rmssd"]
            cfg["HRV_SOURCE"] = hrv_state["baseline_source"]
        return jsonify({"ok": True, "config": cfg})
    else:
        data = request.get_json(force=True, silent=True) or {}

        def _num(key, lo, hi):
            """带范围校验的数值写入，防止把保护阈值改成无效值。"""
            v = data.get(key)
            if v is None: return
            if not isinstance(v, (int, float)): return
            v = int(v)
            if v < lo or v > hi:
                log("[配置] 拒绝越界值 %s=%s（允许 %d~%d）" % (key, v, lo, hi))
                return
            set_config(key, v)

        _num("HR_STOP_ABOVE", 60, 220)
        _num("HR_LIMIT_ABOVE", 50, 200)
        _num("HR_LIMIT_MAX", 0, 80)
        _num("PC_LEVEL_CAP", 0, 80)
        _num("MAX_DURATION_S", 1, 3600)
        # ---- HRV 参数（静息心率可手填，0 = 用本会话自适应基线）----
        _num("RESTING_HR", 0, 220)
        _num("BASELINE_RMSSD", 0, 500)
        _num("HRV_LIMIT_DROP_PCT", 10, 90)
        _num("HRV_STOP_DROP_PCT", 20, 95)
        _num("HR_LIMIT_DELTA", 10, 150)
        _num("HR_STOP_DELTA", 20, 180)
        _num("HRV_WINDOW_SEC", 15, 600)

        # 手动改静息心率/基线后，重建 HRV 会话，避免旧样本与新基线混用
        if any(k in data for k in ("RESTING_HR", "BASELINE_RMSSD")) and not data.get("reload_events"):
            reset_hrv_session("静息心率/基线被手动修改")

        if data.get("reset_hrv"):
            reset_hrv_session("管理后台手动重置")

        if data.get("reload_events"):
            load_events()

        cfg = get_current_thresholds()
        with _lock:
            cfg["HRV_BASELINE_ACTIVE"] = hrv_state["baseline_rmssd"]
            cfg["HRV_SOURCE"] = hrv_state["baseline_source"]
        return jsonify({"ok": True, "config": cfg})

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
    if AUTO_ACCESS_KEY:
        log("[安全] 未配置 ACCESS_KEY，本次已自动生成访问口令：%s"
            "（访问 %s://%s:%d/?key=%s；建议写进 .env 固定下来）" % (ACCESS_KEY, proto, args.host, args.port, ACCESS_KEY))
    if AUTO_BASIC_PASS:
        log("[安全] 未配置 BASIC_PASS，本次已自动生成 Basic 口令：用户 %s / 口令 %s" % (BASIC_USER, BASIC_PASS))
    if not (AUTO_ACCESS_KEY or AUTO_BASIC_PASS):
        log("[安全] 访问口令已从 .env 载入（不会打印明文）")
    print("服务启动: %s://%s:%d/  AI模型=%s  串口=%s(ok=%s)" % (proto, args.host, args.port, DEEPSEEK_MODEL, SERIAL_PORT, serial_link.ok))
    app.run(host=args.host, port=args.port, ssl_context=ssl_ctx, threaded=True)