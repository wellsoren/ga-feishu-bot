# 📋 更新日志

所有显著的变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [1.0.0] - 2026-07-11

### ✨ 新增

- 🚀 **一键部署** — `deploy/install.py` 自动完成检测环境、部署源码、安装依赖、打补丁、生成配置全流程
- 📦 **离线安装** — 支持 `--offline` 模式，无需网络即可安装
- 🔄 **在线安装** — 自动从 PyPI 下载纯 Python 依赖（更小更快）
- 🧩 **解密补丁** — `setup/patch_decryptor.py` 自动替换 pycryptodome 为 pyaes

### ⚡ 改进

- 🧹 **移除硬编码路径** — 所有路径自动检测，适配 `/data/data/` 和 `/data/user/0/` 等多种 GA 安装路径
- 🗂️ **极简依赖包** — 排除 `__pycache__` 和 `.pyc`，依赖包从 99MB 压缩到 2.8MB
- 🧪 **干运行模式** — `--dry-run` 仅检查环境不安装
- 💪 **强制重部署** — `--force` 覆盖已有部署

### 🐛 修复

- 修复 `start_fsbot.py` 在 exec() 模式下 `__file__` 不可用的问题
- 修复 `fsbot_ctl.py` 在非标准 GA 路径下的兼容性

### 📖 文档

- 完整的 README（中文）
- 贡献指南 CONTRIBUTING.md
- 安全策略 SECURITY.md
- 行为准则 CODE_OF_CONDUCT.md

## [0.1.0] - 2026-07-10

### ✨ 初版

- 飞书机器人核心：WebSocket 长连接、消息收发、AI 对话
- `lark_native.py`：飞书全接口 REST 客户端
- `fsbot_ctl.py`：启停控制模块
- 基于 GA (Chaquopy) 运行
