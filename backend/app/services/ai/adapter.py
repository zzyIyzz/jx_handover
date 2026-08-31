"""AI 适配器。

AI 在本系统中的边界：可以识别、归并和改写，但不能创造事实，
也不能决定业务日期规则。所有 AI 输出必须带 source_ids 硬校验，
且 AI 永远没有发布权限。

AI_MODE=mock 时使用本地确定性模拟实现（不调用外部接口），
便于在无 Qwen API 时跑通完整链路。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app import config

logger = logging.getLogger(__name__)

# ---------- 共用：设备编号提取 ----------
_EQUIP_RE = re.compile(
    r"(#\s*\d+\s*SVG|F\d{2}|主变|箱变|集电[ⅠⅡⅢⅣI]+线|五防系统|AGC|功率预测|实训平台|视频监控)"
)


def extract_equipment(text: str) -> str:
    """提取设备/对象编号，用于合并硬规则。#1 SVG -> #1SVG。"""
    m = _EQUIP_RE.search(text or "")
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(1)).upper()


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s，。、；：,.:;（）()]", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class BaseAdapter:
    name = "base"

    def judge_merge(self, a: dict, b: dict) -> dict:
        """判断两条候选是否同一事项。返回
        {"same_item": bool, "confidence": float, "reason_code": str}"""
        raise NotImplementedError

    def summarize_cluster(self, title: str, updates: list[dict]) -> dict:
        """从同一事项时间线生成当前版本总结。
        updates: [{"date": ..., "text": ..., "status_hint": ...}]
        返回 {"summary": str, "latest_progress": str, "blocker": str,
              "next_action": str}"""
        raise NotImplementedError

    def enrich_preview_rows(self, rows: list[dict], context: dict) -> dict:
        """Return AI suggestions keyed by preview_key.

        Deterministic import parsing remains the source of dates, station and
        provenance.  AI may only refine the human-editable preview fields.
        """
        return {"items": [], "usage": {}, "model": self.name}


class MockAdapter(BaseAdapter):
    """确定性模拟：用与真实 AI 相同的保守策略。"""

    name = "mock"

    def judge_merge(self, a: dict, b: dict) -> dict:
        ea, eb = extract_equipment(a["text"]), extract_equipment(b["text"])
        # 硬规则：明确不同设备编号 -> 不是同一事项（F08 vs F13 不合并）
        if ea and eb and ea != eb:
            return {"same_item": False, "confidence": 0.95,
                    "reason_code": "DIFF_EQUIPMENT"}
        if ea and ea == eb:
            return {"same_item": True, "confidence": 0.9,
                    "reason_code": "SAME_EQUIPMENT_SAME_PROBLEM"}
        # 无设备编号：高文本相似 + 关键词重叠 -> 判同一事项，
        # 否则保守不合并并转人工。
        from rapidfuzz import fuzz
        sim = fuzz.token_set_ratio(a["text"], b["text"]) / 100.0
        kw = _jaccard(_bigrams(a["text"]), _bigrams(b["text"]))
        if sim >= 0.72 and kw >= 0.35:
            return {"same_item": True, "confidence": 0.82,
                    "reason_code": "HIGH_TEXT_SIMILARITY"}
        return {"same_item": False, "confidence": 0.5,
                "reason_code": "UNCERTAIN_NEED_HUMAN"}

    def summarize_cluster(self, title: str, updates: list[dict]) -> dict:
        if not updates:
            return {"summary": title, "latest_progress": "",
                    "blocker": "", "next_action": ""}
        ordered = sorted(updates, key=lambda u: u.get("date") or "")
        latest = ordered[-1]
        first = ordered[0]
        summary = f"{title}：{first['text']}" if len(ordered) == 1 else \
            f"{title}：{first['text']}（后续持续处理）"
        blocker = ""
        if latest.get("status_hint") in ("in_progress", "blocked"):
            blocker = "尚未完成"
        return {
            "summary": summary,
            "latest_progress": latest["text"],
            "blocker": blocker,
            "next_action": latest.get("next_action", ""),
        }


class QwenAdapter(BaseAdapter):
    """真实 Qwen API（OpenAI-compatible 协议）。"""

    name = "qwen"

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=config.QWEN_API_KEY,
            base_url=config.QWEN_BASE_URL,
            timeout=config.AI_TIMEOUT_SECONDS,
        )
        self.model = config.QWEN_MODEL
        self.last_usage: dict[str, int] = {}

    def _chat(
        self,
        system: str,
        payload: dict,
        *,
        schema_name: str,
        schema: dict,
    ) -> dict:
        kwargs = dict(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",
                 "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            extra_body={"enable_thinking": False},
        )
        # Do not set max_tokens: a truncated JSON object is worse than a clear
        # timeout/failure, which the resilient wrapper can safely fall back on.
        response = self.client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    def judge_merge(self, a: dict, b: dict) -> dict:
        system = (
            "你是交接班事项归并判断器。只判断两条记录是否描述同一事项，"
            "返回 JSON：{\"same_item\": bool, \"confidence\": 0~1, "
            "\"reason_code\": str}。不得改写事实。"
        )
        return self._chat(
            system,
            {"a": a, "b": b},
            schema_name="handover_merge_verdict",
            schema={
                "type": "object",
                "properties": {
                    "same_item": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason_code": {"type": "string"},
                },
                "required": ["same_item", "confidence", "reason_code"],
                "additionalProperties": False,
            },
        )

    def summarize_cluster(self, title: str, updates: list[dict]) -> dict:
        system = (
            "你是江西片区交接班事实整理器。只能使用输入中明确出现的事实，"
            "不得补充常识、推测或编造下一步。返回 JSON：{\"summary\": str, "
            "\"latest_progress\": str, \"blocker\": str, \"next_action\": str}。"
        )
        return self._chat(
            system,
            {"title": title, "updates": updates},
            schema_name="handover_fact_summary",
            schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "latest_progress": {"type": "string"},
                    "blocker": {"type": "string"},
                    "next_action": {"type": "string"},
                },
                "required": ["summary", "latest_progress", "blocker", "next_action"],
                "additionalProperties": False,
            },
        )

    def enrich_preview_rows(self, rows: list[dict], context: dict) -> dict:
        system = (
            "你是江西片区交接班Excel预览整理器。输入已经由程序按日期和场站筛选。"
            "只能使用输入中的明确事实，不得编造日期、人员、设备、处理结果或外委考核。"
            "preview_key必须原样返回；人员和日期由程序确定，你不得输出或修改。"
            "已完成事项建议important，未完成/进行中/受阻/待启动建议handover；"
            "人工预览会最终决定。备注只保留补充进展、处理过程、受阻原因或下一步，"
            "不要与工作内容重复。"
        )
        compact_rows = []
        for row in rows:
            if row.get("kind") != "item":
                continue
            compact_rows.append({
                "preview_key": row.get("preview_key", ""),
                "title_snapshot": row.get("title_snapshot", ""),
                "detected_section": row.get("section", "handover"),
                "detected_status": row.get("status", "unknown"),
                "detected_priority": row.get("priority", "normal"),
                "personnel": row.get("completed_by") or row.get("previous_owner") or "",
                "requirement": row.get("summary", ""),
                "progress": row.get("latest_progress", ""),
                "start_date": row.get("start_date") or "",
                "end_date": row.get("end_date") or "",
            })
        if not compact_rows:
            return {"items": [], "usage": {}, "model": self.model}

        item_schema = {
            "type": "object",
            "properties": {
                "preview_key": {"type": "string", "maxLength": 200},
                "title_snapshot": {"type": "string", "maxLength": 2000},
                "section": {"type": "string", "enum": ["important", "handover"]},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "blocked", "completed", "unknown"],
                },
                "priority": {
                    "type": "string",
                    "enum": ["urgent", "important", "normal"],
                },
                "summary": {"type": "string", "maxLength": 2000},
                "latest_progress": {"type": "string", "maxLength": 2000},
                "blocker": {"type": "string", "maxLength": 2000},
                "next_action": {"type": "string", "maxLength": 2000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "warnings": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "maxLength": 500},
                },
            },
            "required": [
                "preview_key", "title_snapshot", "section", "status", "priority",
                "summary", "latest_progress", "blocker", "next_action",
                "confidence", "warnings",
            ],
            "additionalProperties": False,
        }
        # Keep requests bounded even when a long source workbook contains many
        # rows for one station. The original workbook is never uploaded; only
        # these already filtered compact rows are sent.
        enriched_items: list[dict] = []
        total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        chunk_size = 40
        for offset in range(0, len(compact_rows), chunk_size):
            chunk = compact_rows[offset:offset + chunk_size]
            result = self._chat(
                system,
                {"context": context, "rows": chunk},
                schema_name="handover_import_preview",
                schema={
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "maxItems": len(chunk),
                            "items": item_schema,
                        },
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
            )
            enriched_items.extend(result.get("items") or [])
            for key in total_usage:
                total_usage[key] += int(self.last_usage.get(key, 0) or 0)
        return {
            "items": enriched_items,
            "usage": total_usage,
            "model": self.model,
        }


class ResilientAdapter(BaseAdapter):
    """Use Qwen when available and fall back without blocking operations."""

    name = "qwen_with_fallback"

    def __init__(self, primary: QwenAdapter, fallback: MockAdapter):
        self.primary = primary
        self.fallback = fallback
        self.last_error = ""
        self.fallback_count = 0

    def _run(self, method: str, *args) -> dict:
        try:
            return getattr(self.primary, method)(*args)
        except Exception as exc:  # noqa: BLE001 - explicit safe fallback
            self.fallback_count += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Qwen %s failed; deterministic fallback used", method)
            return getattr(self.fallback, method)(*args)

    def judge_merge(self, a: dict, b: dict) -> dict:
        return self._run("judge_merge", a, b)

    def summarize_cluster(self, title: str, updates: list[dict]) -> dict:
        return self._run("summarize_cluster", title, updates)

    def enrich_preview_rows(self, rows: list[dict], context: dict) -> dict:
        try:
            return self.primary.enrich_preview_rows(rows, context)
        except Exception as exc:  # noqa: BLE001 - preview must still work
            self.fallback_count += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Qwen preview enrichment failed; rules retained")
            return {
                "items": [],
                "usage": {},
                "model": self.primary.model,
                "error": self.last_error,
            }


def get_ai() -> BaseAdapter:
    if config.AI_MODE == "qwen" and config.QWEN_API_KEY and config.QWEN_BASE_URL:
        return ResilientAdapter(QwenAdapter(), MockAdapter())
    return MockAdapter()


def ai_configuration_status() -> dict[str, Any]:
    configured = bool(config.QWEN_API_KEY and config.QWEN_BASE_URL and config.QWEN_MODEL)
    return {
        "mode": config.AI_MODE,
        "model": config.QWEN_MODEL if config.AI_MODE == "qwen" else "mock",
        "configured": configured if config.AI_MODE == "qwen" else True,
        "base_url": config.QWEN_BASE_URL if config.AI_MODE == "qwen" else "",
        "key_hint": (
            f"****{config.QWEN_API_KEY[-4:]}" if config.QWEN_API_KEY else ""
        ),
    }


def test_qwen_connection() -> dict:
    if config.AI_MODE != "qwen":
        return {"ok": True, "mode": "mock", "message": "当前使用本地确定性模式。"}
    if not config.QWEN_API_KEY:
        return {"ok": False, "mode": "qwen", "message": "尚未填写 Qwen API Key。"}
    try:
        adapter = QwenAdapter()
        result = adapter._chat(
            "你是连接检查器。请返回JSON表示服务可用，不要输出其他内容。",
            {"check": "jx-handover"},
            schema_name="handover_ai_health",
            schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must be user friendly
        logger.exception("Qwen connection test failed")
        return {
            "ok": False,
            "mode": "qwen",
            "model": config.QWEN_MODEL,
            "error_type": type(exc).__name__,
            "message": "Qwen连接失败，请检查服务器网络、API Key和服务地址。",
        }
    return {
        "ok": bool(result.get("ok")),
        "mode": "qwen",
        "model": adapter.model,
        "usage": adapter.last_usage,
        "message": "Qwen3.8-Flash 连接正常。" if result.get("ok") else "模型未返回正常状态。",
    }
