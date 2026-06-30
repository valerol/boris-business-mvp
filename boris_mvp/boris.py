from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from boris_mvp.bois import BOISTransitionReport
from boris_mvp.sima import SIMAOutput


class BORISConstraintsReport(BaseModel):
    risk_level: Literal["low", "medium", "high"] = "low"
    stop_conditions: list[str] = Field(default_factory=list)
    authority: dict[str, str | bool] = Field(default_factory=dict)
    domain_rules: list[str] = Field(default_factory=list)
    action_allowed: bool = False


def apply_constraints(snapshot: SIMAOutput, transition_report: BOISTransitionReport) -> BORISConstraintsReport:
    prompt = str(snapshot.active_context.get("prompt", "")).lower()
    report = BORISConstraintsReport(
        domain_rules=[
            "No destructive action without explicit approval.",
            "No execution when SIMA reports missing intent.",
            "No execution when BOIS blocks a transition.",
        ],
        authority={"approval_required": False, "approved": True},
    )

    if not transition_report.is_valid:
        report.risk_level = "high"
        report.stop_conditions.append("BOIS blocked the pre-LLM knowledge transition.")
        return report

    if "User intent is empty." in snapshot.unknowns:
        report.risk_level = "high"
        report.stop_conditions.append("Missing user intent.")
        return report

    high_risk_terms = ("delete", "remove", "overwrite", "rm ", "sudo", "format", "wipe")
    if any(term in prompt for term in high_risk_terms):
        report.risk_level = "high"
        report.stop_conditions.append("High risk operation requires explicit approval.")
        report.authority = {"approval_required": True, "approved": False}
        return report

    report.risk_level = "medium" if snapshot.unknowns else "low"
    report.action_allowed = True
    return report
