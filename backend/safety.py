"""Independent routing safety signals; these are not clinical diagnoses."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RiskSignal = Literal[
    "chest_distress", "breathing_distress", "severe_bleeding", "consciousness_change",
    "convulsion", "collapse", "self_harm", "possible_urgent_risk",
]
EMERGENCY_SIGNALS = {
    "胸痛": "chest_distress", "呼吸困难": "breathing_distress", "大量出血": "severe_bleeding",
    "意识模糊": "consciousness_change", "抽搐": "convulsion", "晕厥": "collapse",
}
EMERGENCY_WORDS = tuple(EMERGENCY_SIGNALS)
URGENT_PHRASES = {
    **EMERGENCY_SIGNALS,
    "喘不上气": "breathing_distress", "喘不过气": "breathing_distress",
    "透不过气": "breathing_distress", "叫不醒": "consciousness_change",
    "血流不止": "severe_bleeding",
}
# A review trigger is not a diagnosis and is never itself a normal-route permit.
REVIEW_PATTERNS = {
    "breathing_distress": r"喘不[上过]气|喘不过来|透不过气|呼吸.{0,6}(?:费劲|吃力)|气.{0,3}(?:吸不进|不够用)|说不出完整.{0,3}话",
    "chest_distress": r"(?:胸口|胸部|心口).{0,20}(?:压住|压着|发紧|闷|不舒服|冷汗)|(?:冷汗|脸色苍白).{0,20}(?:胸口|心口)",
    "consciousness_change": r"叫不醒|喊不醒|神志不清|意识不清|失去意识|突然.{0,6}(?:说不清话|站不稳)",
    "severe_bleeding": r"血流不止|出血不止|止不住血",
    "collapse": r"突然.{0,6}(?:倒下|倒地)|快要昏倒",
    "self_harm": r"想自杀|想死|结束生命|伤害自己|活不下去|吞服.{0,8}(?:大量|一瓶).{0,8}(?:药|片)",
}


class SafetyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_level: Literal["high", "uncertain", "no_signal"]
    risk_signals: list[RiskSignal] = Field(default_factory=list)
    source: Literal["rule", "llm", "failsafe"]

    @model_validator(mode="after")
    def validate_signals(self):
        if self.risk_level == "no_signal" and self.risk_signals:
            raise ValueError("无风险线索结果不能同时包含风险信号")
        if self.risk_level != "no_signal" and not self.risk_signals:
            raise ValueError("风险或不确定结果必须保留风险信号")
        self.risk_signals = list(dict.fromkeys(self.risk_signals))
        return self

    @property
    def route_policy(self) -> Literal["urgent_safety", "safety_clarification", "normal"]:
        return {"high": "urgent_safety", "uncertain": "safety_clarification", "no_signal": "normal"}[self.risk_level]

    def metadata(self) -> dict:
        return {**self.model_dump(), "route_policy": self.route_policy}


class SafetyReview(BaseModel):
    """Provider response; it cannot select the downstream routing policy."""
    model_config = ConfigDict(extra="forbid", strict=True)
    risk_level: Literal["high", "uncertain", "no_signal"]
    risk_signals: list[RiskSignal]
    evidence: list[str] = Field(max_length=8)

    def decision(self, message: str) -> SafetyDecision:
        if self.risk_level != "no_signal" and not self.evidence:
            raise ValueError("风险复核必须包含原文证据")
        if any(not phrase.strip() or phrase not in message for phrase in self.evidence):
            raise ValueError("风险复核证据必须逐字来自输入")
        return SafetyDecision(risk_level=self.risk_level, risk_signals=self.risk_signals, source="llm")


def _context_requires_review(message: str, start: int, end: int) -> bool:
    # Qualifiers cannot clear a risk; they only require independent review.
    separators = list(re.finditer(r"[，,。！？!?；;\n]", message[:start]))
    clause_start = separators[-1].end() if separators else 0
    prefix = message[max(clause_start, start - 30):start]
    suffix = re.split(r"[，,。！？!?；;\n]", message[end:end + 30], maxsplit=1)[0]
    assertions = list(re.finditer(r"现在|目前|此刻|当前|但(?:出现|有|感觉|感到|又|仍)|而是", prefix))
    if assertions:
        prefix = prefix[assertions[-1].end():]
    return bool(
        re.search(r"否认|没有|并无|未出现|不伴|已无|不再|从未|不是|无.{0,5}$", prefix)
        or re.search(r"去年|上周|既往|曾经|曾有|以前|过去|前次", prefix)
        or re.search(r"查询|检索|查阅|解释|科普|什么是|了解|介绍", prefix)
        or re.search(r"已(?:完全)?(?:恢复|缓解|消失)|已经好了", suffix)
        or (not re.search(r"我|患者|病人", prefix) and re.match(r"(?:是什么|是什么意思|的定义|诊疗指南|的处置流程)", suffix))
    )


def screen_safety(message: str) -> tuple[SafetyDecision | None, list[RiskSignal]]:
    direct, contextual = [], []
    for word, signal in URGENT_PHRASES.items():
        for match in re.finditer(re.escape(word), message):
            target = contextual if _context_requires_review(message, match.start(), match.end()) else direct
            target.append(signal)
    if direct:
        return SafetyDecision(risk_level="high", risk_signals=direct, source="rule"), []
    candidates = list(dict.fromkeys(contextual + [signal for signal, pattern in REVIEW_PATTERNS.items() if re.search(pattern, message)]))
    if candidates:
        return None, candidates
    return SafetyDecision(risk_level="no_signal", source="rule"), []
