from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boris_mvp.app import run_pipeline


def run_case(prompt, context):
    with TemporaryDirectory() as tmp:
        return run_pipeline(prompt, Path(tmp), context)


def assert_skipped(state):
    assert state.status == "blocked", state.status
    assert state.llm_call.attempted is False, state.llm_call
    assert state.llm_response is None, state.llm_response
    assert state.execution_trace is not None
    assert state.execution_trace.executed is False
    reasons = [event.reason for event in state.stop_events]
    assert any("STOP-MISSING-INTENT" in reason for reason in reasons), reasons


def assert_allowed(state):
    assert state.llm_call.attempted is True, state.llm_call
    assert state.llm_response is not None
    assert state.execution_trace is not None
    assert state.execution_trace.executed is True


ambiguous_restore = run_case("А как восстановить?", {"file_tree": ["README.md"], "selected_snippets": {}, "ignored_files_count": 0})
assert_skipped(ambiguous_restore)

ambiguous_fix = run_case("Исправь это", {"file_tree": ["server/main.py"], "selected_snippets": {}, "ignored_files_count": 0})
assert_skipped(ambiguous_fix)

readme_explain = run_case(
    "Explain what this README says",
    {"file_tree": ["README.md"], "selected_snippets": {"README.md": "# Demo\nThis is a README."}, "ignored_files_count": 0},
)
assert_allowed(readme_explain)

add_logging = run_case(
    "Add logging to server/main.py",
    {"file_tree": ["server/main.py"], "selected_snippets": {}, "ignored_files_count": 0},
)
assert_allowed(add_logging)

print("pre-LLM gate checks passed")
