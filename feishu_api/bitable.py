# -*- coding: utf-8 -*-
"""多维表格域 — 记录增删改查 / 字段查询。

覆盖 lark_sop ②节全部高频操作: search/batch_create/update/batch_delete/fields。
封装了 SOP 中记录的坑: 串行写入间歇、批量≤500、字段类型预读。
"""

import time
from ._base import FeishuApiBase, FeishuApiError


class BitableAPI(FeishuApiBase):
    _domain_name = "bitable"
    _scopes = ["bitable:app", "bitable:app:readonly"]

    # ------------------------------------------------------------------
    # 字段查询（写前必读，别猜字段类型）
    # ------------------------------------------------------------------

    def list_fields(self, app_token, table_id, page_size=100):
        """列出多维表格字段。写前先调此方法读字段类型。
        返回 [{field_id, field_name, type, ...}, ...]
        """
        return self._paginate(
            "GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            params={"page_size": page_size}, items_key="items")

    def _probe_permission(self):
        """探测: 用一个不存在的 token 列字段，权限通则报参数错(非权限错)。"""
        try:
            self.list_fields("probe", "probe")
        except FeishuApiError:
            pass
        return None

    # ------------------------------------------------------------------
    # 记录查询
    # ------------------------------------------------------------------

    def search_records(self, app_token, table_id, filter_conditions=None,
                       field_names=None, page_size=100, max_pages=20):
        """搜索记录（支持过滤条件）。
        filter_conditions: [{"field_name":"状态","operator":"is","value":["done"]}, ...]
                          传 list 时自动包成 conjunction="and"。
        field_names: 只返回指定字段 ["字段1","字段2"]。
        返回 [{record_id, fields:{...}}, ...]
        """
        payload = {}
        if filter_conditions:
            if isinstance(filter_conditions, list):
                payload["filter"] = {
                    "conjunction": "and",
                    "conditions": filter_conditions}
            else:
                payload["filter"] = filter_conditions
        if field_names:
            payload["field_names"] = field_names

        return self._paginate(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            params={"page_size": page_size},
            payload=payload, items_key="items", max_pages=max_pages)

    def get_record(self, app_token, table_id, record_id):
        """获取单条记录。"""
        return self._call(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}")

    def list_records(self, app_token, table_id, page_size=100, max_pages=20):
        """列出全部记录（无过滤）。"""
        return self._paginate(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            params={"page_size": page_size},
            items_key="items", max_pages=max_pages)

    # ------------------------------------------------------------------
    # 记录写入（串行 + 间歇，避免 1254291 并发冲突）
    # ------------------------------------------------------------------

    def create_record(self, app_token, table_id, fields):
        """创建单条记录。fields: {"字段名": 值}。"""
        payload = {"fields": fields}
        return self._call(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            payload=payload)

    def batch_create(self, app_token, table_id, records_fields,
                     batch_size=500, interval=0.5):
        """批量创建记录。
        records_fields: [{"字段名":值}, ...]  ← 注意是 fields 列表，非完整 record。
        自动分批(≤500/批)，串行+间歇0.5s 避免 1254291 并发冲突。
        返回所有 record_id 列表。
        """
        all_ids = []
        total = len(records_fields)
        for i in range(0, total, batch_size):
            chunk = records_fields[i:i + batch_size]
            payload = {"records": [{"fields": f} for f in chunk]}
            result = self._call(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                payload=payload)
            for rec in result.get("records", []):
                all_ids.append(rec.get("record_id"))
            if i + batch_size < total:
                time.sleep(interval)
        return all_ids

    def update_record(self, app_token, table_id, record_id, fields):
        """更新单条记录。fields: {"字段名": 新值}。"""
        payload = {"fields": fields}
        return self._call(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            payload=payload)

    def batch_delete(self, app_token, table_id, record_ids,
                     batch_size=500, interval=0.5):
        """批量删除记录。record_ids: ["rec_xxx", ...]。
        自动分批，串行+间歇。
        """
        deleted = []
        total = len(record_ids)
        for i in range(0, total, batch_size):
            chunk = record_ids[i:i + batch_size]
            payload = {"records": chunk}
            result = self._call(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                payload=payload)
            for rec in result.get("records", []):
                deleted.append(rec.get("record_id"))
            if i + batch_size < total:
                time.sleep(interval)
        return deleted

    # ------------------------------------------------------------------
    # 表格管理
    # ------------------------------------------------------------------

    def list_tables(self, app_token, page_size=100):
        """列出多维表格中的所有数据表。"""
        return self._paginate(
            "GET", f"/bitable/v1/apps/{app_token}/tables",
            params={"page_size": page_size}, items_key="items")
