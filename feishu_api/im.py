# -*- coding: utf-8 -*-
"""即时通讯域 — 群聊列表 / 群消息 / 群内搜索 / 发消息。"""

import os
import json
import lark_native
from ._base import FeishuApiBase, FeishuApiError, _OPEN


class InstantMessagingAPI(FeishuApiBase):
    _domain_name = "im"
    _scopes = ["im:chat", "im:chat:readonly", "im:message",
               "im:message:send_as_bot", "im:resource"]

    # ------------------------------------------------------------------
    # 群聊列表
    # ------------------------------------------------------------------

    def list_chats(self, page_size=50):
        """列出机器人所在的群聊。

        实测结构: {items:[...], page_token, has_more}
        """
        return self._paginate("GET", "/im/v1/chats",
                              params={"page_size": page_size},
                              items_key="items")

    def _probe_permission(self):
        """探测: 列群聊。"""
        self.list_chats()
        return None

    # ------------------------------------------------------------------
    # 群消息
    # ------------------------------------------------------------------

    def get_messages(self, container_id, container_id_type="chat",
                     page_size=50, start_time=None, end_time=None,
                     sort_type="ByCreateTimeDesc"):
        """获取指定会话的消息列表。

        container_id: 群 chat_id 或用户 open_id（需 as_user=True）
        start_time/end_time: 毫秒时间戳(int)，可只传其一；
            ⚠️ 飞书要求 string 类型避免 JSON 精度丢失，此处自动转 str
        sort_type: "ByCreateTimeDesc"(默认) / "ByCreateTimeAsc"
        """
        params = {
            "container_id": container_id,
            "container_id_type": container_id_type,
            "page_size": page_size,
            "sort_type": sort_type,
        }
        if start_time is not None:
            params["start_time"] = str(start_time)
        if end_time is not None:
            params["end_time"] = str(end_time)
        return self._paginate("GET", "/im/v1/messages",
                              params=params, items_key="items")

    def search_in_chat(self, chat_id, keyword, page_size=50, max_msgs=200):
        """在指定群聊内按关键词过滤消息（客户端过滤）。

        飞书 bot 无全局消息搜索 API，故对群内消息做关键词子串匹配。
        返回匹配项 list。
        """
        msgs = self.get_messages(chat_id, page_size=page_size)
        # get_messages 已分页（默认最多 max_pages*page_size），客户端再过滤
        kw = (keyword or "").lower()
        matched = []
        for m in msgs:
            content = self._extract_text(m)
            if kw and kw in (content or "").lower():
                matched.append(m)
        return matched

    @staticmethod
    def _extract_text(message):
        """从消息体提取纯文本（容错）。"""
        try:
            body = message.get("body", {}) if isinstance(message, dict) else {}
            content_str = body.get("content", "{}") if isinstance(body, dict) else "{}"
            content = json.loads(content_str) if isinstance(content_str, str) else content_str
            if isinstance(content, dict):
                text = content.get("text")
                if text:
                    return text
                # 富文本/其他结构尝试取 title
                title = content.get("title")
                if isinstance(title, dict):
                    return title.get("content", "")
                return json.dumps(content, ensure_ascii=False)
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # 发消息
    # ------------------------------------------------------------------

    def send_text(self, receive_id, text, receive_id_type="chat_id"):
        """发送文本消息到群/用户。"""
        return self._send_msg(receive_id, "text", {"text": text},
                              receive_id_type)

    # ------------------------------------------------------------------
    # 发消息 — 卡片 / 图片 / 文件 / 富文本
    # ------------------------------------------------------------------

    def _send_msg(self, receive_id, msg_type, content, receive_id_type="chat_id"):
        """发送消息的内部统一入口。content 为 dict 时自动 json.dumps。"""
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content) if not isinstance(content, str) else content,
        }
        return self._call("POST", "/im/v1/messages",
                          params={"receive_id_type": receive_id_type},
                          payload=payload)

    def send_card(self, receive_id, card, receive_id_type="chat_id"):
        """发送交互卡片消息。
        card: dict 卡片结构（config/header/elements）。
        """
        return self._send_msg(receive_id, "interactive", card, receive_id_type)

    def send_image(self, receive_id, image_key, receive_id_type="chat_id"):
        """发送图片消息（需先 upload_image 获取 image_key）。"""
        return self._send_msg(receive_id, "image", {"image_key": image_key},
                              receive_id_type)

    def send_file(self, receive_id, file_key, receive_id_type="chat_id"):
        """发送文件消息（需先 upload_file 获取 file_key）。"""
        return self._send_msg(receive_id, "file", {"file_key": file_key},
                              receive_id_type)

    def send_audio(self, receive_id, file_key, receive_id_type="chat_id"):
        """发送语音消息（需先 upload_file 上传 opus 音频获取 file_key）。"""
        return self._send_msg(receive_id, "audio", {"file_key": file_key},
                              receive_id_type)

    def send_post(self, receive_id, post_content, receive_id_type="chat_id"):
        """发送富文本消息。
        post_content: dict，如
            {"zh_cn": {"title": "标题",
                       "content": [[{"tag":"text","text":"段落"}], ...]}}
        """
        return self._send_msg(receive_id, "post", post_content, receive_id_type)

    # ------------------------------------------------------------------
    # 上传 — 图片 / 文件到消息资源（直连 multipart）
    # ------------------------------------------------------------------
    # api() 只支持 JSON body，multipart 必须直连 requests + tenant_token()
    # （与 drive.py upload_file/download_file 同模式）

    def upload_image(self, file_path, dry_run=False):
        """上传本地图片到飞书消息资源。返回 image_key。
        直连 /im/v1/images，image_type="message"。
        """
        if dry_run:
            return {"_dry_run": True, "file_path": file_path}
        if not os.path.isfile(file_path):
            raise FeishuApiError(f"本地文件不存在: {file_path}")
        token = lark_native.tenant_token()
        url = _OPEN + "/im/v1/images"
        headers = {"Authorization": "Bearer " + token}
        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            r = lark_native.requests.post(
                url, headers=headers, timeout=60,
                files={"image_type": (None, "message"),
                       "image": (file_name, f, "image/png")})
        j = r.json()
        if j.get("code") != 0:
            raise FeishuApiError(f"图片上传失败: {j.get('msg')}",
                                 code=j.get("code"), raw=j)
        return j.get("data", {}).get("image_key", "")

    def upload_file(self, file_path, file_name=None, file_type="stream",
                    dry_run=False):
        """上传本地文件到飞书消息资源。返回 file_key。
        直连 /im/v1/files。
        ⚠ file_type: "stream"(普通文件) / "opus"(语音) / "mp4" / "pdf" 等；
           语音必须用 "opus"（飞书 audio 消息要求）。
        ⚠ file_name 用 (None, "显示名") 格式，否则飞书收到字段名而非文件名。
        """
        if dry_run:
            return {"_dry_run": True, "file_path": file_path,
                    "file_name": file_name, "file_type": file_type}
        if not os.path.isfile(file_path):
            raise FeishuApiError(f"本地文件不存在: {file_path}")
        token = lark_native.tenant_token()
        url = _OPEN + "/im/v1/files"
        headers = {"Authorization": "Bearer " + token}
        file_name = file_name or os.path.basename(file_path)
        with open(file_path, "rb") as f:
            r = lark_native.requests.post(
                url, headers=headers, timeout=60,
                files={"file_type": (None, file_type),
                       "file_name": (None, file_name),
                       "file": (file_name, f, "application/octet-stream")})
        j = r.json()
        if j.get("code") != 0:
            raise FeishuApiError(f"文件上传失败: {j.get('msg')}",
                                 code=j.get("code"), raw=j)
        return j.get("data", {}).get("file_key", "")

    # ------------------------------------------------------------------
    # 一键推送（封装 lark_push_sop 全流程 + 图片高度自适应分流）
    # ------------------------------------------------------------------

    def push_files(self, receive_id, file_paths=None, image_paths=None,
                   card=None, receive_id_type="chat_id", smart_image=True):
        """一键推送卡片+文件+图片到群聊（封装 lark_push_sop 全流程）。

        file_paths:  [本地路径...] → 上传后发 file 消息
        image_paths: [本地路径...] → 上传后发 image 消息
        card:        dict 卡片结构（可选，先发）
        smart_image: True 时图片>2000px 自动走文件消息（避免飞书 inline 裁切）
        返回 {"card":..., "files":[...], "images":[...]}
        """
        result = {"card": None, "files": [], "images": []}
        if card:
            result["card"] = self.send_card(receive_id, card, receive_id_type)
        for fp in (file_paths or []):
            fk = self.upload_file(fp)
            self.send_file(receive_id, fk, receive_id_type)
            result["files"].append(fk)
        for ip in (image_paths or []):
            if smart_image and self._image_height(ip) > 2000:
                # 超高图片走文件消息（避免飞书 inline 裁切顶部）
                fk = self.upload_file(ip)
                self.send_file(receive_id, fk, receive_id_type)
                result["images"].append(fk)
            else:
                ik = self.upload_image(ip)
                self.send_image(receive_id, ik, receive_id_type)
                result["images"].append(ik)
        return result

    @staticmethod
    def _image_height(file_path):
        """读取图片高度(px)，失败返回0。"""
        try:
            import cv2
            img = cv2.imread(file_path)
            if img is not None:
                return img.shape[0]
        except Exception:
            pass
        return 0
