from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from boris_mvp.sima import SIMAOutput


KnowledgeState = Literal["K0", "K1", "K2", "K3", "A"]


class BOISTransitionReport(BaseModel):
    phase: Literal["pre_llm", "post_llm"]
    from_state: KnowledgeState
    to_state: KnowledgeState
    allowed_transitions: list[str] = Field(default_factory=list)
    blocked_transitions: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    is_valid: bool = False


def validate_pre_llm(snapshot: SIMAOutput) -> BOISTransitionReport:
    report = BOISTransitionReport(phase="pre_llm", from_state="K0", to_state="K2")
    has_prompt = bool(snapshot.system_state.get("has_prompt"))

    if not has_prompt:
        report.blocked_transitions.append("K0 -> A")
        report.reasoning.append("Cannot move from no user intent to action.")
        return report

    if not snapshot.clear_intent:
        report.blocked_transitions.append("K1 -> K2")
        report.reasoning.append("SIMA marked clear_intent=false; LLM/action transition is blocked.")
    if snapshot.missing_intent_fields:
        report.blocked_transitions.append("K1 -> K2")
        report.reasoning.append("Missing intent fields: " + ", ".join(snapshot.missing_intent_fields))
    if snapshot.referent_unresolved:
        report.blocked_transitions.append("K1 -> K2")
        report.reasoning.append("Prompt contains unresolved referent or context-dependent target.")
    if report.blocked_transitions:
        return report

    report.allowed_transitions.extend(["K0 -> K1", "K1 -> K2"])
    report.reasoning.append("SIMA produced observable facts and active prompt context.")
    report.is_valid = True
    return report


def validate_post_llm(llm_response: object, pre_report: BOISTransitionReport) -> BOISTransitionReport:
    report = BOISTransitionReport(phase="post_llm", from_state="K2", to_state="A")

    if not pre_report.is_valid:
        report.blocked_transitions.append("K2 -> A")
        report.reasoning.append("Pre-LLM transition report was invalid, so action is forbidden.")
        return report

    action_type = getattr(llm_response, "action_type", "unknown")
    confidence = getattr(llm_response, "confidence", 0.0)
    if action_type == "unknown" or confidence < 0.2:
        report.blocked_transitions.append("K2 -> A")
        report.reasoning.append("LLM proposal is too uncertain for execution.")
        return report

    report.allowed_transitions.extend(["K2 -> K3", "K3 -> A"])
    report.reasoning.append("LLM proposal is explicit enough to become an executable response.")
    report.is_valid = True
    return report
