# 贡献指南（CONTRIBUTING）

感谢你对 Tentacle HRV 感兴趣！在提交代码前，请花两分钟阅读本文。

## 一句话规则

> 本项目为 **GPL-3.0** 个人开源项目。任何贡献在合并前需同意 [CLA.md](./CLA.md)（提交 Pull Request 即视为同意）；请勿提交密钥、证书、模型、日志等敏感或运行时文件。

## 我该如何贡献

### 1. 报告问题（Issue）

- 先搜索是否已有相同 Issue；
- 描述清楚：发生了什么 / 预期是什么 / 如何复现；
- 如涉及报错，附**脱敏后**的日志片段（不要贴 `.env`、API Key、令牌、内网穿透地址等）。

### 2. 提交代码（Pull Request）

1. Fork 本仓库，基于 `main` 新建分支；
2. 提交前自查：
   - ✅ 改动有明确目的，与项目方向一致；
   - ✅ 已阅读并同意 [CLA.md](./CLA.md)；
   - ✅ 未包含敏感/运行时文件（`.env`、`certs/`、`models/`、`*.log`、`latest.jpg`、`ffmpeg.exe`、`build/` 等）；
   - ✅ 后端改动至少通过 `python -m py_compile server.py`；
   - ✅ 固件改动至少能编译：`arduino-cli compile --fqbn esp32:esp32:esp32 esp32_firmware`（需 esp32 core 3.x）；
3. 使用仓库内 [PR 模板](./.github/PULL_REQUEST_TEMPLATE.md)，勾选 CLA 声明；
4. 描述改动内容与测试情况，等待维护者 review。

### 3. 关于"体感设备"与内容的措辞

对外文档、Issue、PR 与社区帖子中，请使用中性、克制的技术措辞（例如以“体感设备 A / 体感设备 B”指代具体设备类型），避免直白的成人向表述——这有助于项目在社区中健康交流与协作。

## 代码风格速览

- 后端：Python 3.10+，中文注释，保持与现有 `server.py` 一致的风格；
- 前端：单文件 `templates/index.html`，改动尽量内聚；
- 固件：`.ino` 位于 `esp32_firmware/`，基于 arduino-esp32 core 3.x API；
- 文档：中文，Markdown。

## 维护者联系

通过 Issue 或 Koishi 论坛项目帖交流即可。
