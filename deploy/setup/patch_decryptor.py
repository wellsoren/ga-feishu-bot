#!/usr/bin/env python3
"""
解密补丁工具 — 用 pyaes 替换 lark_oapi 的 pycryptodome 依赖

在 GA (Chaquopy) 环境中，pycryptodome 因 C 扩展无法安装。
此脚本将 lark_oapi 的 decryptor.py 中的 Crypto.Cipher 调用改为 pyaes 实现。

用法:
    python setup/patch_decryptor.py [--sp-dir DIR]

参数:
    --sp-dir DIR  site-packages 目录，默认自动检测
"""
import argparse
import os
import sys


# 补丁后的 decryptor.py 内容（AES-CBC 解密用 pyaes 实现）
PATCHED_DECRYPTOR = """\
# Patched for GA (Chaquopy) — replaced pycryptodome with pyaes
# Original used: from Crypto.Cipher import AES
import pyaes


def _aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    \"\"\"AES-CBC 解密，纯 Python 实现（替代 pycryptodome）\"\"\"
    aes = pyaes.AES(key)
    decrypted = bytearray()
    previous = iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        # AES ECB 解密
        dec_block = bytearray(aes.decrypt(block))
        # CBC: 与前一块密文（或 IV）异或
        for j in range(len(dec_block)):
            dec_block[j] ^= previous[j]
        decrypted.extend(dec_block)
        previous = block
    # 移除 PKCS7 填充
    pad_len = decrypted[-1]
    if pad_len > 0 and pad_len <= 16:
        return bytes(decrypted[:-pad_len])
    return bytes(decrypted)


def decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    \"\"\"AES-CBC 解密入口\"\"\"
    return _aes_cbc_decrypt(ciphertext, key, iv)
"""


def find_site_packages():
    """自动查找 site-packages 目录"""
    candidates = [
        "/data/data/com.ljq.ga/files/ga/temp/site-packages",
        "/data/user/0/com.ljq.ga/files/ga/temp/site-packages",
        os.path.expanduser("~/ga/temp/site-packages"),
        os.path.join(os.getcwd(), "temp", "site-packages"),
    ]
    for d in candidates:
        decryptor = os.path.join(d, "lark_oapi", "core", "utils", "decryptor.py")
        if os.path.isfile(decryptor):
            return d
    return None


def patch(sp_dir):
    decryptor_path = os.path.join(sp_dir, "lark_oapi", "core", "utils", "decryptor.py")
    if not os.path.isfile(decryptor_path):
        print(f"[ERROR] 未找到: {decryptor_path}")
        return False

    # 备份
    backup = decryptor_path + ".bak"
    if not os.path.exists(backup):
        import shutil
        shutil.copy2(decryptor_path, backup)
        print(f"[INFO] 已备份原始文件: {backup}")

    with open(decryptor_path, "w", encoding="utf-8") as f:
        f.write(PATCHED_DECRYPTOR)
    print(f"[OK] 已修补: {decryptor_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="修补 lark_oapi 解密器")
    parser.add_argument("--sp-dir", help="site-packages 目录路径")
    args = parser.parse_args()

    sp_dir = args.sp_dir or find_site_packages()
    if not sp_dir:
        print("[ERROR] 未找到 site-packages 目录")
        print("   请指定: python setup/patch_decryptor.py --sp-dir /path/to/site-packages")
        return 1

    print(f"[INFO] site-packages: {sp_dir}")
    if patch(sp_dir):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
