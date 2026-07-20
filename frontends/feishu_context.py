"""飞书会话上下文注入工具。

为 FeishuApp.run_agent() 提供当前群聊/私聊上下文，让 LLM 知道"本群"指哪个会话。
不改 GA 核心，所有逻辑限定在 lark_bot 内。
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_ENV_CHAT_ID = "FEISHU_CURRENT_CHAT_ID"
_ENV_RID_TYPE = "FEISHU_RECEIVE_ID_TYPE"
_CACHE_TTL_SEC = 300
_CACHE_FILE_NAME = "chat_name_cache.json"


def _cache_path():
    """缓存文件放在 lark_bot/data/ 下，避免放 temp/（易被清理）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, _CACHE_FILE_NAME)


def _load_cache():
    try:
        path = _cache_path()
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        logger.warning("读取群名缓存失败: %s", e)
    return {}


def _save_cache(cache):
    try:
        path = _cache_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存群名缓存失败: %s", e)


def _get_chat_name_from_cache(chat_id, allow_stale=True):
    now = time.time()
    cache = _load_cache()
    item = cache.get(chat_id)
    if not item:
        return None
    name = item.get("name")
    expire = item.get("expire", 0)
    if now < expire:
        return name
    if allow_stale:
        logger.info("使用过期的群名缓存: chat_id=%s, name=%s", chat_id, name)
        return name
    return None


def _save_chat_name_to_cache(chat_id, name, ttl_sec=_CACHE_TTL_SEC):
    cache = _load_cache()
    cache[chat_id] = {"name": name, "expire": time.time() + ttl_sec}
    _save_cache(cache)


def get_chat_name(chat_id):
    """查询群名。优先读缓存（含过期缓存兜底），未命中则调飞书 API。

    返回值：
        - str: 群名
        - None: 查不到（API 失败、群不存在、无权限等）

    本函数永不抛异常，调用方可安全使用。
    """
    # 1. 优先缓存（允许过期缓存兜底，避免 API 失败时直接降级为 ID）
    cached = _get_chat_name_from_cache(chat_id, allow_stale=True)
    if cached:
        return cached

    # 2. 调飞书 API 精确查询
    try:
        import lark_native

        data = lark_native.api("GET", f"/im/v1/chats/{chat_id}") or {}
        # lark_native.api 已剥离外层 data 包裹，群名在顶层 "name" 字段。
        # 兼容：若返回结构仍带 data 包裹（旧版/其他接口），则再取一层。
        name = data.get("name") or ((data.get("data") or {}).get("name") or "")
        if name:
            _save_chat_name_to_cache(chat_id, name)
            return name
    except Exception as e:
        logger.warning("查群名失败(chat_id=%s): %s", chat_id, e)

    return None


def build_context_prompt(chat_id, receive_id_type, user_input):
    """构建带当前会话上下文的 prompt。

    原则：
      - 查到群名时显示群名，LLM 回复更自然。
      - 查不到群名时显示"当前群聊"，避免把原始 chat_id 暴露给用户。
      - 同时通过环境变量让 LLM 在 code_run 中可靠获取 chat_id。
      - 明确提示 LLM：回复用户时用"本群"/"当前群聊"指代，不要展示原始 ID。
    """
    if receive_id_type == "chat_id":
        name = get_chat_name(chat_id)
        group_label = name if name else "当前群聊"

        context = f"""========== 飞书会话上下文（必须遵守）==========
你当前正在飞书群聊中与用户对话。
当前群名：{group_label}
当前群ID：{chat_id}
"本群"、"当前群"、"这个群"均指上述群ID。
在 code_run 中可通过 os.environ.get('{_ENV_CHAT_ID}') 获取当前群ID。
向用户回复时，请用"本群"或"当前群聊"指代，禁止展示原始ID。
=============================================="""
    else:
        context = f"""========== 飞书会话上下文（必须遵守）==========
你当前正在飞书私聊中与用户对话。
当前用户ID：{chat_id}
=============================================="""

    return f"{context}\n\n用户输入：{user_input}"


def build_extra_sys_prompt(chat_id, receive_id_type):
    """生成追加到 system prompt 的飞书会话上下文规则。

    由于 agentmain.py 在构造 system prompt 时会读取
    self.llmclient.backend.extra_sys_prompt，我们可以把当前群上下文注入到
    system prompt 里，优先级高于 user message 中的普通上下文提示。
    """
    if receive_id_type == "chat_id":
        name = get_chat_name(chat_id)
        group_label = name if name else "当前群聊"
        return (
            f"\n[当前飞书会话上下文 - 最高优先级规则]\n"
            f"你当前正在飞书群聊“{group_label}”(ID: {chat_id})中与用户对话。\n"
            f"规则（必须遵守）：\n"
            f"1. 当用户说“本群”、“当前群”、“这个群”时，严格指上述群ID({chat_id})。\n"
            f"2. 执行任何涉及当前群的操作（如总结群聊、查询群成员、发送消息到当前群）时，必须使用上述群ID，禁止调用 list_chats 等工具去“猜测”当前群。\n"
            f"3. 在 code_run 中通过 os.environ['{_ENV_CHAT_ID}'] 获取当前群ID（必须用 [] 索引而非 .get()，确保环境变量存在再继续）。\n"
            f"4. 向用户回复时，用“本群”或“当前群聊”指代，禁止展示原始ID。\n"
            f"5. ⚠️ 调用 collect_ai_news / push_to_feishu 时，chat_id 必须显式传入 os.environ['{_ENV_CHAT_ID}']，禁止省略或传 None（省略/None 会静默失败跳过推送）。示例：collect_ai_news(time_window=\"day\", auto_push=True, chat_id=os.environ['{_ENV_CHAT_ID}'])\n"
            f"[/当前飞书会话上下文]\n"
        )
    return (
        f"\n[当前飞书会话上下文 - 最高优先级规则]\n"
        f"你当前正在飞书私聊中与用户对话。当前用户ID：{chat_id}\n"
        f"[/当前飞书会话上下文]\n"
    )


def set_context_env(chat_id, receive_id_type):
    """设置环境变量，返回旧值元组 (old_chat_id, old_rid_type)，用于任务结束后清理。"""
    old_chat_id = os.environ.get(_ENV_CHAT_ID)
    old_rid_type = os.environ.get(_ENV_RID_TYPE)
    os.environ[_ENV_CHAT_ID] = str(chat_id)
    os.environ[_ENV_RID_TYPE] = str(receive_id_type)
    return old_chat_id, old_rid_type


def clear_context_env(old_chat_id, old_rid_type):
    """恢复或删除环境变量。"""
    if old_chat_id is not None:
        os.environ[_ENV_CHAT_ID] = old_chat_id
    else:
        os.environ.pop(_ENV_CHAT_ID, None)

    if old_rid_type is not None:
        os.environ[_ENV_RID_TYPE] = old_rid_type
    else:
        os.environ.pop(_ENV_RID_TYPE, None)
