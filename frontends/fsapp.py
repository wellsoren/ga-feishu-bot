import argparse, asyncio, difflib, importlib.util, json, logging, os, queue as Q, re, sys, threading, time, uuid
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

logger = logging.getLogger(__name__)


def _ensure_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_root_dir():
    root = os.environ.get("GA_WORKSPACE_ROOT")
    if root:
        return _ensure_dir(Path(root).expanduser().resolve())
    return _ensure_dir(Path(PROJECT_ROOT).resolve())


def _workspace_config_dir(root=None):
    base = Path(root).expanduser().resolve() if root else _workspace_root_dir()
    if base.name == "ga_config":
        return _ensure_dir(base)
    return _ensure_dir(base / "ga_config")


def _load_dict_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.suffix == ".py":
            mod_name = f"_fs_mykey_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            data = {k: v for k, v in vars(module).items() if not k.startswith("_")}
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.error("load config failed %s: %s", path, e)
        return None


def _resolve_mykey_path():
    workspace_root = _workspace_root_dir()
    config_root = _workspace_config_dir(workspace_root)
    candidates = [
        config_root / "mykey.json",
        config_root / "mykey.py",
        workspace_root / "mykey.json",
        workspace_root / "mykey.py",
        Path(PROJECT_ROOT) / "mykey.json",
        Path(PROJECT_ROOT) / "mykey.py",
    ]
    for candidate in candidates:
        if _load_dict_config(candidate):
            return candidate
    return candidates[0]


def _ensure_runtime_paths():
    workspace_root = _workspace_root_dir()
    config_root = _workspace_config_dir(workspace_root)
    os.environ.setdefault("GA_WORKSPACE_ROOT", str(workspace_root))
    os.environ.setdefault("GA_USER_DATA_DIR", str(config_root))
    return str(workspace_root), str(config_root)


_ensure_runtime_paths()
from agentmain import GeneraticAgent
from frontends.chatapp_common import AgentChatMixin, split_text
from frontends.feishu_context import (
    build_context_prompt,
    build_extra_sys_prompt,
    clear_context_env,
    set_context_env,
)

_TAG_PATS = [r"<" + t + r">.*?</" + t + r">" for t in ("thinking", "summary", "tool_use", "file_content")]
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
_AUDIO_EXTS = {".opus", ".mp3", ".wav", ".m4a", ".aac"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_FILE_TYPE_MAP = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
_MSG_TYPE_MAP = {"image": "[image]", "audio": "[audio]", "file": "[file]", "media": "[media]", "sticker": "[sticker]"}

TEMP_DIR = os.path.join(PROJECT_ROOT, "temp")
MEDIA_DIR = os.path.join(TEMP_DIR, "feishu_media")
os.makedirs(MEDIA_DIR, exist_ok=True)


_DEDUP_TTL_SEC = 10 * 60
_DEDUP_MAX = 2000
_DEDUP_LOCK = threading.Lock()
_SEEN_MESSAGES = {}

# ===== 可调配置常量 =====
_TEXT_SPLIT_LIMIT = 4000       # 纯文本消息分片字符上限
_CARD_SPLIT_LIMIT = 12000      # 单张卡片 markdown 元素字符上限
_RETRY_INIT_DELAY = 5          # 长连接重试初始延迟(秒)
_RETRY_MAX_DELAY = 120         # 长连接重试最大延迟(秒)
_LOG_TEXT_PREVIEW = 200        # 日志中文本预览截断长度
_BANNER_SEP_LEN = 50           # 启动横幅分隔线长度


def _claim_message_once(message_id):
    """Best-effort cross-platform dedup for Feishu reconnect redeliveries."""
    if not message_id:
        return True
    now = time.time()
    with _DEDUP_LOCK:
        expired = [mid for mid, ts in _SEEN_MESSAGES.items() if now - ts > _DEDUP_TTL_SEC]
        for mid in expired:
            _SEEN_MESSAGES.pop(mid, None)
        if len(_SEEN_MESSAGES) > _DEDUP_MAX:
            for mid, _ in sorted(_SEEN_MESSAGES.items(), key=lambda item: item[1])[:len(_SEEN_MESSAGES) - _DEDUP_MAX]:
                _SEEN_MESSAGES.pop(mid, None)
        if message_id in _SEEN_MESSAGES:
            return False
        _SEEN_MESSAGES[message_id] = now
        return True


def _clean(text):
    for pat in _TAG_PATS:
        text = re.sub(pat, "", text or "", flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_files(text):
    return re.findall(r"\[FILE:([^\]]+)\]", text or "")


def _strip_files(text):
    return re.sub(r"\[FILE:[^\]]+\]", "", text or "").strip()


def _display_text(text):
    cleaned = _strip_files(_clean(text))
    if cleaned:
        return cleaned
    return ""   # 空输出是正常行为（纯工具调用轮），跳过 Output 区块


def _to_allowed_set(value):
    if value is None:
        return set()
    if isinstance(value, str):
        value = [value]
    return {str(x).strip() for x in value if str(x).strip()}


def _parse_json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _extract_share_card_content(content_json, msg_type):
    parts = []
    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")
    return "\n".join([p for p in parts if p]).strip() or f"[{msg_type}]"


def _extract_interactive_content(content):
    parts = []
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            return [content] if content.strip() else []
    if not isinstance(content, dict):
        return parts
    title = content.get("title")
    if isinstance(title, dict):
        title_text = title.get("content", "") or title.get("text", "")
        if title_text:
            parts.append(f"title: {title_text}")
    elif isinstance(title, str) and title:
        parts.append(f"title: {title}")
    elements = content.get("elements", [])
    if isinstance(elements, list):
        for row in elements:
            if isinstance(row, dict):
                parts.extend(_extract_element_content(row))
            elif isinstance(row, list):
                for el in row:
                    parts.extend(_extract_element_content(el))
    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))
    header = content.get("header", {})
    if isinstance(header, dict):
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")
    return [p for p in parts if p]


def _extract_element_content(element):
    parts = []
    if not isinstance(element, dict):
        return parts
    tag = element.get("tag", "")
    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)
    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str) and text:
            parts.append(text)
        for field in element.get("fields", []) or []:
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    content = field_text.get("content", "") or field_text.get("text", "")
                    if content:
                        parts.append(content)
    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)
    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            content = text.get("content", "") or text.get("text", "")
            if content:
                parts.append(content)
        url = element.get("url", "") or (element.get("multi_url", {}) or {}).get("url", "")
        if url:
            parts.append(f"link: {url}")
    elif tag == "img":
        alt = element.get("alt", {})
        if isinstance(alt, dict):
            parts.append(alt.get("content", "[image]") or "[image]")
        else:
            parts.append("[image]")
    for child in element.get("elements", []) or []:
        parts.extend(_extract_element_content(child))
    for col in element.get("columns", []) or []:
        for child in (col.get("elements", []) if isinstance(col, dict) else []):
            parts.extend(_extract_element_content(child))
    return parts


def _extract_post_content(content_json):
    def _parse_block(block):
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if block.get("title"):
            texts.append(block.get("title"))
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and el.get("image_key"):
                    images.append(el["image_key"])
        text = " ".join([t for t in texts if t]).strip()
        return text or None, images

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs
    return "", []


AGENT_TIMEOUT_SEC = 900

agent = None
agent_error = None
agent_thread = None
client, user_tasks, app = None, {}, None
agent_lock = threading.Lock()


def _load_config():
    path = _resolve_mykey_path()
    if not path or not path.exists():
        return {}, str(path or "")
    try:
        data = _load_dict_config(path)
        return data if isinstance(data, dict) else {}, str(path)
    except Exception as e:
        logger.error("load mykey failed %s: %s", path, e)
        return {}, str(path)


def _feishu_config():
    cfg, path = _load_config()
    app_id = str(cfg.get("fs_app_id", "") or "").strip()
    app_secret = str(cfg.get("fs_app_secret", "") or "").strip()
    allowed = _to_allowed_set(cfg.get("fs_allowed_users", []))
    return app_id, app_secret, allowed, (not allowed or "*" in allowed), path


APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, CONFIG_PATH = _feishu_config()


def get_agent():
    global agent, agent_error, agent_thread
    with agent_lock:
        if agent is not None:
            return agent
        if agent_error:
            raise RuntimeError(agent_error)
        try:
            agent = GeneraticAgent()
            agent_thread = threading.Thread(target=agent.run, daemon=True)
            agent_thread.start()
            return agent
        except Exception as e:
            agent_error = str(e)
            raise


def create_client():
    return lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).log_level(lark.LogLevel.INFO).build()


def _mask_secret(value):
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def check_config(init_agent=False):
    app_id, app_secret, allowed, public_access, path = _feishu_config()
    result = {
        "config_path": path,
        "app_id": app_id,
        "app_secret": _mask_secret(app_secret),
        "app_secret_present": bool(app_secret),
        "public_access": public_access,
        "allowed_users": sorted(allowed),
        "ready": bool(app_id and app_secret),
    }
    if init_agent:
        try:
            ga = get_agent()
            result["agent_ready"] = True
            result["llm_count"] = len(ga.list_llms()) if hasattr(ga, "list_llms") else 0
            result["current_llm"] = ga.get_llm_name() if getattr(ga, "llmclient", None) else ""
        except Exception as e:
            result["agent_ready"] = False
            result["agent_error"] = str(e)
    return result


def _card_raw(elements):
    return json.dumps({
        "schema": "2.0",
        "config": {"streaming_mode": False, "width_mode": "fill"},
        "body": {"elements": elements},
    }, ensure_ascii=False)


def _card(text):
    return _card_raw([{"tag": "markdown", "content": text}])


def _send_raw(receive_id, payload, msg_type, rtype):
    try:
        body = CreateMessageRequest.builder().receive_id_type(rtype).request_body(
            CreateMessageRequestBody.builder().receive_id(receive_id).msg_type(msg_type).content(payload).build()
        ).build()
        r = client.im.v1.message.create(body)
        if r.success():
            return r.data.message_id if r.data else None
        logger.error("发送失败: %s, %s", r.code, r.msg)
    except Exception as e:
        logger.exception("send_message failed: %s", e)
    return None


def _patch_card(message_id, card_json):
    try:
        body = PatchMessageRequest.builder().message_id(message_id).request_body(
            PatchMessageRequestBody.builder().content(card_json).build()
        ).build()
        r = client.im.v1.message.patch(body)
        if not r.success():
            logger.error("patch_card 失败: %s, %s", r.code, r.msg)
        return r.success()
    except Exception as e:
        logger.exception("patch_card exception: %s", e)
        return False


def send_message(receive_id, content, msg_type="text", use_card=False, receive_id_type="open_id"):
    if use_card:
        return _send_raw(receive_id, _card(content), "interactive", receive_id_type)
    if msg_type == "text":
        return _send_raw(receive_id, json.dumps({"text": content}, ensure_ascii=False), "text", receive_id_type)
    return _send_raw(receive_id, content, msg_type, receive_id_type)


def update_message(message_id, content):
    return _patch_card(message_id, _card(content))


def _upload_image_sync(file_path):
    try:
        with open(file_path, "rb") as f:
            request = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder().image_type("message").image(f).build()
            ).build()
            response = client.im.v1.image.create(request)
            if response.success():
                return response.data.image_key
            logger.error("upload image failed: %s, %s", response.code, response.msg)
    except Exception as e:
        logger.error("upload image failed %s: %s", file_path, e)
    return None


def _upload_file_sync(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    file_type = _FILE_TYPE_MAP.get(ext, "stream")
    file_name = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            request = CreateFileRequest.builder().request_body(
                CreateFileRequestBody.builder().file_type(file_type).file_name(file_name).file(f).build()
            ).build()
            response = client.im.v1.file.create(request)
            if response.success():
                return response.data.file_key
            logger.error("upload file failed: %s, %s", response.code, response.msg)
    except Exception as e:
        logger.error("upload file failed %s: %s", file_path, e)
    return None


def _download_image_sync(message_id, image_key):
    try:
        request = GetMessageResourceRequest.builder().message_id(message_id).file_key(image_key).type("image").build()
        response = client.im.v1.message_resource.get(request)
        if response.success():
            data = response.file.read() if hasattr(response.file, "read") else response.file
            return data, response.file_name
        logger.error("download image failed: %s, %s", response.code, response.msg)
    except Exception as e:
        logger.error("download image failed %s: %s", image_key, e)
    return None, None


def _download_file_sync(message_id, file_key, resource_type="file"):
    if resource_type == "audio":
        resource_type = "file"
    try:
        request = GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(resource_type).build()
        response = client.im.v1.message_resource.get(request)
        if response.success():
            data = response.file.read() if hasattr(response.file, "read") else response.file
            return data, response.file_name
        logger.error("download %s failed: %s, %s", resource_type, response.code, response.msg)
    except Exception as e:
        logger.error("download %s failed %s: %s", resource_type, file_key, e)
    return None, None


def _download_and_save_media(msg_type, content_json, message_id):
    data, filename = None, None
    if msg_type == "image":
        image_key = content_json.get("image_key")
        if image_key and message_id:
            data, filename = _download_image_sync(message_id, image_key)
            if not filename:
                filename = f"{image_key[:16]}.jpg"
    elif msg_type in ("audio", "file", "media"):
        file_key = content_json.get("file_key")
        if file_key and message_id:
            data, filename = _download_file_sync(message_id, file_key, msg_type)
            if not filename:
                filename = file_key[:16]
            if msg_type == "audio" and filename and not filename.endswith(".opus"):
                filename = f"{filename}.opus"
    if data and filename:
        file_path = os.path.join(MEDIA_DIR, os.path.basename(filename))
        with open(file_path, "wb") as f:
            f.write(data)
        return file_path, filename
    return None, None


def _describe_media(msg_type, file_path, filename):
    if msg_type == "image":
        return f"[image: {filename}]\n[Image: source: {file_path}]"
    if msg_type == "audio":
        return f"[audio: {filename}]\n[File: source: {file_path}]"
    if msg_type in ("file", "media"):
        return f"[{msg_type}: {filename}]\n[File: source: {file_path}]"
    return f"[{msg_type}]\n[File: source: {file_path}]"


def _send_local_file(receive_id, file_path, receive_id_type="open_id"):
    if not os.path.isfile(file_path):
        send_message(receive_id, f"⚠️ 文件不存在: {file_path}", receive_id_type=receive_id_type)
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _IMAGE_EXTS:
        image_key = _upload_image_sync(file_path)
        if image_key:
            send_message(receive_id, json.dumps({"image_key": image_key}, ensure_ascii=False), msg_type="image", receive_id_type=receive_id_type)
            return True
    else:
        file_key = _upload_file_sync(file_path)
        if file_key:
            msg_type = "media" if ext in _AUDIO_EXTS or ext in _VIDEO_EXTS else "file"
            send_message(receive_id, json.dumps({"file_key": file_key}, ensure_ascii=False), msg_type=msg_type, receive_id_type=receive_id_type)
            return True
    send_message(receive_id, f"⚠️ 文件发送失败: {os.path.basename(file_path)}", receive_id_type=receive_id_type)
    return False


def _send_generated_files(receive_id, raw_text, receive_id_type="open_id"):
    for file_path in _extract_files(raw_text):
        _send_local_file(receive_id, file_path, receive_id_type)


def _build_user_message(message):
    msg_type = message.message_type
    message_id = message.message_id
    content_json = _parse_json(message.content)
    parts, image_paths = [], []
    if msg_type == "text":
        text = str(content_json.get("text", "") or "").strip()
        if text:
            parts.append(text)
    elif msg_type == "post":
        text, image_keys = _extract_post_content(content_json)
        if text:
            parts.append(text)
        for image_key in image_keys:
            file_path, filename = _download_and_save_media("image", {"image_key": image_key}, message_id)
            if file_path and filename:
                parts.append(_describe_media("image", file_path, filename))
                image_paths.append(file_path)
            else:
                parts.append("[image: download failed]")
    elif msg_type in ("image", "audio", "file", "media"):
        file_path, filename = _download_and_save_media(msg_type, content_json, message_id)
        if file_path and filename:
            parts.append(_describe_media(msg_type, file_path, filename))
            if msg_type == "image":
                image_paths.append(file_path)
        else:
            parts.append(f"[{msg_type}: download failed]")
    elif msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
        parts.append(_extract_share_card_content(content_json, msg_type))
    else:
        parts.append(_MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))
    return "\n".join([p for p in parts if p]).strip(), image_paths


def _fmt_tool_call(tc):
    name = tc.get('tool_name', '?')
    args = {k: v for k, v in (tc.get('args') or {}).items() if not k.startswith('_')}
    return f"- `{name}`({json.dumps(args, ensure_ascii=False)[:_LOG_TEXT_PREVIEW]})"


def _render_diff(old_text, new_text, file_path=""):
    """生成 unified diff 文本（方案E：无行数上限，由飞书卡片自身限制处理超限）。"""
    if not old_text and not new_text:
        return None
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines,
                                      fromfile=f'a/{file_path or "old"}',
                                      tofile=f'b/{file_path or "new"}',
                                      n=3))
    if not diff:
        return None
    return "".join(diff)


def _build_step_detail(resp, tool_calls):
    """从 LLM response + tool_calls 组装单步展开详情（纯函数）。
    方案E: file_patch 步骤注入 diff 可视化"""
    parts = []
    thinking = (getattr(resp, 'thinking', '') or '').strip() if resp else ''
    if thinking:
        parts.append(f"### 💭 Thinking\n{thinking}")

    # 方案E: file_patch 的 diff 可视化
    if tool_calls:
        for tc in tool_calls:
            if tc.get('tool_name') == 'file_patch':
                args = tc.get('args', {})
                old_c = args.get('old_content', '')
                new_c = args.get('new_content', '')
                path = args.get('path', '')
                if old_c or new_c:
                    diff_text = _render_diff(old_c, new_c, path)
                    if diff_text:
                        parts.append("### 🔍 Diff\n```diff\n" + diff_text + "\n```")

    # 原有: tool calls 列表
    if tool_calls:
        parts.append("### 🛠 Tool Calls\n" + "\n".join(_fmt_tool_call(tc) for tc in tool_calls))

    content = _display_text((getattr(resp, 'content', '') or '')).strip() if resp else ''
    if content:
        parts.append(f"### 📝 Output\n{content}")
    return "\n\n".join(parts)


class _TaskCard:
    """飞书任务卡片：单卡片持续 patch；每步一个独立折叠面板（header 显示 summary，展开看详情）。"""
    _DETAIL_LIMIT = 10000
    _TRUNC_SUFFIX = "\n\n_... (内容较长，此处折叠展示)_"

    # 步骤类型 → 状态 emoji 映射（方案C: 进度感知 + 轮转 emoji）
    # 步骤工具 → 状态 emoji 映射（方案C: 进度感知 + 轮转 emoji）
    # key 与 _TOOL_SUMMARY_MAP 保持一致，仅含 7 个实际工具 + _default
    _STATUS_EMOJI_MAP = {
        "file_read":                 "📖",
        "file_write":                "📝",
        "file_patch":                "✏️",
        "code_run":                  "⚡",
        "ask_user":                  "💬",
        "update_working_checkpoint": "📝",
        "start_long_term_update":    "💾",
        "_default":                  "⏳",
    }

    def __init__(self, receive_id, rid_type):
        self.rid, self.rtype = receive_id, rid_type
        self.steps = []          # [(summary, detail), ...]
        self.step_tools = []     # 记录每步的 tool_name，用于轮转 emoji（方案C）
        self.status = "🤔 思考中..."
        self.final = None
        self.msg_id = None
        self.start_fallback_sent = False
        self.final_fallback_sent = False
        self._start_ts = time.time()       # 方案G: 自计时起始时间
        self.elapsed_seconds = None        # 方案G: 完成时计算耗时

    def _step_panel(self, idx, summary, detail):
        """方案B: 友好截断阈值+措辞 + 方案C: 进度前缀+状态emoji+当前步展开 + 方案I: Turn→步骤"""
        detail = detail or "_(无输出)_"
        if len(detail) > self._DETAIL_LIMIT:
            detail = detail[:self._DETAIL_LIMIT] + self._TRUNC_SUFFIX

        is_current = (idx == len(self.steps)) and not self.final
        if is_current:
            tool_name = self.step_tools[-1] if self.step_tools else None
            prefix = self._STATUS_EMOJI_MAP.get(tool_name, self._STATUS_EMOJI_MAP["_default"])
        else:
            prefix = "✅"

        header_text = f"{prefix} 步骤 {idx} · {summary}"
        return {
            "tag": "collapsible_panel",
            "expanded": is_current,
            "header": {"title": {"tag": "plain_text", "content": header_text}},
            "elements": [{"tag": "markdown", "content": detail}],
        }

    def _build(self):
        """方案C: 顶部状态栏 + 方案D: 最终输出结构化为 📋 结果 + 耗时"""
        step_count = len(self.steps)
        if self.final:
            status_line = f"**✅ 已完成** (共 {step_count} 步)"
        elif step_count > 0:
            tool_name = self.step_tools[-1] if self.step_tools else None
            emoji = self._STATUS_EMOJI_MAP.get(tool_name, self._STATUS_EMOJI_MAP["_default"])
            status_line = f"**{emoji} 步骤 {step_count}**"
        else:
            status_line = f"**{self.status}**"
        els = [{"tag": "markdown", "content": status_line}]

        for i, (s, d) in enumerate(self.steps, 1):
            els.append(self._step_panel(i, s, d))

        # 方案D: 最终输出结构化
        if self.final:
            final_display = self.final
            meta_line = ""
            if self.elapsed_seconds is not None:
                meta_line = f"\n\n---\n*⏱ {self.elapsed_seconds:.1f}s*"
            els += [
                {"tag": "hr"},
                {"tag": "markdown", "content": f"### 📋 结果\n\n{final_display}{meta_line}"},
            ]
        return _card_raw(els)

    def _push(self):
        card = self._build()
        if self.msg_id:
            ok = _patch_card(self.msg_id, card)
        else:
            self.msg_id = _send_raw(self.rid, card, "interactive", self.rtype)
            ok = bool(self.msg_id)
        return ok

    def _fallback_text(self, text, *, final=False):
        attr = "final_fallback_sent" if final else "start_fallback_sent"
        if getattr(self, attr):
            return
        setattr(self, attr, True)
        send_message(self.rid, text, receive_id_type=self.rtype)

    # ── 公开接口 ──

    def start(self):
        if not self._push():
            self._fallback_text("🤔 思考中...")

    def step(self, summary, detail="", tool_name=None):
        """方案C: 跟踪 tool_name 用于轮转 emoji + 方案I: Turn→步骤"""
        self.steps.append((summary, detail))
        self.step_tools.append(tool_name or "_default")
        total = len(self.steps)
        current_emoji = self._STATUS_EMOJI_MAP.get(tool_name, self._STATUS_EMOJI_MAP["_default"])
        self.status = f"{current_emoji} 步骤 {total}"
        self._push()

    def done(self, text):
        """方案G: 自计时耗时计算"""
        self.elapsed_seconds = time.time() - self._start_ts
        self.status = f"✅ 已完成 · 耗时 {self.elapsed_seconds:.1f}s"
        self.final = text or "_(无文本输出)_"
        if not self._push():
            self._fallback_text(_display_text(text), final=True)

    def fail(self, msg):
        self.status = f"❌ {msg}"
        if not self._push():
            self._fallback_text(f"❌ {msg}", final=True)


# 方案A: 工具摘要人性化映射表（已对照 assets/tools_schema.json 核实 7 个 GA 实际工具）
_TOOL_SUMMARY_MAP = {
    "file_read":                 "📖 读取文件",
    "file_write":                "📝 写入文件",
    "file_patch":                "✏️ 修改文件",
    "code_run":                  "⚡ 执行代码",
    "ask_user":                  "💬 询问用户",
    "update_working_checkpoint": "📝 更新工作记忆",
    "start_long_term_update":    "💾 更新长期记忆",
}

def _make_task_hook(card, task_id, on_final):
    """飞书任务 hook：每轮 patch 卡片状态；结束触发 on_final(raw) 处理附件。
    改造点: 方案A(摘要人性化) + 方案G(自计时耗时)"""
    def hook(ctx):
        try:
            parent = getattr(ctx.get("self"), "parent", None)
            if getattr(parent, "_fs_active_task_id", None) != task_id:
                return

            if ctx.get('exit_reason'):
                # 方案G: 任务结束，done() 自动计算耗时
                resp = ctx.get('response')
                raw = resp.content if hasattr(resp, 'content') else str(resp)
                on_final(raw)

            elif ctx.get('summary'):
                # 方案A: 摘要人性化
                summary = ctx['summary']
                tool_calls = ctx.get('tool_calls') or []
                tool_name = tool_calls[0].get('tool_name') if tool_calls else None

                # 检测是否为 raw 工具调用摘要（ga_android.py 降级生成的"调用工具xxx, args: ..."）
                if summary.startswith("调用工具"):
                    if tool_name == 'no_tool' or not tool_name:
                        summary = "💭 思考分析"
                    else:
                        summary = _TOOL_SUMMARY_MAP.get(tool_name, f"🔧 执行{tool_name}")

                detail = _build_step_detail(ctx.get('response'), tool_calls)
                card.step(summary, detail, tool_name=tool_name)

        except Exception as e:
            logger.error("[fs hook] error: %s", e)
    return hook


class FeishuApp(AgentChatMixin):
    label, source, split_limit = "Feishu", "feishu", _TEXT_SPLIT_LIMIT
    card_split_limit = _CARD_SPLIT_LIMIT  # 单张卡片 markdown 元素字符上限，超出则分片发送

    async def handle_command(self, chat_key, user_input, *, receive_id=None, receive_id_type="open_id", **_):
        """命令分发: 内置命令(/help /stop /status /clear)走原逻辑;
        飞书业务域命令(/日历 /日程 /群聊 /消息 /文档 /文件 /权限 /帮助)走 feishu_api(卡片)。"""
        rid = receive_id or chat_key
        stripped = (user_input or "").strip()
        cmd_head = stripped.split()[0].lower() if stripped else ""
        # 内置命令优先 → 父类
        if cmd_head in getattr(self, "_command_handlers", {}):
            return await super().handle_command(chat_key, user_input,
                                                receive_id=rid, receive_id_type=receive_id_type)
        # 飞书业务域命令
        if getattr(self, "_feishu_router", None) is not None:
            try:
                from feishu_api import dispatch_command
                handled, reply = dispatch_command(self, user_input)
                if handled and reply:
                    await self.send_card(chat_key, reply,
                                         receive_id=rid, receive_id_type=receive_id_type)
                    return
            except Exception as e:
                logger.error("[feishu_api] dispatch 异常: %s", e)
                await self.send_card(chat_key, f"❌ 命令处理异常: {e}",
                                     receive_id=rid, receive_id_type=receive_id_type)
                return
        # 兜底: 未知命令 → 父类
        return await super().handle_command(chat_key, user_input,
                                            receive_id=rid, receive_id_type=receive_id_type)

    async def send_text(self, chat_id, content, *, receive_id=None, receive_id_type="open_id", **_):
        rid = receive_id or chat_id
        for part in split_text(content, self.split_limit):
            await asyncio.to_thread(send_message, rid, part, "text", False, receive_id_type)

    async def send_card(self, chat_id, content, *, receive_id=None, receive_id_type="open_id", **_):
        """飞书业务域回复: 以 interactive 卡片(markdown) 发送，长文本自动分片。"""
        rid = receive_id or chat_id
        for part in split_text(content, self.card_split_limit):
            await asyncio.to_thread(send_message, rid, part, use_card=True, receive_id_type=receive_id_type)

    async def send_done(self, chat_id, raw_text, *, receive_id=None, receive_id_type="open_id", **_):
        rid = receive_id or chat_id
        text = _display_text(raw_text)
        await asyncio.to_thread(send_message, rid, text, "text", False, receive_id_type)
        await asyncio.to_thread(_send_generated_files, rid, raw_text, receive_id_type)

    async def run_agent(self, chat_id, text, *, receive_id=None, receive_id_type="open_id", images=None, **_):
        if self.user_tasks:
            await self.send_text(chat_id, "当前会话已有任务在运行，请等待完成或发送 /stop 后再试。", receive_id=receive_id, receive_id_type=receive_id_type)
            return
        state = {"running": True}
        self.user_tasks[chat_id] = state
        rid = receive_id or chat_id
        task_id = f"{chat_id}_{uuid.uuid4().hex}"
        hook_key = f"fs_{task_id}"
        card = _TaskCard(rid, receive_id_type)
        result = {"raw": None, "sent": False}
        finish_lock = threading.Lock()

        def _finish(raw):
            with finish_lock:
                if result["sent"]:
                    return
                result["raw"] = raw
                result["sent"] = True
            card.done(_display_text(raw))
            _send_generated_files(rid, raw, receive_id_type=receive_id_type)

        # 注入当前飞书会话上下文，让 LLM 知道"本群"指哪个群。
        # 查群名失败时会优雅降级为"当前群聊"，不会把原始 chat_id 暴露给用户。
        old_chat_id_env, old_rid_type_env = set_context_env(rid, receive_id_type)
        text = build_context_prompt(rid, receive_id_type, text)

        # 同时追加到 system prompt，作为最高优先级规则让 LLM 遵守。
        backend = getattr(getattr(self.agent, "llmclient", None), "backend", None)
        old_extra_sys_prompt = getattr(backend, "extra_sys_prompt", "") if backend else ""
        if backend:
            backend.extra_sys_prompt = (old_extra_sys_prompt or "") + build_extra_sys_prompt(rid, receive_id_type)

        try:
            await asyncio.to_thread(card.start)
            if not hasattr(self.agent, '_turn_end_hooks'):
                self.agent._turn_end_hooks = {}
            self.agent._turn_end_hooks[hook_key] = _make_task_hook(card, task_id, _finish)
            self.agent._fs_active_task_id = task_id
            dq = self.agent.put_task(text, source=self.source, images=images or None)
            start = time.time()
            while state["running"] and not result["sent"]:
                try:
                    item = await asyncio.to_thread(dq.get, True, 1)
                except Q.Empty:
                    item = None
                if item and "done" in item:
                    await asyncio.to_thread(_finish, item.get("done", ""))
                    break
                if time.time() - start > AGENT_TIMEOUT_SEC:
                    self.agent.abort()
                    await asyncio.to_thread(card.fail, "任务超时")
                    break
            if not state["running"] and not result["sent"]:
                self.agent.abort()
                await asyncio.to_thread(card.fail, "已停止")
        except Exception as e:
            logger.exception("run_agent 执行异常")
            await asyncio.to_thread(card.fail, f"错误: {e}")
        finally:
            # 清理飞书会话上下文环境变量，避免影响后续任务。
            clear_context_env(old_chat_id_env, old_rid_type_env)
            # 恢复 system prompt 追加内容。
            if backend:
                try:
                    backend.extra_sys_prompt = old_extra_sys_prompt
                except AttributeError:
                    pass
            if getattr(self.agent, "_fs_active_task_id", None) == task_id:
                try:
                    delattr(self.agent, "_fs_active_task_id")
                except AttributeError:
                    pass
            if hasattr(self.agent, '_turn_end_hooks'):
                self.agent._turn_end_hooks.pop(hook_key, None)
            self.user_tasks.pop(chat_id, None)


def get_app():
    global app
    if app is None:
        app = FeishuApp(get_agent(), user_tasks)
    # 确保业务域命令已注册（即使 app 已存在但之前注册失败）
    _feishu_router = getattr(app, '_feishu_router', None)
    if _feishu_router is None:
        try:
            from feishu_api import register_all_commands
            register_all_commands(app)
            logger.info("[feishu_api] 业务域命令已注册: %s", getattr(app, '_feishu_domains', []))
        except Exception as e:
            logger.error("[feishu_api] 命令注册失败(不影响主流程): %s", e, exc_info=True)
    return app


def _run_async(coro):
    try:
        asyncio.run(coro)
    except Exception:
        logger.exception("_run_async 执行异常")


def handle_message_recalled(data):
    """处理消息撤回事件（飞书用户撤回消息时触发，仅记录日志，不做处理）"""
    try:
        event = getattr(data, "event", None)
        message_id = getattr(event, "message_id", "?") if event else "?"
        operator_id = "?"
        if event:
            operator = getattr(event, "operator", None)
            if operator:
                operator_id = getattr(operator, "operator_id", "?")
                if hasattr(operator_id, "open_id"):
                    operator_id = operator_id.open_id
        logger.info("[飞书] 消息已撤回: message_id=%s, operator=%s", message_id, operator_id)
    except Exception as e:
        logger.error("[飞书] 处理撤回事件异常: %s", e)


# ===== 群聊 owner 准入控制 =====
# owner 绑定持久化文件（独立于密钥文件 mykey.json，仅存 owner open_id）
_OWNER_CONFIG_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "fs_owner.json")
_bot_open_id_cache = {"value": None}


_tenant_token_cache = {"value": None, "expires": 0}


def _get_tenant_access_token():
    """获取 tenant_access_token，带 5400 秒缓存。失败返回 None。

    复用 mykey.json 的 APP_ID/APP_SECRET，不依赖 lark_native 的凭证链。
    """
    now = time.time()
    if _tenant_token_cache["value"] and _tenant_token_cache["expires"] > now + 60:
        return _tenant_token_cache["value"]
    try:
        import urllib.request
        import json as _json
        token_body = _json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode("utf-8")
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=token_body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, timeout=15) as r:
            data = _json.loads(r.read()) or {}
        token = data.get("tenant_access_token")
        expire = data.get("expire", 7200)
        if token:
            _tenant_token_cache["value"] = token
            _tenant_token_cache["expires"] = now + expire
            return token
    except Exception as e:
        logger.warning("获取 tenant_access_token 失败: %s", e)
    return _tenant_token_cache["value"]


def _get_bot_open_id():
    """获取机器人自身 open_id（用于判断群聊是否被@），模块级缓存。失败返回 None。

    复用 mykey.json 的 APP_ID/APP_SECRET 取 tenant_access_token，直接调
    /bot/v3/info/，不再依赖 lark_native（其 _CRED 凭证链在 lark_bot 部署下
    未初始化，会导致永久返回 None → _is_at_bot 恒 False → 群@机器人无响应）。
    """
    if _bot_open_id_cache.get("value"):
        return _bot_open_id_cache.get("value")
    token = _get_tenant_access_token()
    if not token:
        return _bot_open_id_cache.get("value")
    try:
        import urllib.request
        import json as _json
        info_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/bot/v3/info/",
            headers={"Authorization": "Bearer " + token},
            method="GET",
        )
        with urllib.request.urlopen(info_req, timeout=15) as r:
            oid = ((_json.loads(r.read()) or {}).get("bot") or {}).get("open_id")
        if oid:
            _bot_open_id_cache["value"] = oid
    except Exception as e:
        logger.warning("获取机器人 open_id 失败: %s", e)
    return _bot_open_id_cache.get("value")


def _get_chat_id_by_message_id(message_id):
    """通过 message_id 反查消息详情，返回 chat_id。失败返回空字符串。

    当飞书事件推送中 message.chat_id 为空时，用此接口兜底获取真实 chat_id。
    优先用 urllib 直接调飞书 API；若失败则回退到 lark_native.api()。
    """
    if not message_id:
        return ""
    token = _get_tenant_access_token()
    if not token:
        return ""
    try:
        import urllib.request
        import urllib.parse
        import json as _json
        encoded_id = urllib.parse.quote(message_id, safe="")
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{encoded_id}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer " + token},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read()) or {}
        if data.get("code") != 0:
            logger.warning("反查消息详情失败: %s %s", data.get("code"), data.get("msg"))
            return ""
        items = (data.get("data") or {}).get("items") or []
        if items:
            return (items[0] or {}).get("chat_id", "")
    except Exception as e:
        logger.warning("urllib 反查消息详情异常: %s", e)

    # 兜底：尝试 lark_native.api()（GA 主环境若已初始化凭证链则可工作）
    try:
        import lark_native
        data = lark_native.api("GET", f"/im/v1/messages/{message_id}")
        items = ((data or {}).get("data") or {}).get("items") or []
        if items:
            return (items[0] or {}).get("chat_id", "")
    except Exception as e:
        logger.warning("lark_native 反查消息详情异常: %s", e)
    return ""


def _extract_raw_chat_id(data):
    """从 SDK 事件对象的原始 __dict__ 中尝试提取 chat_id。"""
    try:
        raw = getattr(data, "__dict__", None) or {}
        event_raw = raw.get("event")
        if event_raw is None:
            return None
        if hasattr(event_raw, "__dict__"):
            event_raw = event_raw.__dict__
        msg_raw = event_raw.get("message")
        if msg_raw is None:
            return None
        if hasattr(msg_raw, "__dict__"):
            msg_raw = msg_raw.__dict__
        return msg_raw.get("chat_id") or None
    except Exception:
        return None


def _reply_to_message(message_id, content, msg_type="text"):
    """通过 message_id 回复消息到原会话（群聊/私聊均可）。

    不需要 chat_id，用于 chat_id 丢失时的兜底回复/调试。
    """
    if not message_id:
        return False
    token = _get_tenant_access_token()
    if not token:
        return False
    try:
        import urllib.request
        import urllib.parse
        import json as _json
        encoded_id = urllib.parse.quote(message_id, safe="")
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{encoded_id}/reply"
        body = _json.dumps({
            "content": _json.dumps({"text": content}),
            "msg_type": msg_type,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read()) or {}
        return data.get("code") == 0
    except Exception as e:
        logger.warning("回复消息失败: %s", e)
    return False


def _is_at_bot(mentions):
    """判断消息是否 @ 了机器人。mentions 为飞书 Mention 列表。"""
    if not mentions:
        return False
    bot_id = _get_bot_open_id()
    if not bot_id:
        return False
    for m in mentions:
        uid = getattr(m, "id", None)
        if uid and getattr(uid, "open_id", None) == bot_id:
            return True
    return False


def _get_owner_open_id():
    """读取已绑定的 owner open_id，未绑定返回空串。"""
    try:
        if os.path.isfile(_OWNER_CONFIG_PATH):
            with open(_OWNER_CONFIG_PATH, "r", encoding="utf-8") as f:
                return str((json.load(f) or {}).get("owner_open_id", "") or "").strip()
    except Exception as e:
        logger.warning("读取 owner 配置失败: %s", e)
    return ""


def _bind_owner(open_id):
    """绑定 owner open_id 并持久化，返回是否成功。"""
    try:
        os.makedirs(os.path.dirname(_OWNER_CONFIG_PATH), exist_ok=True)
        with open(_OWNER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"owner_open_id": open_id, "bind_time": int(time.time())}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error("绑定 owner 失败: %s", e)
        return False


def _strip_at_placeholders(text):
    """清理飞书文本中 @某人 的占位符（@_user_N）。"""
    if not text:
        return text
    return re.sub(r"@_user_\d+\s*", "", text).strip()


def handle_message(data):
    event, message, sender = data.event, data.event.message, data.event.sender
    message_id = getattr(message, "message_id", "") or ""
    if not _claim_message_once(message_id):
        logger.debug("忽略重复飞书消息: %s", message_id)
        return
    open_id = sender.sender_id.open_id
    # 飞书事件原始数据：message.chat_id 在群聊中可能为空（SDK 解析问题）。
    # 依次尝试：1) SDK 标准字段；2) sender.chat_id；3) 事件对象原始 __dict__；
    # 4) 通过 message_id 调飞书 API 反查消息详情。
    chat_id = getattr(message, "chat_id", None) or ""
    if not chat_id:
        chat_id = getattr(getattr(event, "sender", None), "chat_id", None) or ""
    if not chat_id:
        raw_cid = _extract_raw_chat_id(data)
        chat_id = raw_cid or ""
    if not chat_id and message_id:
        chat_id = _get_chat_id_by_message_id(message_id)
    chat_type = getattr(message, "chat_type", "") or ""
    logger.info(
        "[DEBUG] chat_id=%s chat_type=%s open_id=%s message_id=%s",
        chat_id, chat_type, open_id, message_id,
    )
    if not PUBLIC_ACCESS and open_id not in ALLOWED_USERS:
        logger.warning("未授权用户: %s", open_id)
        return
    # === 群聊准入控制：仅 owner @机器人才响应；首次@机器人自动绑定 owner ===
    if chat_type == "group":
        mentions = getattr(message, "mentions", None) or []
        at_bot = _is_at_bot(mentions)
        owner = _get_owner_open_id()
        if not owner:
            if at_bot:
                if _bind_owner(open_id):
                    send_message(chat_id, "✅ 你已成为机器人所有者(owner)，open_id 已绑定。此后群内仅你 @我 时我才会响应。", receive_id_type="chat_id")
                else:
                    send_message(chat_id, "⚠️ owner 绑定失败，请稍后重试。", receive_id_type="chat_id")
            return
        if not (open_id == owner and at_bot):
            return
    user_input, image_paths = _build_user_message(message)
    if chat_type == "group" and message.message_type == "text":
        user_input = _strip_at_placeholders(user_input)
    if not user_input:
        if chat_type == "group":
            return
        if chat_id:
            send_message(chat_id, f"⚠️ 暂不支持处理此类飞书消息：{message.message_type}", receive_id_type="chat_id")
        else:
            send_message(open_id, f"⚠️ 暂不支持处理此类飞书消息：{message.message_type}")
        return
    logger.debug("收到消息 [%s] (%s, %s images): %s", open_id, message.message_type, len(image_paths), user_input[:_LOG_TEXT_PREVIEW])
    receive_id = chat_id or open_id
    receive_id_type = "chat_id" if chat_id else "open_id"
    chat_key = receive_id
    if message.message_type == "text" and user_input.startswith("/"):
        threading.Thread(
            target=_run_async,
            args=(get_app().handle_command(chat_key, user_input, receive_id=receive_id, receive_id_type=receive_id_type),),
            daemon=True,
        ).start()
        return
    threading.Thread(
        target=_run_async,
        args=(get_app().run_agent(chat_key, user_input, receive_id=receive_id, receive_id_type=receive_id_type, images=image_paths),),
        daemon=True,
    ).start()


# 停止标志 - 外部可通过 fsapp.shutdown_flag.set() 来关闭机器人
shutdown_flag = threading.Event()


def _run_client_with_shutdown_check(cli):
    """运行 WS 客户端，select 阶段每秒检查 shutdown_flag 以实现可中断阻塞。

    替代 cli.start() 中不可中断的 loop.run_until_complete(_select())。"""
    loop = asyncio.get_event_loop()

    # 阶段1: 连接（复用 cli.start() 的重连逻辑）
    try:
        loop.run_until_complete(cli._connect())
    except Exception:
        # 尝试断开 + 自动重连（同 cli.start() 行为）
        try:
            loop.run_until_complete(cli._disconnect())
        except Exception:
            pass
        if getattr(cli, '_auto_reconnect', True):
            try:
                loop.run_until_complete(cli._reconnect())
            except Exception:
                raise
        else:
            raise

    # 阶段2: 启动心跳
    loop.create_task(cli._ping_loop())

    # 阶段3: 可中断的 select（每秒检查 shutdown_flag）
    async def _shutdown_aware_select():
        while not shutdown_flag.is_set():
            await asyncio.sleep(1)

    loop.run_until_complete(_shutdown_aware_select())


def main():
    global client, APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, CONFIG_PATH
    APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, CONFIG_PATH = _feishu_config()
    if not APP_ID or not APP_SECRET:
        logger.error("请在 mykey 配置中填写 fs_app_id 和 fs_app_secret\n配置文件: %s", CONFIG_PATH)
        sys.exit(1)
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(handle_message)
               .register_p1_customized_event("im.message.recalled_v1", handle_message_recalled)
               .register_p2_im_message_recalled_v1(handle_message_recalled)
               .build())
    retry_delay = _RETRY_INIT_DELAY
    while not shutdown_flag.is_set():
        try:
            client = create_client()
            cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
            logger.info("%s\n飞书 Agent 已启动（长连接模式）\nApp ID: %s\n配置: %s\n等待消息...\n%s", "=" * _BANNER_SEP_LEN, APP_ID, CONFIG_PATH, "=" * _BANNER_SEP_LEN)
            _run_client_with_shutdown_check(cli)
            retry_delay = _RETRY_INIT_DELAY
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if shutdown_flag.is_set():
                break
            logger.warning("飞书长连接断开或启动失败: %s", e, exc_info=True)
        if shutdown_flag.is_set():
            break
        logger.info("%ss 后重连飞书长连接...", retry_delay)
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, _RETRY_MAX_DELAY)
    logger.info("飞书机器人已正常停止")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="A3Agent Feishu frontend")
    parser.add_argument("--check", action="store_true", help="只检查飞书配置，不启动长连接")
    parser.add_argument("--check-agent", action="store_true", help="检查配置并初始化 Agent/LLM")
    args = parser.parse_args()
    if args.check or args.check_agent:
        print(json.dumps(check_config(init_agent=args.check_agent), ensure_ascii=False, indent=2), flush=True)
    else:
        main()
