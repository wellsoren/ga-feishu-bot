# 🤖 GA Feishu Bot

<div align="center">

**在安卓手机上运行飞书机器人 —— 基于 GA (Chaquopy) 运行时环境**

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Android-lightgrey)]()
[![Feishu](https://img.shields.io/badge/Feishu-Open_Platform-red)]()

</div>

---

## 📖 项目简介

`ga-feishu-bot` 让您在 **安卓手机** 上直接运行飞书机器人，无需服务器、无需电脑、无需 root。

它基于 **GA App**（一个内置 Chaquopy Python 运行时的安卓应用），通过 WebSocket 长连接与飞书开放平台双向通讯，实现：

- ✅ **消息收发** — 实时响应飞书消息（文本、图片、文件等）
- ✅ **智能对话** — 接入 LLM（大语言模型），成为 AI 助手
- ✅ **飞书 API** — 完整 REST API 封装（IM、文档、多维表格、知识库等）
- ✅ **多维表格 / 知识库** — bitable/wiki 业务域命令模块
- ✅ **群聊上下文感知** — @机器人自动识别群名、群ID，"总结本群"等命令精准作用于当前群
- ✅ **用户 OAuth** — 支持用户身份授权，访问私有资源

### 谁适合用这个项目？

| 场景 | 适合 |
|------|------|
| 想在手机上跑飞书机器人 | ✅ |
| 不想买服务器/VPS | ✅ |
| 已有 GA App 用户 | ✅ 直接部署 |
| 没有 GA App | ✅ 先装 GA，再一键部署 |
| 需要高并发/企业级部署 | ❌ 建议用服务器 |

---

## 🏗️ 项目结构

```
ga-feishu-bot/
├── frontends/               # 机器人核心
│   ├── __init__.py
│   ├── fsapp.py             # 主程序 — WebSocket 长连接 + 消息处理
│   ├── feishu_context.py    # 群聊上下文 — 自动识别群名/群ID + get_chat_name 缓存
│   └── chatapp_common.py    # 通用工具 — AgentChatMixin
├── setup/                   # 部署工具
│   ├── fetch_deps.py        # 从 PyPI 下载纯 Python 依赖
│   └── patch_decryptor.py   # 解密库补丁（pyaes 替换 pycryptodome）
├── channels/                # 渠道配置模板
│   └── lark.json.template   # 飞书凭证模板
├── deploy/                  # 一键部署包
│   ├── install.py           # ★ 一键安装脚本
│   ├── setup/               # 部署用工具脚本
│   └── site_packages.tar.gz # 离线依赖 (2.8MB)
├── start_fsbot.py           # 启动器
├── fsbot_ctl.py             # 控制模块（start / stop / status）
├── lark_native.py           # 飞书全接口 REST 客户端
├── feishu_api/              # ★飞书业务域命令模块（/日历 /文档 /多维表格 /知识库 …）
│   ├── __init__.py          #   命令注册(register_all_commands) + 分发(dispatch_command) + HELP_TEXT
│   ├── _base.py             #   _call() — lark_native REST 封装
│   ├── command_router.py    #   命令解析与路由（中英别名、路径参数去尖括号）
│   ├── formatters.py        #   卡片化输出格式渲染（分片）
│   ├── bitable.py           #   多维表格（list / read / create / update）
│   ├── calendar.py          #   日历/日程
│   ├── docx.py              #   云文档（read / create）
│   ├── drive.py             #   云盘
│   ├── im.py                #   IM 消息
│   ├── permissions.py       #   权限总览
│   └── wiki.py              #   知识库（list / read / search）
├── .lark_workspace          # 工作目录标记
├── README.md                # ← 就是本文件
├── LICENSE                  # MIT 许可证
├── .gitignore
├── CONTRIBUTING.md          # 贡献指南
├── CHANGELOG.md             # 更新日志
├── CODE_OF_CONDUCT.md       # 行为准则
├── SECURITY.md              # 安全策略
└── docs/                    # 详细文档
    └── user_guide.md        # 用户指南（启停/状态/故障排查）
```

---

## 🚀 快速开始

### 前置条件

- 一台 **安卓手机**
- 已安装 **GA App**（[下载地址](https://github.com/你的用户名/ga-feishu-bot/releases)）
- 一个 **飞书开放平台** 企业自建应用（[创建应用](https://open.feishu.cn)）

### 方式一：一键部署（推荐）

```bash
# 1. 解压部署包
tar -xzf ga_feishu_bot_v2.0.5.tar.gz
cd ga_feishu_deploy

# 2. 运行安装（会自动检测 GA 环境，在线下载依赖）
python deploy/install.py

# 3. 如需离线安装，用 --offline 使用内置离线包
python deploy/install.py --offline
```

### 方式二：手动部署

```bash
# 1. 将项目复制到 GA 数据目录
cp -r ga-feishu-bot /data/data/com.ljq.ga/files/ga/lark_bot/

# 2. 安装依赖
python setup/fetch_deps.py --dest site-packages

# 3. 打解密补丁
python setup/patch_decryptor.py --sp-dir site-packages
```

### 配置凭证

编辑 `mykey.json`（`deploy/install.py` 会自动生成模板）：

```json
{
  "fs_app_id": "cli_xxxxxxxxxxxxxxxxxx",
  "fs_app_secret": "your_app_secret_here",
  "fs_allowed_users": []
}
```

- `fs_app_id` / `fs_app_secret` — 从飞书开放平台获取
- `fs_allowed_users` — 允许使用机器人的用户列表（`[]` 表示所有人）

### 👑 Owner 准入控制（群聊）

为防止机器人在群里被任意用户打扰，群聊默认启用 **Owner 准入控制**：

- **首次 @机器人**：第一个在群里 @机器人的用户自动绑定为机器人所有者（owner），open_id 持久化写入 `fs_owner.json`（与 `mykey.json` 同目录）
- **此后**：群内仅当 owner @机器人 时才响应，其他人 @ 或不 @ 均被静默忽略
- **私聊**：不受此限制，仍按 `fs_allowed_users` 白名单控制

> 重置 owner：删除工作目录下的 `fs_owner.json`，下次在群里 @机器人将重新绑定

### 启动机器人

> 🎉 部署完成后，**GA 冷启动时飞书机器人将自动上线**，无需手动操作。

手动控制（调试/维护用）：

```python
# 方式1：使用控制模块
from fsbot_ctl import start, stop, status
start()
status()

# 方式2：直接运行启动器
python start_fsbot.py
```

> 📖 **详细操作指南**（启停/状态查询/故障排查）见 [`docs/user_guide.md`](docs/user_guide.md)

---

## 🎯 主要功能

### 💬 消息处理
- 自动回复文本、图片、文件等消息
- 支持富文本卡片回复
- 消息长度自动分片

### 🤖 AI 对话
- 集成 LLM 进行智能对话
- 支持多轮上下文记忆
- 支持 `/clear` 清除对话历史

### 🔌 飞书 API
- `lark_native.py` 提供完整 REST API 封装
- 支持 bot 身份（tenant_access_token）
- 支持用户身份（user_access_token，OAuth 授权码流）
- 一行代码调用任何开放平台端点

### 📊 多维表格（bitable）
- `feishu_api/bitable.py` — 多维表格操作模块
- 支持 `/多维表格 list` — 列出多维表格
- 支持 `/多维表格 read` — 读取表格记录，支持筛选条件
- 支持 `/多维表格 create` — 新增记录
- 支持 `/多维表格 update` — 更新已有记录

### 📚 知识库（wiki）
- `feishu_api/wiki.py` — 知识库操作模块
- 支持 `/知识库 list` — 列出知识空间
- 支持 `/知识库 read` — 获取知识库节点详情
- 支持 `/知识库 search` — 搜索知识库内容

### 👥 群聊上下文感知
- `frontends/feishu_context.py` — 群聊上下文模块
- @机器人自动识别当前群名和群 ID，无需手动指定
- "总结本群" 等命令准确作用于正在对话的群，不混淆
- `get_chat_name` 自动缓存群名，减少重复请求
- 支持多群隔离：多个群聊的上下文互不干扰

---

## 📖 进阶用法

### 飞书全接口客户端

```python
from lark_native import api, save_creds

# 配置凭证
save_creds("cli_xxx", "your_secret")

# 获取用户列表（bot 身份）
resp = api("GET", "/open-apis/im/v1/users")

# 发送消息
api("POST", "/open-apis/im/v1/messages", {
    "receive_id": "ou_xxxx",
    "msg_type": "text",
    "content": '{"text":"Hello from phone!"}'
})
```

### 控制机器人启停

```python
from fsbot_ctl import start, stop, status, restart

# 启动（带连接确认，最长等30秒）
result = start(timeout=30)
print(result)  # {"success": True, "message": "..."}

# 一键重启（停止→清模块缓存→启动，加载新代码）
result = restart()
print(result)  # {"success": True, "message": "飞书机器人已启动并连接成功"}

# 查看状态
info = status()
print(info)  # {"running": True, "ready": True, ...}

# 停止
result = stop()
```

---

## 🔧 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: Crypto` | 未替换解密库 | 运行 `setup/patch_decryptor.py` |
| `ModuleNotFoundError: lark_oapi` | 依赖未安装 | 运行 `setup/fetch_deps.py` |
| WebSocket 连不上 | 网络/DNS 问题 | 检查能否访问 `open.feishu.cn` |
| 消息收不到 | 未订阅事件/权限 | 在飞书开放平台配置事件回调 |
| 机器人启动超时 | 网络慢 | 增加 `start(timeout=60)` |

---

## 🤝 贡献

欢迎贡献！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

- 🐛 发现 bug？提交 [Issue](https://github.com/你的用户名/ga-feishu-bot/issues)
- 💡 有想法？提交 Pull Request
- 📚 改进文档？欢迎 PR

---

## 📄 许可证

[MIT License](LICENSE) © 2026 ga-feishu-bot

---

## 🙏 致谢

- [GA App](https://github.com/) — 提供 Chaquopy Python 运行时
- [lark-oapi](https://pypi.org/project/lark-oapi/) — 飞书开放平台 SDK
- [GenericAgent](https://github.com/lsdefine/GenericAgent) — 提供 fsapp.py WebSocket + 消息处理框架
- 所有贡献者和用户
