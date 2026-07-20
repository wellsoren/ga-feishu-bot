# 📋 更新日志

所有显著的变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [2.0.6] - 2026-07-20

### ✨ 新增

- `frontends/chatapp_common.py` — 新增 `/llm` 命令，支持列出可用大模型列表，以及通过 `/llm <数字>` 切换当前模型
- `lark_bot/` 部署隔离 — 新增 `_project_dir` 排除机制，确保运行时只加载部署目录代码，避免与上游项目目录混淆

### 🔧 更新

- `fsbot_ctl.py` — 新增 `_project_dir` 排除逻辑，支持多目录隔离部署
- `README.md` — 更新 v2.0.6 版本号，新增 `/llm` 功能说明

## [2.0.5] - 2026-07-20

### ✨ 新增

- `fsbot_ctl.py` — 新增 `restart()` 一键重启函数（停止→清 `sys.modules` 缓存→启动，真加载新代码）
- `fsbot_ctl.py` — 新增 `_clean_bot_modules()` 模块缓存清理（路径过滤，零误伤第三方库）
- `fsbot_ctl.py` — `start()` 集成 `_reset_modules_for_restart()`，确保启动时加载最新代码

### 🔧 更新

- `ga_bot_ctl.py` — 导出 `restart()`，支持 "重启飞书机器人" 命令
- `lark_bot_sop.md` — 新增重启章节 + 边界场景提示
- `README.md` — 控制段新增 `restart()` 使用示例
- `modes_index.md` — 触发词新增「重启飞书机器人」

## [2.0.4] - 2026-07-19

### ✨ 群聊上下文感知

- `frontends/feishu_context.py` — 新增群聊上下文模块
- @机器人自动识别当前群名和群 ID
- `get_chat_name` 自动缓存群名（TTL=86400s），查不到时友好降级为 chat_id
- "总结本群" 等命令精准作用于正在对话的群，不混淆
- `frontends/fsapp.py` — run_agent() 注入群上下文（set_context_env + build_context_prompt），handle_message() 四路 chat_id 提取链

### ✨ 新增模块

- `feishu_api/bitable.py` — 多维表格操作模块
- `feishu_api/wiki.py` — 知识库操作模块

### 🔧 更新

- `feishu_api/__init__.py` — 注册新命令、增强分发逻辑
- `feishu_api/docx.py` — 云文档功能增强
- `feishu_api/formatters.py` — 格式化输出优化
- `feishu_api/im.py` — IM 消息模块更新
- `feishu_api/permissions.py` — 权限模块更新

## [2.0.3] - 2026-07-19

## [2.0.2] - 2026-07-15

### ✨ 卡片消息全面改造

- 方案 A — **摘要人性化**：`_TOOL_SUMMARY_MAP` 语义映射，`_make_task_hook` 自动加载
- 方案 B — **截断温和化**：`_DETAIL_LIMIT` 8000→10000，移除恐慌措辞
- 方案 C — **进度感知**：`_step_panel` 当前步自动展开 + 状态 emoji，状态栏实时轮转
- 方案 D — **最终输出结构化**：`_build()` 加 "📋 结果" 标题区域
- 方案 E — **代码 diff 可视化**：`_render_diff()` + `_build_step_detail` diff 注入
- 方案 G — **耗时显示**：`_TaskCard` 自计时 + `done()` 自动显示耗时
- 方案 I — **"Turn"→"步骤"中文化**：`_step_panel` + `_TaskCard.step` 同步

### 🔧 代码质量

- 移除未使用 `FILE_HINT` import
- 32 处 `print()` → `logging` 分级迁移
- 6 处魔法数字集中为配置常量

## [2.0.1] - 2026-07-14

### 🔧 维护

- 版本号更新至 v2.0.1（`deploy/install.py`、`README.md`）
- CHANGELOG 补录 v2.0.1 条目

## [2.0.0] - 2026-07-13

### ✨ 新增

- 🚀 **飞书业务域命令模块 `feishu_api/`** — 全新 9 文件包，支持 `/日历` `/日程` `/群聊` `/消息` `/文档` `/文件` `/权限` `/帮助` 等中文命令
  - `__init__.py`：命令注册(register_all_commands) + 分发(dispatch_command) + HELP_TEXT
  - `_base.py`：`_call()` — lark_native REST 封装
  - `command_router.py`：命令解析与路由（中英别名、路径参数去尖括号）
  - `formatters.py`：卡片化输出格式渲染（分片）
  - `calendar.py`：日历/日程
  - `docx.py`：云文档（read / create）
  - `drive.py`：云盘
  - `im.py`：IM 消息
  - `permissions.py`：权限总览
- 💳 **卡片化回复** — `frontends/fsapp.py` 新增 `send_card()`，业务域命令以 interactive 卡片(markdown) 发送，长文本自动分片（`card_split_limit=12000`）
- 📄 **`/文档 create` 命令** — 支持创建飞书云文档并返回文档链接

### 🐛 修复

- 🔑 **token 参数修复** — `/文档 read` 等命令的 `--token` 参数尖括号包裹导致 `invalid param`，`command_router` 已自动去尖括号

### 📖 文档

- 📚 README 补充 `feishu_api/` 目录结构说明

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

- 完整的 README（中英文双语）
- 贡献指南 CONTRIBUTING.md
- 安全策略 SECURITY.md
- 行为准则 CODE_OF_CONDUCT.md

## [0.1.0] - 2026-07-10

### ✨ 初版

- 飞书机器人核心：WebSocket 长连接、消息收发、AI 对话
- `lark_native.py`：飞书全接口 REST 客户端
- `fsbot_ctl.py`：启停控制模块
- 基于 GA (Chaquopy) 运行
