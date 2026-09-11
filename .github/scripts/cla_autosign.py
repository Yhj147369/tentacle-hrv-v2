#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLA 自动签署（auto-sign）核心逻辑：由 .github/workflows/cla.yml 的 auto-sign job 调用。

流程（全部满足才写入名单）：
  1) 事件是 PR 评论（issue.pull_request 存在）；
  2) 评论者 == PR 作者；
  3) 正文（去装饰、去空白、小写）精确匹配「我同意 CLA / 我同意CLA / I agree / I agree to the CLA」；
  4) 作者尚未在 .cla/signatures.json（已在则幂等提示并跳过写入）。

写入名单后的"推送 main + 使 PR 的 cla-check 转绿"分两种模式：

[免维护模式]（env CLA_BOT_PAT 非空，推荐）：
  - 用 PAT 直推 main：owner 级 PAT 绕过分支保护，无需临时分支技巧；
  - 推送成功后，查询该 PR head 上 event=pull_request_target 且 path=.github/workflows/cla.yml 的
    失败 workflow run，用 PAT 逐一 POST /actions/runs/{id}/rerun —— 重跑时签名名单已含作者 →
    真实 cla-check run 自动转绿 → PR merge 即 CLEAN（消除"open 时红色 run 无法自动变绿"问题）。
  - 若 rerun 查询/触发接口不可用（如 PAT 权限不足），回退到降级模式的 REST 建绿并输出提示。

[降级模式]（无 CLA_BOT_PAT）：保持纯 GITHUB_TOKEN 行为不变 —— main 直推失败时用
  "临时分支 → 给 commit 建同名绿色 check run → 推 main → 删临时分支"；PR 变绿用 REST 创建
  同名 cla-check=success check run（merge 是否 CLEAN 取决于是否存在真实失败的 CLA run）。
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
JOB_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
PAT = os.environ.get("CLA_BOT_PAT") or ""  # 可为空 → 自动降级
EVENT_PATH = os.environ["GITHUB_EVENT_PATH"]
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
RUN_URL = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
WORKFLOW_PATH = ".github/workflows/cla.yml"

SIGN_FILE = ".cla/signatures.json"
STRIP_CHARS = "「」『』“”‘’\"'《》[](){}【】<>。.,，!！?？*_#`"
AGREE_PHRASES = {"我同意cla", "iagree", "iagreetothecla"}
DONE_MARKER = "已自动签署 CLA"
DONE_BODY = "已自动签署 CLA，检查已通过 ✅ 现在可以合并本 PR 了。"
ALREADY_MARKER = "你已在签名名单中"


def redact(s: str) -> str:
    """日志脱敏：绝不把 CLA_BOT_PAT 打进日志。"""
    if PAT and s:
        s = s.replace(PAT, "***")
    return s


def log(msg: str) -> None:
    print(redact(str(msg)), flush=True)


def norm(s: str) -> str:
    s = (s or "").strip().strip(STRIP_CHARS)
    return re.sub(r"\s+", "", s).lower()


def api(method: str, path: str, payload=None, token: str | None = None, fatal: bool = True):
    """GitHub REST 调用。token 缺省用 job 的 GITHUB_TOKEN；fatal=False 时失败仅记日志并返回 None。"""
    tok = token or JOB_TOKEN
    url = f"https://api.github.com{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cla-autosign",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else True  # 空 body 的成功(如 rerun 201)记为 True
    except urllib.error.HTTPError as e:
        body = redact(e.read().decode("utf-8", "replace"))
        log(f"!! HTTP {e.code} on {method} {path}: {body[:500]}")
        if not fatal:
            return None
        raise SystemExit(1)


def existing_comments(pr: int):
    return api("GET", f"/repos/{REPO}/issues/{pr}/comments") or []


def comment_exists(pr: int, marker: str) -> bool:
    return any(marker in (c.get("body") or "") for c in existing_comments(pr))


def post_comment(pr: int, body: str) -> None:
    api("POST", f"/repos/{REPO}/issues/{pr}/comments", {"body": body})
    log(f"==> 已在 PR #{pr} 发布评论")


def run_git(args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["git"] + args, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------- PR 转绿


def create_check_run(sha: str, title: str = "CLA auto-signed ✓", summary: str = "") -> None:
    """REST 创建同名 cla-check=success check run（用 job token，需要 checks: write）。"""
    payload = {
        "name": "cla-check",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success",
        "output": {
            "title": title,
            "summary": summary or "本 commit 已通过 CLA 自动签署。",
        },
    }
    api("POST", f"/repos/{REPO}/check-runs", payload)
    log(f"==> 已创建 cla-check=success check run @ {sha}")


def mark_pr_green_rest(pr_number: int) -> None:
    """降级方式：在 PR 最新 head.sha 上 REST 建同名绿色 check run（现状逻辑）。"""
    pr = api("GET", f"/repos/{REPO}/pulls/{pr_number}")
    head_sha = pr["head"]["sha"]
    create_check_run(head_sha, title="CLA auto-signed ✓",
                     summary=f"作者已自动签署 CLA（PR #{pr_number}），required check 已满足。")
    log(f"==> 已创建 cla-check=success check run（PR #{pr_number} head {head_sha}）")


def find_failed_cla_runs(head_sha: str, token: str):
    """列出该 head 上 event=pull_request_target 且属于本 cla.yml 的失败 workflow run（新→旧）。
    API 出错返回 None（调用方据此回退），无失败 run 返回空列表。"""
    resp = api("GET",
               f"/repos/{REPO}/actions/runs?head_sha={head_sha}&event=pull_request_target&per_page=100",
               token=token, fatal=False)
    if resp is None:
        return None
    return [r for r in (resp.get("workflow_runs") or [])
            if r.get("path") == WORKFLOW_PATH and r.get("conclusion") == "failure"]


def rerun_runs(runs, token: str) -> int:
    """用 PAT rerun 失败的 CLA run；返回成功触发数。"""
    ok = 0
    for r in runs:
        rid = r["id"]
        res = api("POST", f"/repos/{REPO}/actions/runs/{rid}/rerun", token=token, fatal=False)
        if res is None:
            log(f"!! rerun run {rid} 失败（run 不可 rerun 或 PAT 权限不足）")
        else:
            ok += 1
            log(f"==> 已触发 cla-check rerun: run {rid}")
    return ok


def ensure_pr_green(pr_number: int) -> str:
    """让该 PR 的 cla-check 转绿，返回所用方式："rerun" / "rest"。

    免维护模式：rerun 该 PR head 上失败的 CLA run（重跑自动通过 → merge CLEAN）；
    无 PAT、查询失败、无失败 run 或 rerun 触发失败时：回退 REST 建同名绿色 check run（现状）。
    """
    if PAT:
        try:
            pr = api("GET", f"/repos/{REPO}/pulls/{pr_number}")
        except SystemExit:
            pr = None
        if pr is None:
            log("!! 获取 PR 信息失败，回退 REST 建绿")
        else:
            failed = find_failed_cla_runs(pr["head"]["sha"], token=PAT)
            if failed is None:
                log("!! 查询该 PR 的 CLA run 失败（PAT 是否缺 Actions 读权限?），回退 REST 建绿")
            elif failed:
                n = rerun_runs(failed, token=PAT)
                if n > 0:
                    log(f"==> 已触发 {n} 个失败 CLA run 的 rerun（重跑将自动通过 → merge CLEAN）")
                    return "rerun"
                log("!! rerun 均未触发成功，回退 REST 建绿")
            else:
                log("==> 该 PR head 上无失败的 CLA run（可能已绿），REST 建绿兜底一次")
                mark_pr_green_rest(pr_number)
                return "rest"
        # 走到这里说明需回退 REST
    mark_pr_green_rest(pr_number)
    return "rest"


# ---------------------------------------------------------------- 推送 main


EXTRAHEADER_KEY = "http.https://github.com/.extraheader"


def _b64_auth(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")


def _restore_job_extraheader() -> None:
    """把 checkout@v4 注入的 GITHUB_TOKEN extraheader 写回（保证后续降级路径仍可认证）。"""
    run_git(["config", EXTRAHEADER_KEY, f"AUTHORIZATION: basic {_b64_auth(JOB_TOKEN)}"])


def push_main_with_pat() -> bool:
    """免维护模式：用 CLA_BOT_PAT 直推 origin/main（绕过分支保护）。

    注意：actions/checkout 已在 .git/config 注入 GITHUB_TOKEN 的 http.extraheader；若直接叠加 PAT
    extraheader 会发出两个 Authorization 头 → git 400 "Duplicate header"。因此先 unset 掉原值、写入
    PAT 值，推送后再恢复 job token 的 extraheader（无论成败）。
    """
    run_git(["config", "--unset-all", EXTRAHEADER_KEY])  # 无该键时 exit 5，可忽略
    run_git(["config", EXTRAHEADER_KEY, f"AUTHORIZATION: basic {_b64_auth(PAT)}"])
    r = run_git(["push", "origin", "HEAD:main"], env_extra={"GIT_TERMINAL_PROMPT": "0"})
    ok = r.returncode == 0
    _restore_job_extraheader()
    if not ok:
        log(f"!! CLA_BOT_PAT 直推 main 失败: {r.stderr.strip()}")
        return False
    log("==> 已用 CLA_BOT_PAT 直推 .cla/signatures.json 到 main")
    return True


def push_main_degraded(sha: str) -> bool:
    """降级模式（无 PAT 或 PAT 直推失败）：现状逻辑 —— 临时分支 + 给 commit 预置绿色 check run 再推 main。"""
    def push_origin():
        return run_git(["push", "origin", "HEAD:main"])

    r = push_origin()
    if r.returncode == 0:
        log("==> 已推送 .cla/signatures.json 到 main")
        return True
    log(f"!! 直推 main 被拒: {r.stderr.strip()}")

    # main 受保护（required check: cla-check），bot 无 bypass 权限时新 commit 无通过状态会被拒。
    # 方案：先把 commit 推到临时分支使其在远端存在 → 为该 commit 建 cla-check=success check run → 再推 main。
    tmp_branch = f"cla-autosign-{RUN_ID}"
    r = run_git(["push", "origin", f"HEAD:refs/heads/{tmp_branch}"])
    if r.returncode != 0:
        log(f"!! push 临时分支失败: {r.stderr.strip()}")
        return False
    create_check_run(sha, title="cla: auto-sign commit (github-actions[bot])",
                     summary="推送到受保护 main 前，为本 commit 预置通过的 cla-check。")
    log(f"==> 已为 push 目标 commit {sha} 创建 cla-check=success check run")
    r = push_origin()
    if r.returncode != 0:
        # 这里以前会再用 GIT_SSL_NO_VERIFY=true 重试一次：在携带推送凭据的操作上关闭
        # TLS 校验，等于给中间人让路，且掩盖真实的证书问题。改为如实失败并留下日志。
        log(f"!! push main 失败: {r.stderr.strip()}")
        run_git(["push", "origin", "--delete", tmp_branch])
        return False
    run_git(["push", "origin", "--delete", tmp_branch])
    log("==> 已清理临时分支；已推送 .cla/signatures.json 到 main")
    return True


# ---------------------------------------------------------------- 主流程


def main() -> int:
    if PAT:
        log("==> 免维护模式（CLA_BOT_PAT 已配置）")
    else:
        log("==> 降级模式（未配置 CLA_BOT_PAT，保持纯 GITHUB_TOKEN 行为）")

    if not os.path.exists(SIGN_FILE):
        log(f"!! 缺少 {SIGN_FILE}，无法自动签署")
        return 1

    with open(EVENT_PATH, encoding="utf-8") as fh:
        ev = json.load(fh)
    comment = ev.get("comment") or {}
    issue = ev.get("issue") or {}
    pr_number = issue.get("number")
    commenter = (comment.get("user") or {}).get("login", "")
    author = (issue.get("user") or {}).get("login", "")
    body = comment.get("body") or ""
    log(f"==> issue_comment on #{pr_number} by {commenter} (PR author: {author})")

    # ---- 安全前置校验：全部满足才继续 ----
    if not issue.get("pull_request"):
        log("==> 普通 issue 评论（非 PR 评论），跳过")
        return 0
    if not commenter or commenter != author:
        log(f"==> 评论者 {commenter!r} != PR 作者 {author!r}，跳过")
        return 0
    if norm(body) not in AGREE_PHRASES:
        log(f"==> 正文不匹配签署语（规范化后: {norm(body)!r}），跳过")
        return 0
    log(f"==> ✅ 通过校验：PR #{pr_number} 作者 {author} 回复同意 CLA")

    # ---- 名单操作 ----
    # 注：signatures.json 历史上可能带 UTF-8 BOM（Windows 写入），须用 utf-8-sig 读取
    with open(SIGN_FILE, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    sigs = data.setdefault("signatures", [])
    if any(s.get("login") == author for s in sigs):
        log(f"==> {author} 已在签名名单中，跳过写入")
        if not comment_exists(pr_number, ALREADY_MARKER):
            post_comment(pr_number, f"{author} {ALREADY_MARKER}，无需重复签署 ✅")
        # 仍尝试把该 PR 的 cla-check 翻绿（若之前是红的）
        mode = ensure_pr_green(pr_number)
        log(f"==> 已完成（方式: {mode}）")
        return 0

    sigs.append({
        "login": author,
        "name": author,
        "agreed_at": datetime.date.today().isoformat(),
    })
    with open(SIGN_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log(f"==> 已更新 {SIGN_FILE}")

    # ---- 提交 ----
    run_git(["config", "user.name", "github-actions[bot]"])
    run_git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run_git(["add", SIGN_FILE])
    r = run_git(["commit", "-m", f"cla: auto-sign {author}"])
    if r.returncode != 0:
        log(f"!! commit 失败: {r.stderr.strip()}")
        return 1
    log((r.stdout or r.stderr).strip())

    sha = (run_git(["rev-parse", "HEAD"]).stdout or "").strip()
    log(f"==> 待推送 commit: {sha}")

    # ---- 推送 main：PAT 优先，失败/缺失降级 ----
    pushed = False
    if PAT:
        pushed = push_main_with_pat()
    if not pushed:
        pushed = push_main_degraded(sha)
    if not pushed:
        log("!! 推送 main 最终失败，中止（未改名单落地前勿重复签署）")
        return 1

    # ---- 让该 PR 的 cla-check 转绿 ----
    mode = ensure_pr_green(pr_number)

    # ---- 幂等完成评论 ----
    extra = ""
    if mode == "rerun":
        extra = "\n\n已自动触发该 PR 的 cla-check 重跑（重跑将自动通过），请稍候 ~1 分钟即可合并。"
    if not comment_exists(pr_number, DONE_MARKER):
        post_comment(pr_number, DONE_BODY + extra + f"\n\n签署记录已写入 .cla/signatures.json（{author}，{datetime.date.today().isoformat()}）。")
    else:
        log("==> 完成评论已存在，跳过")

    log("==> auto-sign 完成 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
