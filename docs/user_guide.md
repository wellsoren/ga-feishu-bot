# 飞书机器人使用指南

> 统一使用 `fsbot_ctl` 控制模块（v2 优化版: 防僵尸/独立事件循环/可靠停止）

## 前置约束

- Bot 工作目录: 部署后自动设定（`install.py` 复制源码到 GA 数据目录）
- 日志文件: `bot.log`（与 `fsbot_ctl.py` 同级）
- 机器人通过 **GA App 内嵌的 Chaquopy Python 运行时** 执行

## 启动机器人

```python
from fsbot_ctl import start

result = start()                     # 默认等 15s 连接确认
# {"success": True, "message": "飞书机器人已启动并连接成功"}
```

内部自动处理：
- ✅ **防重复启动**（线程 ID 注册机制，忽略僵尸线程）
- ✅ **重置模块状态**（清 `shutdown_flag` + 替换旧事件循环引用）
- ✅ **独立事件循环**（新线程内 `asyncio.new_event_loop()` + `set_event_loop()`）
- ✅ **`ws_mod.loop` 绑定**（确保 `stop()` 能正确中断线程内的事件循环）
- ✅ **轮询 `bot.log` 确认 WebSocket 已连接**（v2.1: 正则匹配 `connect.*wss?://`）
- ✅ **超时降级**（超时但线程仍在 → 返回具体错误提示，如 DNS 解析失败/连接重试耗尽等）

## 停止机器人

```python
from fsbot_ctl import stop

result = stop()                      # 默认等 10s 线程退出
# {"success": True, "message": "飞书机器人已正常停止"}
```

内部自动处理（两阶段）：
- ✅ 设 `shutdown_flag` → 通知 `fsapp.main()` 退出
- ✅ `loop.call_soon_threadsafe(loop.stop)` → 中断 WebSocket 事件循环
- ✅ `bot.join(timeout)` → 等待线程退出
- ✅ **第 2 阶段**：若线程未退出，关闭事件循环 + 再等 5s
- ✅ 清理 `_BOT_THREAD_ID` 注册 → 支持后续重启
- ✅ 已停止状态再调用返回"未在运行"

## 状态查询

```python
from fsbot_ctl import status

info = status()
# {"running": True/False, "bot_thread": "...", "shutdown_flag": True/False,
#  "event_loop": {"running": True/False, "closed": False}}
```

## 关键原理

| 组件 | 作用 |
|:----|:------|
| `fsapp.shutdown_flag` | `threading.Event()`，`main()` 循环检查此处 |
| `lark_oapi.ws.client.loop` | 模块级事件循环引用（被新线程覆盖为新 loop） |
| `_BOT_THREAD_ID` | 本模块启动的线程 ID，用于防僵尸 + 精确查找 |
| `fsbot_ctl.start()` | 清理旧模块 + 起独立循环线程 + 等连接 |
| `fsbot_ctl.stop()` | 设标志 + 中断循环 + 两阶段等待 + 清理注册 |
| `_reset_modules_for_restart()` | 清 `shutdown_flag` + 替换 `ws_mod.loop` 为新事件循环 |

**为什么需要独立事件循环？**  
`cli.start()` 阻塞在 `loop.run_until_complete(_select())` 里。调用 `loop.stop()` 后，该 loop 无法再执行 `run_until_complete()`。重启前必须创建新线程并分配 `asyncio.new_event_loop()`。

**线程注册机制解决了什么？**  
旧版通过线程名称 `"lark-bot"` 查找，但停止失败的僵尸线程仍会匹配，导致误判"已运行"。新版通过 `_BOT_THREAD_ID`（线程 ident）精确匹配本模块启动的线程，僵尸线程被自动忽略。

## 快速校验

```python
from fsbot_ctl import status, start, stop

# 检查状态
s = status()

# 启动（可调 timeout 参数增加等待时间）
r = start(timeout=30)
print(r["message"])

# 停止
r = stop(timeout=15)
print(r["message"])
```

## 故障排查

### 启动后机器人无响应（"假成功"）

**现象**：`start()` 返回"线程已启动"但私聊机器人无回复。稍后再次 `status()` 检查才正常。

**原因**（可能两者叠加）：

| # | 问题 | 表现 | 日志关键词 |
|---|------|------|-----------|
| 1 | **DNS 解析故障** | `lark_oapi` 自动重连期间无法解析 `open.feishu.cn`，WebSocket 连不上 | `Failed to resolve 'open.feishu.cn'` / `Errno 7 No address associated with hostname` |
| 2 | **`_wait_for_connection()` 正则不匹配**（v2.1已修复） | 正则 `r"connected wss?://"` 不匹配实际日志 `"connected to wss://"`，导致连接确认永远超时 | — |

**解决**：
- 检查网络/DNS 是否正常（`open.feishu.cn` 能否 ping 通）
- 若 DNS 间歇性故障，`lark_oapi` 有自动重连机制，耐心等 1-2 分钟
- v2.1 后 `start()` 返回消息会明确提示 `"检测到以下问题：DNS 解析失败"`，便于快速定位

### 启动超时

调整 `start(timeout=60)` 增加等待时间。超时后线程仍在后台重连，可调 `status()` 确认。

### 停止后无法重启

原因是事件循环已被关闭无法重用。v2 已修复：`start()` 自动调用 `_reset_modules_for_restart()` 创建新线程 + 新事件循环。若仍异常，检查 `bot.log` 尾部是否有 `FATAL` 错误。
