#!/usr/bin/env python3
"""
ga-feishu-bot GitHub 一键推送脚本
=================================
将 ga-feishu-bot/ 全部文件通过 Git Data API 推送到 GitHub。
Token 从项目根目录 .github_token 读取。

用法:
    python push_to_github.py ["commit message"]

安全:
    .github_token 已在 .gitignore 中排除，不会被提交。
"""
import requests, os, base64, json, sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OWNER, REPO = "wellsoren", "ga-feishu-bot"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"

# ── 读取 Token ──
token_file = os.path.join(PROJECT_ROOT, ".github_token")
if not os.path.exists(token_file):
    print("[ERROR] 未找到 .github_token，请创建该文件并写入 GitHub Token")
    sys.exit(1)
with open(token_file) as f:
    TOKEN = f.read().strip()

HEADERS = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}

# ── 获取当前 HEAD ──
r = requests.get(f"{API}/git/ref/heads/main", headers=HEADERS, timeout=30)
if r.status_code != 200:
    print(f"[ERROR] 获取 main 分支失败: {r.status_code}")
    sys.exit(1)
PARENT_SHA = r.json()["object"]["sha"]
print(f"当前 HEAD: {PARENT_SHA[:12]}")

# ── 收集文件 ──
blobs = {}
all_dirs = set()

for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
    dirnames[:] = [d for d in dirnames if d not in ('.git', '__pycache__')]
    rel_dir = os.path.relpath(dirpath, PROJECT_ROOT)
    if rel_dir != '.':
        all_dirs.add(rel_dir)
    for fn in filenames:
        # 跳过密钥文件（不应提交）
        if fn in ('.github_token',):
            continue
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, PROJECT_ROOT)
        with open(full, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        mode = "100755" if fn.endswith(('.py', '.sh')) else "100644"
        blobs[rel] = (b64, mode)

print(f"文件数: {len(blobs)}, 目录数: {len(all_dirs)}")

# ── 上传 Blobs ──
print("上传 Blobs...")
blob_shas = {}
for rel, (b64, mode) in blobs.items():
    r = requests.post(f"{API}/git/blobs", headers=HEADERS,
                      json={"content": b64, "encoding": "base64"}, timeout=60)
    if r.status_code != 201:
        print(f"  ❌ {rel}: {r.status_code}")
        sys.exit(1)
    blob_shas[rel] = (r.json()['sha'], mode)
    print(f"  ✅ {rel}")

# ── 构建树 ──
print("构建目录树...")
tree_shas = {}

# 叶目录
leaf_dirs = [d for d in all_dirs if '/' in d or d in ('channels', 'docs', 'frontends', 'setup')]
# 先深后浅
leaf_dirs_sorted = sorted(set(leaf_dirs), key=lambda d: d.count('/'), reverse=True)
for d in leaf_dirs_sorted:
    items = []
    for rel, (sha, mode) in blob_shas.items():
        if rel.startswith(d + '/'):
            name = rel[len(d)+1:]
            items.append({"path": name, "mode": mode, "type": "blob", "sha": sha})
    if not items:
        continue
    r = requests.post(f"{API}/git/trees", headers=HEADERS,
                      json={"tree": items}, timeout=60)
    if r.status_code != 201:
        print(f"  ❌ {d}/: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    tree_shas[d] = r.json()['sha']

# 父目录（如 deploy/）
for d in sorted(all_dirs - set(leaf_dirs_sorted), key=lambda d: d.count('/'), reverse=True):
    items = []
    for rel, (sha, mode) in blob_shas.items():
        if rel.startswith(d + '/') and rel.count('/') == d.count('/') + 1:
            name = rel[len(d)+1:]
            items.append({"path": name, "mode": mode, "type": "blob", "sha": sha})
    for sub in all_dirs:
        if sub.startswith(d + '/') and sub.count('/') == d.count('/') + 1:
            items.append({"path": sub[len(d)+1:], "mode": "040000", "type": "tree", "sha": tree_shas[sub]})
    if not items:
        continue
    r = requests.post(f"{API}/git/trees", headers=HEADERS,
                      json={"tree": items}, timeout=60)
    tree_shas[d] = r.json()['sha']

# 根树
root_items = []
for rel, (sha, mode) in blob_shas.items():
    if '/' not in rel:
        root_items.append({"path": rel, "mode": mode, "type": "blob", "sha": sha})
for d in all_dirs:
    if '/' not in d:
        root_items.append({"path": d, "mode": "040000", "type": "tree", "sha": tree_shas[d]})

r = requests.post(f"{API}/git/trees", headers=HEADERS,
                  json={"tree": root_items}, timeout=60)
root_sha = r.json()['sha']
print(f"  根树: {root_sha[:12]}")

# ── 创建 Commit ──
msg = sys.argv[1] if len(sys.argv) > 1 else "update"
r = requests.post(f"{API}/git/commits", headers=HEADERS, json={
    "message": msg,
    "tree": root_sha,
    "parents": [PARENT_SHA]
}, timeout=60)
commit_sha = r.json()['sha']
print(f"Commit: {commit_sha[:12]} — {msg}")

# ── 推送 ──
r = requests.patch(f"{API}/git/refs/heads/main", headers=HEADERS,
                   json={"sha": commit_sha, "force": True}, timeout=30)
print(f"✅ 推送成功！https://github.com/{OWNER}/{REPO}")
