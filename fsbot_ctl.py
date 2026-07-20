"""
飞书机器人控制模块 — 统一启停入口
===============================
修复了「停止后无法重启」「重复启动」「无连接确认」三大问题。

用法 (通过 code_run):
    import sys; sys.path.insert(0, '/data/user/0/com.ljq.ga/files/ga/lark_bot')
    from fsbot_ctl import start, stop, status

    result = start()     # 启动 + 等连接确认
    info = status()      # 查看状态
    result = stop()      # 停止
"""

import asyncio
import os
import re
import sys
import threading
import time

LARK_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
GA_ROOT = os.path.dirname(LARK_BOT_DIR)
SP_DIR = os.path.join(LARK_BOT_DIR, "site-packages")
BOT_LOG = os.path.join(LARK_BOT_DIR, "bot.log")
# 不 exec start_fsbot.py（其模块清理逻辑在 Chaquopy 下会失败）
# 由本模块直接起线程

_MODULES_INITIALIZED = False
_BOT_THREAD = None      # bot 线程对象引用（优先于 ID 匹配）
_BOT_THREAD_ID = None   # 兼容旧代码的线程 ID 记录


def _ensure_paths():
    """确保导入路径可用, 清理坏掉的 SitePackagesFinder"""
    global _MODULES_INITIALIZED
    if _MODULES_INITIALIZED:
        return
    for p in [LARK_BOT_DIR, GA_ROOT, SP_DIR]:
        # 无论是否已在 sys.path，都确保在最前面
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
    # 剔除 project 目录，防止开源仓库代码干扰机器人运行
    _project_dir = os.path.join(GA_ROOT, "project", "ga-feishu-bot")
    while _project_dir in sys.path:
        sys.path.remove(_project_dir)
    # 清理 start_fsbot.py exec() 残留的 SitePackagesFinder
    # 其闭包丢失 importlib，会导致 lark_oapi 导入失败
    sys.meta_path[:] = [
        f for f in sys.meta_path
        if 'SitePackagesFinder' not in type(f).__name__
    ]
    _MODULES_INITIALIZED = True


def _find_bot_thread():
    """查找 lark-bot 线程（对象引用优先 → 枚举回退 → 名称回退）

    三层策略:
      1. _BOT_THREAD 引用检查（最快，start() 保存的对象引用）
      2. 遍历所有线程，匹配 target 函数名 _run_bot
      3. 遍历所有线程，匹配线程名 lark-bot
    """
    global _BOT_THREAD, _BOT_THREAD_ID

    # 方法1: 引用检查（start() 保存的线程对象引用）
    if _BOT_THREAD is not None and _BOT_THREAD.is_alive():
        _BOT_THREAD_ID = _BOT_THREAD.ident  # 同步 ID，方便旧代码读取
        return _BOT_THREAD

    # 方法2: 遍历线程，按 target 函数名匹配（兼容旧线程或模块重载）
    for t in threading.enumerate():
        if not t.is_alive():
            continue
        target = getattr(t, '_target', None)
        if target is not None:
            tname = getattr(target, '__name__', '') or ''
            if 'run_bot' in tname:
                _BOT_THREAD = t
                _BOT_THREAD_ID = t.ident
                return t

    # 方法3: 按线程名回退
    for t in threading.enumerate():
        if not t.is_alive():
            continue
        tname = t.name or ''
        if 'lark-bot' in tname:
            _BOT_THREAD = t
            _BOT_THREAD_ID = t.ident
            return t

    return None


def _reset_modules_for_restart():
    """重置模块状态 + 清除 sys.modules 缓存，使停止后能再次启动并加载新代码"""
    # 0. 先清除用户代码缓存，使重启后加载最新代码
    _clean_bot_modules()

    # 1. 重置 shutdown_flag
    try:
        from frontends import fsapp

        if fsapp.shutdown_flag.is_set():
            fsapp.shutdown_flag.clear()
    except ImportError:
        pass

    # 2. 如果 event loop 已停止，换一个新的
    try:
        import lark_oapi.ws.client as ws_mod

        loop = getattr(ws_mod, "loop", None)
        if loop is not None:
            # 已停止 或 已关闭 → 替换
            if not loop.is_running():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                ws_mod.loop = new_loop
    except ImportError:
        pass


def _clean_bot_modules():
    """清除 lark_bot 用户代码的 sys.modules 缓存（不清第三方库和自身）

    基于文件路径过滤：所有 __file__ 以 lark_bot 目录开头、且不在
    site-packages/ 下的模块，从 sys.modules 中删除。这样 restart()
    或 start() 重新导入时会从磁盘加载最新源码。
    不清除: fsbot_ctl, ga_bot_ctl, lark_oapi.*, httpx.* 等第三方库。
    """
    skip_names = {'fsbot_ctl', 'ga_bot_ctl'}
    lbd = os.path.dirname(os.path.abspath(__file__))

    for name, mod in list(sys.modules.items()):
        if mod is None or name in skip_names:
            continue
        try:
            fp = getattr(mod, '__file__', '') or ''
            if fp:
                real_fp = os.path.realpath(fp)
                if real_fp.startswith(os.path.realpath(lbd)):
                    if 'site-packages' not in real_fp:
                        sys.modules.pop(name, None)
        except Exception:
            pass


def _wait_for_connection(timeout: float) -> dict:
    """轮询 bot.log 等待 WebSocket 连接成功

    Returns:
        dict: {"connected": bool, "errors": [str, ...]}
    """
    errors = []
    # 已知连接错误模式 → 中文提示
    error_patterns = [
        (r"Failed to resolve", "DNS 解析失败（无法解析 open.feishu.cn，请检查网络/DNS）"),
        (r"NameResolutionError", "DNS 解析异常"),
        (r"Max retries exceeded", "连接飞书服务器重试耗尽（可能网络不通）"),
        (r"no close frame received", "WebSocket 连接被意外断开（可能网络不稳定）"),
        (r"connect failed", "WebSocket 端点连接失败"),
    ]
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _find_bot_thread():
            return {"connected": False, "errors": errors + ["机器人线程已退出"]}
        try:
            with open(BOT_LOG) as f:
                content = f.read()
                if re.search(r"connect.*wss?://", content):
                    return {"connected": True, "errors": errors}
                # 轮询过程中检测已知错误（每个错误只记录一次）
                for pattern, hint in error_patterns:
                    if re.search(pattern, content):
                        err_key = f"[{hint}]"
                        if err_key not in errors:
                            errors.append(err_key)
        except (FileNotFoundError, OSError):
            pass
        time.sleep(0.5)
    return {"connected": False, "errors": errors}


# ── 公开 API ──────────────────────────────────────────


def status():
    """返回机器人当前状态字典

    Returns:
        dict: {
            "running": bool,      # bot 线程是否存活
            "bot_thread": str|None,  # 线程名或 None
            "shutdown_flag": bool|None,
            "event_loop": {"running": bool, "closed": bool}|None,
        }
    """
    _ensure_paths()

    bot = _find_bot_thread()
    result = {
        "running": bot is not None,
        "bot_thread": bot.name if bot else None,
    }

    try:
        from frontends import fsapp
        result["shutdown_flag"] = fsapp.shutdown_flag.is_set()
    except ImportError:
        result["shutdown_flag"] = None

    try:
        import lark_oapi.ws.client as ws_mod
        loop = getattr(ws_mod, "loop", None)
        if loop:
            result["event_loop"] = {
                "running": loop.is_running(),
                "closed": loop.is_closed(),
            }
        else:
            result["event_loop"] = None
    except ImportError:
        result["event_loop"] = None

    return result


def start(timeout: int = 15):
    """启动飞书机器人

    自动处理: 防重复启动 + 重置停止标志 + 重建 event loop + 等待连接确认

    Args:
        timeout: 等待 WebSocket 连接的超时秒数（默认 15）

    Returns:
        dict: {
            "success": bool,
            "message": str,
        }
    """
    _ensure_paths()

    # ── 防重复启动 ──
    if _find_bot_thread():
        return {"success": True, "message": "机器人已在运行中，无需重复启动"}

    # ── 清理旧状态（支持重启） ──
    _reset_modules_for_restart()

    # ── 启动机器人（直接起线程，不 exec start_fsbot.py） ──
    # start_fsbot.py 的模块清理逻辑在 Chaquopy 下有问题
    # 这里直接在新线程中运行 fsapp.main()
    def _run_bot():
        """在独立线程中运行 fsapp.main()，日志通过 logging FileHandler 写入 bot.log"""
        import logging
        import asyncio
        from lark_oapi.core import log as lark_log

        os.makedirs(os.path.dirname(BOT_LOG), exist_ok=True)

        # 配置 lark_oapi 日志写 bot.log（线程安全，不污染 sys.stdout）
        # 移除默认 StreamHandler(sys.stdout)，改用 FileHandler
        for h in list(lark_log.logger.handlers):
            if isinstance(h, logging.StreamHandler) and h.stream is sys.stdout:
                lark_log.logger.removeHandler(h)
        fh = logging.FileHandler(BOT_LOG, mode="a")
        fh.setFormatter(logging.Formatter(
            "[Lark] [%(asctime)s] [%(levelname)s] %(message)s"
        ))
        lark_log.logger.addHandler(fh)
        lark_log.logger.setLevel(logging.INFO)

        try:
            import lark_oapi.ws.client as ws_mod
            thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(thread_loop)
            ws_mod.loop = thread_loop

            from frontends import fsapp
            print(f"[lark-bot] fsapp imported, starting main()...", flush=True)
            fsapp.main()
        except Exception as e:
            import traceback
            print(f"[lark-bot] FATAL: {e}", flush=True)
            traceback.print_exc()
        finally:
            lark_log.logger.removeHandler(fh)
            fh.close()

    bot_thread = threading.Thread(target=_run_bot, daemon=False, name="lark-bot")
    bot_thread.start()
    global _BOT_THREAD, _BOT_THREAD_ID
    _BOT_THREAD = bot_thread        # 保存对象引用，供 _find_bot_thread() 快速定位
    _BOT_THREAD_ID = bot_thread.ident  # 兼容旧代码

    # ── 等待连接确认 ──
    conn_info = _wait_for_connection(timeout)
    if conn_info["connected"]:
        return {"success": True, "message": "飞书机器人已启动并连接成功"}
    elif _find_bot_thread():
        msg = "机器人线程已启动，等待 WebSocket 连接"
        if conn_info["errors"]:
            msg += "，检测到以下问题："
            msg += "；".join(conn_info["errors"])
        return {
            "success": True,
            "message": msg,
        }
    else:
        reason = ""
        try:
            with open(BOT_LOG) as f:
                lines = f.readlines()
                reason = (lines[-3].strip() if len(lines) >= 3 else lines[-1].strip()) if lines else ""
        except (FileNotFoundError, OSError):
            pass
        msg = f"机器人启动失败"
        if reason:
            msg += f": {reason}"
        return {"success": False, "message": msg}


def stop(timeout: int = 10):
    """停止飞书机器人

    自动处理: 设置停止标志 + 断开 WebSocket + 等待线程退出

    Args:
        timeout: 等待线程退出的超时秒数（默认 10）

    Returns:
        dict: {
            "success": bool,
            "message": str,
        }
    """
    _ensure_paths()
    global _BOT_THREAD, _BOT_THREAD_ID

    bot = _find_bot_thread()
    if not bot:
        return {"success": True, "message": "机器人未在运行"}

    # ── 第 1 步: 设置停止标志 ──
    try:
        from frontends import fsapp
        fsapp.shutdown_flag.set()
    except ImportError as e:
        return {"success": False, "message": f"停止失败（导入 fsapp 出错）: {e}"}

    # ── 第 2 步: 中断 WebSocket 事件循环 ──
    try:
        import lark_oapi.ws.client as ws_mod
        loop = getattr(ws_mod, "loop", None)
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
    except ImportError:
        pass

    # ── 第 3 步: 等待线程退出 ──
    bot.join(timeout)
    if bot.is_alive():
        # 尝试关闭事件循环强制退出
        try:
            import lark_oapi.ws.client as ws_mod
            loop = getattr(ws_mod, "loop", None)
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(loop.close)
        except ImportError:
            pass
        bot.join(5)
        if bot.is_alive():
            return {
                "success": True,
                "message": f"机器人已设置停止标志，但线程仍在清理（{timeout+5}s 后仍未退出）",
                "warning": True,
            }
        _BOT_THREAD = None
        _BOT_THREAD_ID = None
        return {"success": True, "message": "飞书机器人已正常停止（额外等待后退出）"}

    _BOT_THREAD = None
    _BOT_THREAD_ID = None
    return {"success": True, "message": "飞书机器人已正常停止"}


def restart(stop_timeout: int = 3, start_timeout: int = 15):
    """重启飞书机器人 = 停止 → 等线程退出 → 清模块缓存 → 启动

    Args:
        stop_timeout:  等待线程退出超时（秒），默认 3s
        start_timeout: 等待 WebSocket 连接超时（秒），默认 15s

    Returns:
        dict: start() 的返回结果

    内部流程:
        ① stop(timeout=stop_timeout)
        ② 循环等待旧线程退出（最多 10s，每 0.5s 检测一次）
        ③ _clean_bot_modules() 清缓存
        ④ start(timeout=start_timeout) 自动调 _reset_modules_for_restart
    """
    # 1. 停止
    stop_result = stop(timeout=stop_timeout)

    # 2. 等旧线程完全退出（最多等 10s）
    for _ in range(20):
        if not _find_bot_thread():
            break
        time.sleep(0.5)

    # 3. 清模块缓存
    _clean_bot_modules()

    # 4. 启动（内部自动调 _reset_modules_for_restart → 重建 loop + 清标志 + 起新线程）
    return start(timeout=start_timeout)
