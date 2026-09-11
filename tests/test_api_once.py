# -*- coding: utf-8 -*-
r"""单次真实 API 调用验证（成本约 1 次请求）

用途：确认 .env 的模型名、max_tokens、thinking 参数在本账号上真实可用，
     并回读 response.model / usage，验证「名实是否相符」。

运行：.\venv\Scripts\python.exe tests\test_api_once.py
注意：会消耗一次真实 API 调用（约 1~3k tokens）。API Key 从 .env 读取，绝不明文输出。
"""
import base64
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import cv2
import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()

key = os.environ.get("DEEPSEEK_API_KEY", "")
model = os.environ.get("DEEPSEEK_MODEL", "")
base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "1800"))
thinking = os.environ.get("DEEPSEEK_THINKING", "enabled").strip().lower()
budget = int(os.environ.get("DEEPSEEK_THINKING_BUDGET", "1024"))

assert key, "未配置 DEEPSEEK_API_KEY"
print("模型名 : %s" % model)
print("Key    : %s...%s（长度 %d，已脱敏）" % (key[:6], key[-4:], len(key)))
print("参数   : max_tokens=%d thinking=%s budget=%d" % (max_tokens, thinking, budget))

# 构造一张极小的合成图（避免使用真实 latest.jpg，减少隐私面）
img = np.zeros((64, 64, 3), np.uint8)
cv2.rectangle(img, (10, 10), (54, 54), (200, 200, 200), -1)
ok, buf = cv2.imencode(".jpg", img)
b64 = base64.b64encode(buf.tobytes()).decode()

payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": "你是测试助手。只回一行：OK 加一个数字。"},
        {"role": "user", "content": [
            {"type": "text", "text": "这是什么颜色方块？最后一行输出 SET 10 5 constant"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]},
    ],
    "max_tokens": max_tokens,
    "temperature": 1.0,
    "stream": False,
}
if thinking == "disabled":
    payload["thinking"] = {"type": "disabled"}
elif thinking == "enabled" and budget > 0:
    payload["thinking"] = {"type": "enabled", "budget_tokens": budget}

t0 = time.time()
try:
    r = requests.post(base + "/chat/completions",
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                      json=payload, timeout=60)
    dt = time.time() - t0
    print("HTTP   : %s  耗时 %.2fs" % (r.status_code, dt))
    if r.status_code != 200:
        print("错误体 : %s" % r.text[:600])
        sys.exit(1)
    j = r.json()
    msg = j["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    usage = j.get("usage", {})
    print("响应model : %s  %s" % (j.get("model"), "✅ 与 .env 一致" if j.get("model") == model else "⚠️ 不一致（名实不符）"))
    print("usage     : prompt=%s completion=%s reasoning=%s" % (
        usage.get("prompt_tokens"), usage.get("completion_tokens"),
        len(reasoning) and "有(%d 字)" % len(reasoning) or "无"))
    print("正文      : %s" % content[:300].replace("\n", " | "))
    print("最后一行  : %r" % (content.strip().splitlines()[-1] if content else ""))
except Exception as e:
    print("调用异常: %s" % e)
    sys.exit(1)
