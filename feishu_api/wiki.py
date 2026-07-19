# -*- coding: utf-8 -*-
"""知识库域 — wiki token 换 obj_token / 读知识库文档全文。

覆盖 lark_sop ③节: get_node 换 token + obj_type 分流。
wiki 链接 token ≠ file_token，必先 get_node 换 obj_token。
"""

from ._base import FeishuApiBase, FeishuApiError


class WikiAPI(FeishuApiBase):
    _domain_name = "wiki"
    _scopes = ["wiki:wiki", "wiki:wiki:readonly"]

    def get_node(self, token):
        """wiki token → obj_token + obj_type。
        返回 {node: {obj_token, obj_type, has_child, ...}}

        obj_type 分流:
          - docx  → 用 DocxAPI.get_text(obj_token) 读全文
          - bitable → obj_token 即 app_token，传给 BitableAPI
          - sheet  → 用 sheets 接口
        """
        return self._call("GET", "/wiki/v2/spaces/get_node",
                          params={"token": token})

    def resolve(self, token):
        """便捷方法: get_node 后直接返回 (obj_token, obj_type)。"""
        node = self.get_node(token).get("node", {})
        return node.get("obj_token"), node.get("obj_type")

    def get_doc_text(self, token):
        """一站式: wiki token → 换 obj_token → 读 docx 全文。
        仅适用于 obj_type == "docx" 的知识库文档。
        """
        obj_token, obj_type = self.resolve(token)
        if obj_type != "docx":
            raise FeishuApiError(
                f"该 wiki 节点类型为 {obj_type}，非 docx，无法用 get_doc_text。"
                f"bitable→用BitableAPI，sheet→用sheets接口。")
        from .docx import DocxAPI
        docx = DocxAPI(as_user=self.as_user)
        return docx.get_text(obj_token)

    def _probe_permission(self):
        """探测: 用无效 token 调 get_node，权限通则报参数错。"""
        try:
            self.get_node("probe")
        except FeishuApiError:
            pass
        return None
