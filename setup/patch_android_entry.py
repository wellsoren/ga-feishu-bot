#!/usr/bin/env python3
"""
android_entry.py 补丁工具 — 注入飞书机器人自启动代码

在 GA (Chaquopy) 环境中，每次 GA 冷启动时自动启动飞书机器人。
此脚本在 android_entry.py 中注入 _ensure_fsbot() 函数和 init() 钩子。

用法:
    python setup/patch_android_entry.py [--ga-root DIR]

参数:
    --ga-root DIR  GA 根目录路径，默认自动检测
    --check        仅检查是否已打过补丁（不实际修改）
    --revert       回滚补丁（需有 .bak 备份）

幂等性:
    重复执行安全，已打过补丁则跳过。
"""

import argparse
import os
import re
import shutil
import sys


# ── 常量 ──────────────────────────────────────────────

# 在 _safe() 函数后插入的 _ensure_fsbot() 函数
ENSURE_FSBOT_FUNC = '''
def _ensure_fsbot():
    """GA 启动时自动启动飞书机器人（后台 daemon，阻塞直到连接确认或超时）。
    通过 .lark_workspace 定位工作目录，复用 ga_bot_ctl.start() 的完整流程。
    失败静默 —— 由 _safe() 吞异常，不影响 GA 主流程。"""
    from ga_bot_ctl import start as fsbot_start
    fsbot_start(timeout=30)
'''

# 在 init() 中 _ensure_agent 之后插入的代码块
INIT_HOOK = """    if os.path.exists(os.path.join(ga_dir, '.lark_workspace')):   # 飞书机器人随 GA 自启动
        try: threading.Thread(target=lambda: _safe(_ensure_fsbot), daemon=True).start()
        except Exception: pass"""

# GA 候选路径
GA_CANDIDATES = [
    "/data/data/com.ljq.ga/files/ga",
    "/data/user/0/com.ljq.ga/files/ga",
    os.path.expanduser("~/ga"),
    "/storage/emulated/0/Android/data/com.ljq.ga/files/ga",
]


# ── 工具函数 ──────────────────────────────────────────

def detect_ga_root(custom_path=None):
    """检测 GA 根目录"""
    if custom_path:
        if os.path.isfile(os.path.join(custom_path, "android_entry.py")):
            return custom_path
        return None

    # 优先从当前目录向上找
    d = os.path.abspath(os.getcwd())
    for _ in range(10):
        if os.path.isfile(os.path.join(d, "android_entry.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    # 候选目录
    for d in GA_CANDIDATES:
        if os.path.isfile(os.path.join(d, "android_entry.py")):
            return d
    return None


def is_patched(ga_root):
    """检查 android_entry.py 是否已打过补丁"""
    target = os.path.join(ga_root, "android_entry.py")
    if not os.path.isfile(target):
        return False, "android_entry.py 不存在"
    with open(target, encoding="utf-8") as f:
        content = f.read()
    return "_ensure_fsbot" in content, None


def patch(ga_root, dry_run=False):
    """给 android_entry.py 打补丁"""
    target = os.path.join(ga_root, "android_entry.py")
    if not os.path.isfile(target):
        return False, f"文件不存在: {target}"

    patched, _ = is_patched(ga_root)
    if patched:
        print(f"[INFO] 已打过补丁，跳过: {target}")
        return True, None

    # 读取原文件
    with open(target, encoding="utf-8") as f:
        content = f.read()

    # ── 补丁 1: 在 _safe() 函数后插入 _ensure_fsbot() ──
    # _safe() 函数形态:
    #   def _safe(fn):
    #       try: fn()
    #       except Exception: pass
    pattern_safe = r'(def _safe\(fn\):\s*\n\s+try: fn\(\)\s*\n\s+except Exception: pass\n)'
    match = re.search(pattern_safe, content)
    if not match:
        return False, "未找到 _safe() 函数（android_entry.py 格式可能已变化）"
    insert_point = match.end()
    content = content[:insert_point] + ENSURE_FSBOT_FUNC + content[insert_point:]

    # ── 补丁 2: 在 init() 中 _ensure_agent 之后插入钩子 ──
    # 目标位置: _ensure_agent daemon 线程启动后
    #   try: threading.Thread(target=lambda: _safe(_ensure_agent), daemon=True).start()
    #   except Exception: pass
    pattern_agent = (
        r'(try: threading\.Thread\(target=lambda: _safe\(_ensure_agent\), daemon=True\)\.start\(\)\s*\n'
        r'\s+except Exception: pass\n)'
    )
    match2 = re.search(pattern_agent, content)
    if not match2:
        return False, "未找到 _ensure_agent daemon 线程（android_entry.py 格式可能已变化）"
    insert_point2 = match2.end()
    content = content[:insert_point2] + INIT_HOOK + "\n" + content[insert_point2:]

    if dry_run:
        print(f"[DRY-RUN] 将修改: {target}")
        print(f"  - 插入 _ensure_fsbot() 函数")
        print(f"  - 插入 init() 钩子")
        return True, None

    # 备份
    backup = target + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(target, backup)
        print(f"[INFO] 已备份: {backup}")

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] 已修补 android_entry.py（注入飞书自启动代码）")
    return True, None


def revert(ga_root):
    """回滚补丁"""
    target = os.path.join(ga_root, "android_entry.py")
    backup = target + ".bak"
    if not os.path.isfile(backup):
        return False, f"未找到备份文件: {backup}"
    shutil.copy2(backup, target)
    print(f"[OK] 已回滚: {target}")
    return True, None


# ── 主入口 ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="给 android_entry.py 注入飞书自启动代码",
        epilog="""
示例:
  python setup/patch_android_entry.py              # 自动检测并打补丁
  python setup/patch_android_entry.py --check      # 仅检查是否已打补丁
  python setup/patch_android_entry.py --revert     # 回滚补丁
  python setup/patch_android_entry.py --ga-root /path/to/ga  # 指定 GA 路径
        """,
    )
    parser.add_argument("--ga-root", help="GA 根目录路径")
    parser.add_argument("--check", action="store_true", help="仅检查补丁状态")
    parser.add_argument("--revert", action="store_true", help="回滚补丁")
    parser.add_argument("--dry-run", action="store_true", help="预览操作，不实际修改")
    args = parser.parse_args()

    ga_root = detect_ga_root(args.ga_root)
    if not ga_root:
        print("[ERROR] 未找到 GA 根目录")
        print("   请指定: python setup/patch_android_entry.py --ga-root /path/to/ga")
        return 1

    print(f"[INFO] GA 根目录: {ga_root}")

    if args.check:
        patched, err = is_patched(ga_root)
        if patched:
            print("[OK] 已打过补丁")
            return 0
        else:
            print("[INFO] 未打补丁")
            return 1

    if args.revert:
        ok, err = revert(ga_root)
        if ok:
            return 0
        print(f"[ERROR] {err}")
        return 1

    ok, err = patch(ga_root, dry_run=args.dry_run)
    if ok:
        return 0
    print(f"[ERROR] {err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
