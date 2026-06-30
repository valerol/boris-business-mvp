from __future__ import annotations

from pydantic import BaseModel, Field

from boris_mvp.bois import BOISTransitionReport
from boris_mvp.boris import BORISConstraintsReport
from boris_mvp.llm import LLMResponse


class ExecutionTrace(BaseModel):
    executed: bool = False
    actions: list[str] = Field(default_factory=list)
    result: str = ""


def execute_response(
    llm_response: LLMResponse,
    post_check: BOISTransitionReport,
    constraints: BORISConstraintsReport,
) -> ExecutionTrace:
    if not post_check.is_valid:
        return ExecutionTrace(actions=["execution_skipped"], result="Execution blocked by BOIS post-check.")
    if not constraints.action_allowed:
        return ExecutionTrace(actions=["execution_skipped"], result="Execution blocked by BORIS constraints.")

    return ExecutionTrace(
        executed=True,
        actions=["return_validated_response"],
        result=llm_response.content,
    )
