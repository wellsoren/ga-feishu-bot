#!/usr/bin/env python3
"""
依赖下载工具 — 为 GA (Chaquopy) 环境下载纯 Python wheels

用法:
    python setup/fetch_deps.py [--dest DIR]

参数:
    --dest DIR  下载目录，默认 ./deps/
"""
import argparse
import json
import os
import sys
import urllib.request


def download_best_wheel(pkg_name, dest_dir):
    """从 PyPI 下载最匹配的纯 Python wheel"""
    url = f"https://pypi.org/pypi/{pkg_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  [FAIL] 获取 {pkg_name} 信息失败: {e}")
        return None

    ver = data["info"]["version"]
    releases = data["releases"].get(ver, [])

    # 优先: py3-none-any.whl (纯 Python、无 ABI 依赖)
    for f in releases:
        if f["packagetype"] == "bdist_wheel" and "py3-none-any" in f["filename"]:
            return _download(f["url"], f["filename"], f["size"], dest_dir)

    # 其次: 任意纯 Python wheel (如 py2.py3-none-any.whl)
    for f in releases:
        if f["packagetype"] == "bdist_wheel" and "-none-any" in f["filename"]:
            return _download(f["url"], f["filename"], f["size"], dest_dir)

    # 再其次: sdist tar.gz
    for f in releases:
        if f["packagetype"] == "sdist":
            return _download(f["url"], f["filename"], f["size"], dest_dir)

    print(f"  [WARN] 未找到 {pkg_name} 的纯 Python 包")
    return None


def _download(url, filename, size, dest_dir):
    dest = os.path.join(dest_dir, filename)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        print(f"  [SKIP] {filename} (已存在)")
        return dest

    print(f"  [DL] {filename} ({size} bytes)...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        print("OK")
        return dest
    except Exception as e:
        print(f"FAIL: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="下载 GA-Feishu-Bot 依赖")
    parser.add_argument("--dest", default=os.path.join(os.getcwd(), "deps"),
                        help="下载目标目录 (默认 ./deps)")
    args = parser.parse_args()

    os.makedirs(args.dest, exist_ok=True)

    PACKAGES = [
        "exceptiongroup", "sniffio", "h11", "idna", "certifi",
        "pyaes", "anyio", "requests-toolbelt", "httpcore", "httpx",
        "websockets", "lark-oapi",
    ]

    print(f"下载依赖到: {args.dest}")
    print("=" * 50)

    success = []
    failed = []
    for pkg in PACKAGES:
        print(f"[{pkg}]")
        result = download_best_wheel(pkg, args.dest)
        if result:
            success.append(pkg)
        else:
            failed.append(pkg)

    print("=" * 50)
    print(f"成功: {len(success)}/{len(PACKAGES)} ({', '.join(success)})")
    if failed:
        print(f"失败: {', '.join(failed)}")
        return 1

    print("\n💡 下一步: 解压所有 .whl 文件到 GA 的 temp/site-packages/ 目录")
    print("   或运行项目的 install.py 自动完成")


if __name__ == "__main__":
    sys.exit(main())
