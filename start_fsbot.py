"""
飞书机器人启动器 — GA↔飞书双向通讯 (daemon=False 常驻模式)
部署于: lark_bot/ 专用工作区（一键部署版，无硬编码路径）
"""
import importlib
import importlib.abc
import os
import sys
import threading


# ── 自动检测路径（无硬编码） ──
def _detect_lark_bot_dir():
    """检测 lark_bot 工作目录"""
    # 优先用 __file__
    try:
        if '__file__' in dir() and __file__ and os.path.isfile(__file__):
            return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        pass
    # 从当前目录向上查找 .lark_workspace 标记
    cwd = os.path.abspath(os.getcwd())
    d = cwd
    while True:
        if os.path.isfile(os.path.join(d, '.lark_workspace')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 回退到当前目录
    return cwd


def _detect_ga_root(lark_bot_dir):
    """从 lark_bot 目录向上找 GA 根目录"""
    d = os.path.dirname(lark_bot_dir)  # 先看父目录
    if os.path.isfile(os.path.join(d, 'ga_android.py')):
        return d
    # 再看常见位置
    candidates = [
        "/data/data/com.ljq.ga/files/ga",
        "/data/user/0/com.ljq.ga/files/ga",
        os.path.expanduser("~/ga"),
    ]
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "ga_android.py")):
            return cand
    # 都不行就用 lark_bot 的父目录
    return d


LARK_BOT_DIR = _detect_lark_bot_dir()
GA_ROOT = _detect_ga_root(LARK_BOT_DIR)
SP_DIR = os.path.join(LARK_BOT_DIR, "site-packages")
BOT_LOG = os.path.join(LARK_BOT_DIR, "bot.log")

# ── ① Chaquopy 兼容的 site-packages 导入钩子 ──
class SitePackagesFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if '.' in fullname:
            return None
        mod_dir = os.path.join(SP_DIR, fullname)
        init_py = os.path.join(mod_dir, '__init__.py')
        if os.path.isfile(init_py):
            return importlib.util.spec_from_file_location(
                fullname, init_py, submodule_search_locations=[mod_dir])
        mod_file = mod_dir + '.py'
        if os.path.isfile(mod_file):
            return importlib.util.spec_from_file_location(fullname, mod_file)
        return None


sys.meta_path.insert(1, SitePackagesFinder())
sys.path.insert(0, LARK_BOT_DIR)  # frontends/ 在 lark_bot/ 下
sys.path.insert(0, GA_ROOT)      # 确保 mykey.py / agentmain 可导入


def start_bot():
    """在独立线程中启动飞书机器人 (daemon=False 常驻)"""
    # ── 工作目录注册 ──
    _ws_reg = os.path.join(LARK_BOT_DIR, '.lark_workspace')
    if os.path.isfile(_ws_reg):
        print(f"[lark-bot] 工作目录: {LARK_BOT_DIR} (已注册)")

    os.environ.setdefault("GA_USER_DATA_DIR", LARK_BOT_DIR)

    # ── ② 重定向 stdout/stderr 到日志文件 ──
    log_dir = os.path.dirname(BOT_LOG)
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = open(BOT_LOG, 'a', buffering=1)
    sys.stderr = sys.stdout
    os.chdir(LARK_BOT_DIR)

    import traceback
    try:
        # ── ③ 清除旧模块缓存（防止因旧目录已删导致导入失败）──
        for _key in list(sys.modules.keys()):
            if 'lark_oapi' in _key:
                del sys.modules[_key]
        for _key in list(sys.modules.keys()):
            if _key == 'frontends' or _key.startswith('frontends.'):
                del sys.modules[_key]

        # ── ④ 加载 fsapp ──
        from frontends import fsapp

        print("[lark-bot] fsapp imported, starting main()...")
        # ── ⑤ 启动 main（飞书 WS 长连接）──
        fsapp.main()
    except Exception as e:
        print(f"[lark-bot] FATAL: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    print(f"=" * 50)
    print(f"  飞书机器人启动器")
    print(f"  工作目录: {LARK_BOT_DIR}")
    print(f"  GA 根目录: {GA_ROOT}")
    print(f"  日志文件: {BOT_LOG}")
    print(f"=" * 50)
    t = threading.Thread(target=start_bot, daemon=False, name="lark-bot")
    t.start()
    print(f"[启动器] lark-bot 线程已启动 (name={t.name}, daemon={t.daemon})")
    print(f"[启动器] 日志文件: {BOT_LOG}")
