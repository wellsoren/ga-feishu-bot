#!/usr/bin/env python3
"""
飞书机器人一键安装脚本
=======================
在已有 GA (com.ljq.ga) 环境的手机上自动部署飞书机器人。

用法:
    python install.py                    # 普通安装（在线下载依赖）
    python install.py --offline          # 离线安装（用本地 site_packages.tar.gz）
    python install.py --ga-root PATH     # 指定 GA 根目录
    python install.py --dry-run          # 仅检查环境，不实际安装

工作流程:
    1. 检测 GA 环境 → 2. 部署源码 → 3. 安装依赖 → 4. 打解密补丁
    → 5. 生成配置模板 → 6. 注册工作目录 → 7. 注入自启动补丁 → 8. 验证安装
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request


# ── 常量 ──────────────────────────────────────────────
VERSION = "1.0.1"
PACKAGE_NAME = "ga_feishu_deploy"

# 需要部署的文件映射（src_in_package → dest_relative_to_lark_bot）
DEPLOY_FILES = {
    "files/frontends/__init__.py":        "frontends/__init__.py",
    "files/frontends/fsapp.py":          "frontends/fsapp.py",
    "files/frontends/chatapp_common.py": "frontends/chatapp_common.py",
    "files/start_fsbot.py":              "start_fsbot.py",
    "files/fsbot_ctl.py":                "fsbot_ctl.py",
    "files/lark_native.py":              "lark_native.py",
}

# Python 依赖列表（用于在线下载）
DEPENDENCIES = [
    "exceptiongroup", "sniffio", "h11", "idna", "certifi",
    "pyaes", "anyio", "requests-toolbelt", "httpcore", "httpx",
    "websockets", "lark-oapi",
]

# GA 候选路径
GA_CANDIDATES = [
    "/data/data/com.ljq.ga/files/ga",
    "/data/user/0/com.ljq.ga/files/ga",
    os.path.expanduser("~/ga"),
    "/storage/emulated/0/Android/data/com.ljq.ga/files/ga",
]


# ── 工具函数 ──────────────────────────────────────────

def color(s, code=32):
    """返回带 ANSI 颜色的文本"""
    return f"\033[{code}m{s}\033[0m"


def info(msg):
    print(f"  {color('•', 36)} {msg}")


def ok(msg):
    print(f"  {color('✓', 32)} {msg}")


def warn(msg):
    print(f"  {color('⚠', 33)} {msg}")


def fail(msg):
    print(f"  {color('✗', 31)} {msg}")


def section(title):
    print(f"\n{color('━━━', 34)} {color(title, 34)}")


# ── 环境检测 ──────────────────────────────────────────

def detect_ga_root(custom_path=None):
    """检测 GA 根目录"""
    if custom_path:
        if os.path.isfile(os.path.join(custom_path, "ga_android.py")):
            return custom_path
        return None

    for d in GA_CANDIDATES:
        if os.path.isfile(os.path.join(d, "ga_android.py")):
            return d
    return None


def check_ga_environment(ga_root):
    """检查 GA 环境是否可用"""
    checks = {}
    checks["ga_root"] = bool(ga_root)

    # 检查 Python (Chaquopy)
    sp_dir = os.path.join(ga_root, "lark_bot", "site-packages") if ga_root else ""
    checks["has_chaquopy"] = True  # 有 GA 就有 Chaquopy

    # 检查磁盘空间
    if ga_root:
        statvfs = os.statvfs(ga_root)
        free_mb = (statvfs.f_frsize * statvfs.f_bavail) / (1024 * 1024)
        checks["free_space_mb"] = int(free_mb)
        checks["enough_space"] = free_mb > 200  # 至少 200MB
    else:
        checks["free_space_mb"] = 0
        checks["enough_space"] = False

    return checks


# ── 文件部署 ──────────────────────────────────────────

def _find_source_root(pkg_dir):
    """检测源码根目录：独立部署包（files/）vs GitHub 仓库（父目录）"""
    # 独立部署包模式：pkg_dir 下有 files/、setup/
    if os.path.isdir(os.path.join(pkg_dir, "files")):
        return pkg_dir, "files"
    # GitHub 仓库模式：源码在父目录
    parent = os.path.dirname(pkg_dir)
    if os.path.isdir(os.path.join(parent, "frontends")):
        return parent, "repo"
    return pkg_dir, "files"  # 默认回退


def deploy_source_files(pkg_dir, lark_bot_dir):
    """部署源码文件到目标目录"""
    section("部署源码文件")

    source_root, mode = _find_source_root(pkg_dir)
    mode_label = "独立部署包" if mode == "files" else "GitHub 仓库"
    info(f"源码来源: {mode_label} ({source_root})")

    os.makedirs(lark_bot_dir, exist_ok=True)
    os.makedirs(os.path.join(lark_bot_dir, "frontends"), exist_ok=True)
    os.makedirs(os.path.join(lark_bot_dir, "channels"), exist_ok=True)

    deployed = 0
    for src_rel, dst_rel in DEPLOY_FILES.items():
        # 独立包模式: "files/frontends/..." → source_root + "/files/frontends/..."
        # GitHub 模式: "files/frontends/..." → source_root + "/frontends/..."
        src_rel_actual = src_rel if mode == "files" else src_rel.replace("files/", "", 1)
        src = os.path.join(source_root, src_rel_actual)
        dst = os.path.join(lark_bot_dir, dst_rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            deployed += 1
            ok(f"{dst_rel}")
        else:
            warn(f"未找到: {src_rel_actual}")

    # 复制 setup 目录（独立包模式：pkg_dir/setup/；GitHub 模式：pkg_dir/../setup/ 或 pkg_dir/setup/）
    setup_src = os.path.join(pkg_dir, "setup")  # deploy/setup/
    if not os.path.isdir(setup_src):
        # 尝试在源码根目录找 setup/
        setup_src = os.path.join(source_root, "setup")
    setup_dst = os.path.join(lark_bot_dir, "setup")
        if os.path.isdir(setup_dst):
            shutil.rmtree(setup_dst)
        shutil.copytree(setup_src, setup_dst)
        ok("setup/ 工具脚本")
        deployed += 1

    return deployed


# ── 依赖安装 ──────────────────────────────────────────

def download_wheel(pkg_name, dest_dir):
    """从 PyPI 下载纯 Python wheel"""
    url = f"https://pypi.org/pypi/{pkg_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return None, str(e)

    ver = data["info"]["version"]
    releases = data["releases"].get(ver, [])

    # 优先: py3-none-any.whl
    for f in releases:
        if f["packagetype"] == "bdist_wheel" and "py3-none-any" in f["filename"]:
            return _do_download(f["url"], f["filename"], dest_dir), None

    # 其次: 任意纯 Python wheel
    for f in releases:
        if f["packagetype"] == "bdist_wheel" and "-none-any" in f["filename"]:
            return _do_download(f["url"], f["filename"], dest_dir), None

    # 再其次: sdist
    for f in releases:
        if f["packagetype"] == "sdist":
            return _do_download(f["url"], f["filename"], dest_dir), None

    return None, "未找到兼容的 wheel 或 sdist"


def _do_download(url, filename, dest_dir):
    dest = os.path.join(dest_dir, filename)
    try:
        urllib.request.urlretrieve(url, dest)
        return dest
    except Exception:
        return None


def install_deps_online(lark_bot_dir):
    """在线下载并安装依赖"""
    section("安装依赖（在线下载）")

    sp_dir = os.path.join(lark_bot_dir, "site-packages")
    os.makedirs(sp_dir, exist_ok=True)
    wheel_dir = os.path.join(lark_bot_dir, "deps_cache")
    os.makedirs(wheel_dir, exist_ok=True)

    success, failed = [], []
    for pkg in DEPENDENCIES:
        info(f"正在下载 {pkg}...")
        wheel_path, err = download_wheel(pkg, wheel_dir)
        if wheel_path:
            ok(f"{pkg}")
            success.append(pkg)
        else:
            warn(f"{pkg} 下载失败: {err}")
            failed.append(pkg)

    # 解压所有 wheel 到 site-packages
    if success:
        import zipfile
        wheels = [f for f in os.listdir(wheel_dir) if f.endswith(".whl")]
        for w in wheels:
            whl_path = os.path.join(wheel_dir, w)
            try:
                with zipfile.ZipFile(whl_path, 'r') as zf:
                    zf.extractall(sp_dir)
            except Exception as e:
                warn(f"解压失败 {w}: {e}")

    return success, failed


def install_deps_offline(pkg_dir, lark_bot_dir):
    """从本地 site_packages.tar.gz 安装依赖"""
    section("安装依赖（离线包）")

    sp_dir = os.path.join(lark_bot_dir, "site-packages")
    os.makedirs(sp_dir, exist_ok=True)

    tar_path = os.path.join(pkg_dir, "site_packages.tar.gz")
    if not os.path.isfile(tar_path):
        return False, "未找到 site_packages.tar.gz"

    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(sp_dir)
        ok(f"已解压到 site-packages/")
        return True, None
    except Exception as e:
        return False, str(e)


def apply_patch(lark_bot_dir):
    """应用解密库补丁"""
    section("应用解密库补丁")

    sp_dir = os.path.join(lark_bot_dir, "site-packages")
    decryptor = os.path.join(sp_dir, "lark_oapi", "core", "utils", "decryptor.py")

    if not os.path.isfile(decryptor):
        return False, f"未找到: {decryptor}"

    # pyaes 补丁内容
    patched_code = """\
# Patched for GA (Chaquopy) — replaced pycryptodome with pyaes
import pyaes


def _aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    aes = pyaes.AES(key)
    decrypted = bytearray()
    previous = iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        dec_block = bytearray(aes.decrypt(block))
        for j in range(len(dec_block)):
            dec_block[j] ^= previous[j]
        decrypted.extend(dec_block)
        previous = block
    pad_len = decrypted[-1]
    if pad_len > 0 and pad_len <= 16:
        return bytes(decrypted[:-pad_len])
    return bytes(decrypted)


def decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    return _aes_cbc_decrypt(ciphertext, key, iv)
"""

    # 备份原始文件
    backup = decryptor + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(decryptor, backup)

    with open(decryptor, "w", encoding="utf-8") as f:
        f.write(patched_code)
    ok("lark_oapi 解密器已替换为 pyaes")
    return True, None


# ── 配置生成 ──────────────────────────────────────────

def create_config_template(lark_bot_dir, force=False):
    """生成 mykey.json 配置模板"""
    section("创建配置模板")

    config_path = os.path.join(lark_bot_dir, "mykey.json")

    if os.path.isfile(config_path) and not force:
        warn(f"配置文件已存在: {config_path}")
        warn("如需重新生成，请加 --force 参数")
        return config_path, False

    template = {
        "fs_app_id": "cli_xxxxxxxxxxxxxxxxxx",
        "fs_app_secret": "your_app_secret_here",
        "fs_allowed_users": [],
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    ok(f"已创建: mykey.json")
    info("请在飞书开放平台 (https://open.feishu.cn) 创建应用后，")
    info("将 App ID 和 App Secret 填入 mykey.json")
    return config_path, True


# ── 工作目录注册 ──────────────────────────────────────

def register_workspace(lark_bot_dir):
    """注册工作目录"""
    section("注册工作目录")

    ws_file = os.path.join(lark_bot_dir, ".lark_workspace")
    ws_data = {
        "workspace": lark_bot_dir,
        "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "type": "feishu_bot",
        "startup_script": "start_fsbot.py",
        "config": "mykey.json",
        "log": "bot.log",
        "description": "飞书机器人工作目录（一键部署）",
    }
    with open(ws_file, "w", encoding="utf-8") as f:
        json.dump(ws_data, f, indent=2, ensure_ascii=False)
    ok(f"工作目录已注册")
    return ws_file


# ── android_entry 自启动补丁 ──────────────────────────

def apply_android_entry_patch(ga_root):
    """给 android_entry.py 注入飞书自启动代码（幂等）"""
    section("应用自启动补丁")

    # 动态导入同包下的补丁脚本
    import importlib
    try:
        patcher = importlib.import_module("setup.patch_android_entry")
    except ImportError:
        # 如果 setup 不在 sys.path 中，手动加载
        setup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup")
        if setup_dir not in sys.path:
            sys.path.insert(0, setup_dir)
        try:
            import patch_android_entry as patcher
        except ImportError:
            warn("未找到 patch_android_entry.py，跳过自启动补丁")
            return False, "补丁脚本缺失"

    ok_status, err = patcher.patch(ga_root)
    if ok_status:
        ok("android_entry.py 已注入自启动代码（_ensure_fsbot）")
        return True, None
    elif err:
        warn(f"自启动补丁: {err}")
        return False, err
    return True, None


# ── 验证 ──────────────────────────────────────────────

def verify_installation(lark_bot_dir):
    """验证安装结果"""
    section("验证安装")
    checks = []

    # 检查关键文件
    required_files = [
        "frontends/fsapp.py",
        "frontends/chatapp_common.py",
        "start_fsbot.py",
        "fsbot_ctl.py",
        "lark_native.py",
        "setup/fetch_deps.py",
        "setup/patch_decryptor.py",
    ]
    for f in required_files:
        path = os.path.join(lark_bot_dir, f)
        if os.path.isfile(path):
            checks.append((f, True, ""))
        else:
            checks.append((f, False, "文件缺失"))

    # 检查 site-packages
    sp_dir = os.path.join(lark_bot_dir, "site-packages")
    sp_ok = os.path.isdir(sp_dir) and len(os.listdir(sp_dir)) > 3
    checks.append(("site-packages/", sp_ok, "依赖未安装" if not sp_ok else ""))

    # 检查补丁
    decryptor = os.path.join(sp_dir, "lark_oapi", "core", "utils", "decryptor.py")
    patched = os.path.isfile(decryptor)
    if patched:
        with open(decryptor) as f:
            patched = "pyaes" in f.read()
    checks.append(("解密补丁", patched, "补丁未应用" if not patched else ""))

    # 检查配置
    config_path = os.path.join(lark_bot_dir, "mykey.json")
    config_ok = os.path.isfile(config_path)
    checks.append(("mykey.json", config_ok, "配置未生成" if not config_ok else ""))

    # 打印结果
    all_ok = True
    for name, ok_status, err in checks:
        if ok_status:
            ok(name)
        else:
            fail(f"{name}: {err}")
            all_ok = False

    return all_ok, checks


# ── 主流程 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=f"飞书机器人一键部署 v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python install.py                    # 一键安装（在线下载依赖）
  python install.py --offline          # 离线安装（需有 site_packages.tar.gz）
  python install.py --force            # 强制覆盖已有文件
  python install.py --dry-run          # 仅检查环境
  python install.py --ga-root /custom/path   # 指定 GA 目录
        """,
    )
    parser.add_argument("--offline", action="store_true", help="离线模式（使用本地 site_packages.tar.gz）")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有文件")
    parser.add_argument("--ga-root", help="指定 GA 根目录路径")
    parser.add_argument("--dry-run", action="store_true", help="仅检查环境，不执行安装")
    parser.add_argument("--version", action="store_true", help="显示版本号")

    args = parser.parse_args()

    if args.version:
        print(f"ga_feishu_deploy v{VERSION}")
        return 0

    # 部署包所在目录（本脚本所在目录）
    pkg_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"\n{'='*50}")
    print(f"  飞书机器人 一键部署 v{VERSION}")
    print(f"  适配: GA (com.ljq.ga) + Chaquopy 环境")
    print(f"{'='*50}")

    # ── 第 1 步：检测 GA 环境 ──
    section("检测 GA 环境")

    ga_root = detect_ga_root(args.ga_root)
    if not ga_root:
        fail("未找到 GA 环境！")
        print()
        print("  请先安装 GA App (com.ljq.ga)")
        print("  或使用 --ga-root 参数手动指定 GA 根目录")
        print()
        info("GA 候选路径:")
        for c in GA_CANDIDATES:
            print(f"    {c}")
        return 1

    ok(f"GA 根目录: {ga_root}")

    # 如果是 dry-run，到此结束
    if args.dry_run:
        info("Dry-run 模式，未执行任何安装操作")
        return 0

    # ── 第 2 步：创建 lark_bot 目录 ──
    lark_bot_dir = os.path.join(ga_root, "lark_bot")
    if os.path.isdir(lark_bot_dir) and not args.force:
        warn(f"lark_bot 目录已存在: {lark_bot_dir}")
        warn("如需重新部署，请加 --force 参数")
        warn("或先手动删除: rm -rf " + lark_bot_dir)
        return 1

    if os.path.isdir(lark_bot_dir):
        shutil.rmtree(lark_bot_dir)
    os.makedirs(lark_bot_dir, exist_ok=True)

    # ── 第 3 步：部署源码文件 ──
    deploy_source_files(pkg_dir, lark_bot_dir)

    # ── 第 4 步：安装依赖 ──
    if args.offline:
        dep_ok, dep_err = install_deps_offline(pkg_dir, lark_bot_dir)
        if not dep_ok:
            warn(f"离线安装失败: {dep_err}")
            warn("尝试在线下载...")
            install_deps_online(lark_bot_dir)
    else:
        success_deps, failed_deps = install_deps_online(lark_bot_dir)
        if failed_deps:
            warn(f"以下依赖在线下载失败: {', '.join(failed_deps)}")
            if os.path.isfile(os.path.join(pkg_dir, "site_packages.tar.gz")):
                warn("检测到离线包，尝试离线安装...")
                install_deps_offline(pkg_dir, lark_bot_dir)

    # ── 第 5 步：应用解密补丁 ──
    patch_ok, patch_err = apply_patch(lark_bot_dir)
    if not patch_ok:
        warn(f"补丁应用失败: {patch_err}")

    # ── 第 6 步：生成配置模板 ──
    create_config_template(lark_bot_dir, force=args.force)

    # ── 第 7 步：注册工作目录 ──
    register_workspace(lark_bot_dir)

    # ── 第 8 步：注入自启动补丁 ──
    apply_android_entry_patch(ga_root)

    # ── 第 9 步：验证安装 ──
    all_ok, _ = verify_installation(lark_bot_dir)

    # ── 完成 ──
    print(f"\n{'='*50}")
    if all_ok:
        print(f"  {color('✓ 安装完成！', 32)}")
    else:
        print(f"  {color('⚠ 安装完成（部分检查未通过）', 33)}")
    print(f"{'='*50}")
    print()
    print(f"  {color('下一步操作:', 36)}")
    print()
    print(f"  1. 编辑配置文件:")
    print(f"     {os.path.join(lark_bot_dir, 'mykey.json')}")
    print(f"     填入你的飞书 App ID 和 App Secret")
    print()
    print(f"  2. 重启 GA 应用:")
    print(f"     GA 冷启动时飞书机器人将自动上线（已注入自启动代码）")
    print()
    print(f"  3. 手动控制（可选）:")
    print(f"     from ga_bot_ctl import start, stop, status")
    print(f"     status()  # 查看运行状态")
    print()
    print(f"  {color('飞书开放平台:', 36)} https://open.feishu.cn")
    print(f"  {color('项目文档:', 36)} 阅读 README.md 获取完整说明")
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
