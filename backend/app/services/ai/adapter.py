"""AI 适配器。

AI 在本系统中的边界：可以识别、归并和改写，但不能创造事实，
也不能决定业务日期规则。所有 AI 输出必须带 source_ids 硬校验，
且 AI 永远没有发布权限。

AI_MODE=mock 时使用本地确定性模拟实现（不调用外部接口），
便于在无 Qwen API 时跑通完整链路。
"""
from __future__ import annotations

import json
import re

from app import config

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
            api_key=config.QWEN_API_KEY, base_url=config.QWEN_BASE_URL
        )
        self.model = config.QWEN_MODEL

    def _chat(self, system: str, payload: dict) -> dict:
        kwargs = dict(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",
                 "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        # 结构化输出不设 max_tokens，避免 JSON 被截断
        response = self.client.chat.completions.create(**kwargs)
        return json.loads(response.choices[0].message.content)

    def judge_merge(self, a: dict, b: dict) -> dict:
        system = (
            "你是交接班事项归并判断器。只判断两条记录是否描述同一事项，"
            "返回 JSON：{\"same_item\": bool, \"confidence\": 0~1, "
            "\"reason_code\": str}。不得改写事实。"
        )
        return self._chat(system, {"a": a, "b": b})

    def summarize_cluster(self, title: str, updates: list[dict]) -> dict:
        system = (
            "你是江西片区交接班事实整理器。只能使用输入中明确出现的事实，"
            "不得补充常识、推测或编造下一步。返回 JSON：{\"summary\": str, "
            "\"latest_progress\": str, \"blocker\": str, \"next_action\": str}。"
        )
        return self._chat(system, {"title": title, "updates": updates})


def get_ai() -> BaseAdapter:
    if config.AI_MODE == "qwen" and config.QWEN_API_KEY and config.QWEN_BASE_URL:
        return QwenAdapter()
    return MockAdapter()
