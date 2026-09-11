# -*- coding: utf-8 -*-
r"""Tentacle HRV 逻辑单元测试（不需要手环 / 串口 / API Key）

覆盖：
  1. HRV 引擎：静息基线采集 → 紧张状态识别 → 过载识别
  2. 静息心率手动覆盖（RESTING_HR）
  3. clamp_command 的绝对阈值 + 相对基线偏离双重约束
  4. parse_command 只认正文最后一行
  5. 回归：心率台阶跃迁不再锁死 IBI 窗口（旧实现会让 HRV 限幅静默失效最长 120 秒）
  6. 回归：单个伪迹/丢拍不清空窗口（不误伤正常样本）

运行：.\venv\Scripts\python.exe tests\test_hrv_and_clamp.py
注意：导入 server.py 会加载 Vosk 模型（约 10~60 秒），属预期。
"""
import io
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import server as S  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s %s" % ("[PASS]" if cond else "[FAIL]", name, detail))


def feed(ibis, hr):
    """按顺序把 IBI 序列喂给 HRV 引擎（模拟手环连续上报）。"""
    for ibi in ibis:
        S._update_hrv_sample(ibi, hr)


print("=== 1. 静息基线采集 ===")
S.reset_hrv_session()
# 静息：IBI≈1000ms(60bpm)，抖动 ±15ms → RMSSD≈21ms
rest_ibis = [1000 + (15 if i % 2 else -15) for i in range(24)]
feed(rest_ibis, 60)
code, stage, text, dev = S.get_hrv_status(60)
base = S.hrv_state["baseline_rmssd"]
check("基线已建立", base is not None, "baseline_rmssd=%.1f ms" % (base or -1))
check("基线来源=自适应采集", S.hrv_state["baseline_source"] == "本会话开局自适应采集",
      S.hrv_state["baseline_source"])
check("静息态判定为正常/兴奋而非紧张", code in ("normal", "excited"), "code=%s" % code)

print()
print("=== 2. 紧张状态：HRV 显著下降（含状态突变过滤验证）===")
# 紧张：IBI≈650ms(92bpm)，抖动极小 ±5ms → RMSSD≈7ms（较基线 -67%）
# 注意：由静息(1000ms)切到紧张(650ms)时，相邻样本差 350ms，属「生理不可能差分」，
# 引擎应把它剔除而非计入 RMSSD（否则 RMSSD 会被平方放大、假性抬高）。
tense_ibis = [650 + (5 if i % 2 else -5) for i in range(24)]
feed(tense_ibis, 92)
rejected = S.hrv_state.get("last_diff_reject", 0)
check("状态突变产生的异常差分被剔除", rejected >= 1, "已剔除 %d 个差分" % rejected)
check("剔除后 RMSSD 不被假性抬高",
      S.hrv_state["rmssd"] is None or S.hrv_state["rmssd"] < 20,
      "rmssd=%s" % (("%.1f" % S.hrv_state["rmssd"]) if S.hrv_state["rmssd"] else None))
code, stage, text, dev = S.get_hrv_status(92)
check("HRV 相对基线下降", dev is not None and dev < -50, "dev=%.0f%%" % (dev if dev is not None else 0))
check("判定为过载", code == "overload", "code=%s" % code)
check("状态文本含战术指引", "战术指引" in text)

print()
print("=== 3. 静息心率手动覆盖（RESTING_HR） ===")
S.reset_hrv_session()
S.set_config("RESTING_HR", 58)
feed([1030 + (15 if i % 2 else -15) for i in range(24)], 58)
code, stage, text, dev = S.get_hrv_status(58)
check("基线取自手填静息心率", S.hrv_state["baseline_hr"] == 58,
      "baseline_hr=%s" % S.hrv_state["baseline_hr"])
check("基线来源=手动填写", "手动填写" in S.hrv_state["baseline_source"],
      S.hrv_state["baseline_source"])
check("手填静息心率时 HRV 偏离接近 0", dev is not None and abs(dev) < 25,
      "dev=%.0f%%" % (dev if dev is not None else 0))
S.set_config("RESTING_HR", 0)

print()
print("=== 4. clamp_command 双重约束 ===")
S.reset_hrv_session()
S.set_config("RESTING_HR", 60)   # 静息基线 60 bpm
feed(rest_ibis, 60)
S.get_hrv_status(60)

# 4a 正常状态：不触发限幅
c = S.clamp_command("SET 70 10 random", 62, -5)
check("正常状态不触发限幅", c == "SET 70 10 random", c)

# 4b 绝对阈值熔断：>130
c = S.clamp_command("SET 50 10 sine", 138, -10)
check("绝对心率 >130 强制 STOP", c == "STOP", c)

# 4c 相对基线熔断：心率超静息 65+ bpm（60+65=125，未到 130 绝对阈值，也必须 STOP）
c = S.clamp_command("SET 50 10 sine", 128, -10)
check("心率超基线 65+ 强制 STOP", c == "STOP", c)

# 4d HRV 熔断：降幅 >= 60%
c = S.clamp_command("SET 50 10 sine", 70, -70)
check("HRV 降幅 >=60% 强制 STOP", c == "STOP", c)

# 4e HRV 限幅：降幅 >= 40% 但 < 60%
c = S.clamp_command("SET 75 20 pulse", 80, -45)
check("HRV 降幅 >=40% 触发限幅至 50", c.startswith("SET 50 ") and "pulse" in c, c)

# 4f 心率超基线限幅（45~65 之间）
c = S.clamp_command("SET 75 20 pulse", 108, -5)
check("心率超基线 45+ 触发限幅", c.startswith("SET 50 "), c)

# 4g PC 强度上限
c = S.clamp_command("SET 100 10 constant", 70, -5)
check("PC 强度上限钳制到 80", c.startswith("SET 80 "), c)

# 4h STOP 原样返回
check("STOP 原样返回", S.clamp_command("STOP", 150, -80) == "STOP")

print()
print("=== 5. 无数据时不误判 ===")
S.reset_hrv_session()
code, stage, text, dev = S.get_hrv_status(None)
check("无心率数据判定为 unknown/no_data", code == "unknown" and stage == "no_data", code)
check("无数据时不触发 STOP（hr=None）", S.clamp_command("SET 40 8 sine", None, None) == "SET 40 8 sine")

print()
print("=== 6. 回归：心率台阶跃迁不再锁死 HRV 窗口 ===")
# 旧实现：差分 >250ms 的样本只被丢弃、不推进比较基准，于是持续跃迁后每个新样本都被拿去
# 和陈旧的末端样本比较、全判为异常 → 窗口冻结在跃迁前的静息值 → dev 恒为 0%、
# HRV 限幅/熔断静默失效，直到旧样本熬过整个窗口（默认 120 秒）才自愈。
S.reset_hrv_session()
S.set_config("RESTING_HR", 60)
feed(rest_ibis, 60)
S.get_hrv_status(60)
n_before = len(S.hrv_state["ibi"])
feed([650 + (5 if i % 2 else -5) for i in range(12)], 92)   # 阶跃到 92bpm
n_after = len(S.hrv_state["ibi"])
code, stage, text, dev = S.get_hrv_status(92)
check("窗口未被冻结在旧静息样本上", n_after < n_before, "窗口 %d → %d" % (n_before, n_after))
check("窗口已按新水平重建", n_after >= 8, "新窗口样本数=%d" % n_after)
check("跃迁被记录为段重置", S.hrv_state.get("segment_resets", 0) >= 1,
      "segment_resets=%s" % S.hrv_state.get("segment_resets"))
check("HRV 偏离立即可用（无需等 120 秒）", dev is not None and dev < -40, "dev=%s" % dev)
check("台阶跃迁后判定为过载", code == "overload", "code=%s" % code)
check("不再反复剔除新样本", S.hrv_state.get("last_reject_ibi") is None,
      "last_reject_ibi=%s" % S.hrv_state.get("last_reject_ibi"))

print()
print("=== 7. 回归：单个伪迹/丢拍不清空窗口（不误伤）===")
S.reset_hrv_session()
S.set_config("RESTING_HR", 60)
feed(rest_ibis, 60)
S.get_hrv_status(60)
resets_before = S.hrv_state.get("segment_resets", 0)
n0 = len(S.hrv_state["ibi"])
S._update_hrv_sample(1600, 60)     # 单个丢拍：多出一个长间期，应被丢弃
n1 = len(S.hrv_state["ibi"])
feed([1000 + (15 if i % 2 else -15) for i in range(10)], 60)
n2 = len(S.hrv_state["ibi"])
check("伪迹样本被丢弃", n1 == n0, "窗口 %d → %d" % (n0, n1))
check("伪迹未触发段重置", S.hrv_state.get("segment_resets", 0) == resets_before,
      "segment_resets=%s" % S.hrv_state.get("segment_resets"))
check("后续正常样本继续累积", n2 == n1 + 10, "窗口 %d → %d" % (n1, n2))
check("伪迹未污染 RMSSD", S.hrv_state["rmssd"] is not None and S.hrv_state["rmssd"] < 40,
      "rmssd=%s" % (S.hrv_state["rmssd"] if S.hrv_state["rmssd"] else None))
S.set_config("RESTING_HR", 0)

print()
print("=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + ", ".join(FAIL))
    sys.exit(1)
print("全部通过 ✅")
