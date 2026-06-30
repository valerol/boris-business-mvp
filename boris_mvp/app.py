from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from boris_mvp.bois import BOISTransitionReport, validate_post_llm, validate_pre_llm
from boris_mvp.boris import BORISConstraintsReport, apply_constraints
from boris_mvp.execution import ExecutionTrace, execute_response
from boris_mvp.llm import LLMResponse, model_name, propose_action
from boris_mvp.server_config import has_openai_key, load_dotenv
from boris_mvp.sima import SIMAOutput, analyze_reality


STATE_FILE = "state.json"


class TaskRequest(BaseModel):
    prompt: str
    project_context: dict = Field(default_factory=dict)


class TaskRunRequest(BaseModel):
    prompt: str | None = None


class LogEntry(BaseModel):
    timestamp: str
    message: str


class StopEvent(BaseModel):
    layer: str
    reason: str


class ProposedPatch(BaseModel):
    file_path: str
    diff: str
    summary: str
    requires_approval: bool = True


class TaskState(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    prompt: str = ""
    status: str = "idle"
    analysis: str = ""
    result: str = ""
    project_context: dict = Field(default_factory=dict)
    sima_output: SIMAOutput | None = None
    bois_transition_report: list[BOISTransitionReport] = Field(default_factory=list)
    bois_report: list[BOISTransitionReport] = Field(default_factory=list)
    boris_constraints_report: BORISConstraintsReport | None = None
    boris_report: BORISConstraintsReport | None = None
    context_package: dict = Field(default_factory=dict)
    llm_response: LLMResponse | None = None
    proposed_patches: list[ProposedPatch] = Field(default_factory=list)
    execution_trace: ExecutionTrace | None = None
    stop_events: list[StopEvent] = Field(default_factory=list)
    logs: list[LogEntry] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: now())

    def add_log(self, message: str) -> None:
        self.logs.append(LogEntry(timestamp=now(), message=message))
        self.updated_at = now()

    def stop(self, layer: str, reason: str) -> None:
        self.status = "blocked"
        self.stop_events.append(StopEvent(layer=layer, reason=reason))
        self.result = f"STOP at {layer}: {reason}"
        self.add_log(f"STOP at {layer}: {reason}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(workspace: Path) -> Path:
    return workspace.resolve() / STATE_FILE


def load_state(workspace: Path) -> TaskState:
    path = state_path(workspace)
    if not path.exists():
        return TaskState()
    try:
        return TaskState.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        state = TaskState(status="blocked")
        state.stop("STATE", "Existing state.json is not compatible with the current state model.")
        return state


def save_state(workspace: Path, state: TaskState) -> None:
    workspace.resolve().mkdir(parents=True, exist_ok=True)
    state_path(workspace).write_text(state.model_dump_json(indent=2), encoding="utf-8")


def run_pipeline(prompt: str, workspace: Path, project_context: dict | None = None, task_id: str | None = None) -> TaskState:
    clean_prompt = " ".join(prompt.strip().split())
    state = TaskState(task_id=task_id or str(uuid4()), prompt=clean_prompt, project_context=project_context or {}, status="created")
    state.add_log("Task created from user prompt.")
    save_state(workspace, state)

    state.status = "sima"
    state.sima_output = analyze_reality(clean_prompt, workspace, state.project_context)
    state.analysis = _summarize_sima(state.sima_output)
    state.add_log("SIMA produced a structured reality snapshot.")
    save_state(workspace, state)

    state.status = "bois_pre"
    pre_report = validate_pre_llm(state.sima_output)
    state.bois_transition_report.append(pre_report)
    state.bois_report = state.bois_transition_report
    state.add_log("BOIS validated pre-LLM knowledge transitions.")
    save_state(workspace, state)
    if not pre_report.is_valid:
        state.stop("BOIS", "; ".join(pre_report.reasoning) or "Invalid pre-LLM transition.")
        save_state(workspace, state)
        return state

    state.status = "boris"
    state.boris_constraints_report = apply_constraints(state.sima_output, pre_report)
    state.boris_report = state.boris_constraints_report
    state.add_log("BORIS evaluated domain constraints, authority, and risk.")
    save_state(workspace, state)
    if not state.boris_constraints_report.action_allowed:
        reason = "; ".join(state.boris_constraints_report.stop_conditions) or "BORIS blocked execution."
        state.stop("BORIS", reason)
        save_state(workspace, state)
        return state

    state.status = "context_packaging"
    state.context_package = package_context(
        clean_prompt,
        state.sima_output,
        pre_report,
        state.boris_constraints_report,
        state.project_context,
    )
    state.add_log("Context packaged for LLM proposal generation.")
    save_state(workspace, state)

    state.status = "llm"
    state.llm_response = propose_action(
        clean_prompt,
        state.sima_output,
        state.boris_constraints_report,
        state.context_package,
    )
    state.add_log(f"LLM proposal generated by {state.llm_response.provider}.")
    save_state(workspace, state)

    state.status = "bois_post"
    post_report = validate_post_llm(state.llm_response, pre_report)
    state.bois_transition_report.append(post_report)
    state.bois_report = state.bois_transition_report
    state.add_log("BOIS performed mandatory post-LLM transition check.")
    save_state(workspace, state)
    if not post_report.is_valid:
        state.stop("BOIS", "; ".join(post_report.reasoning) or "Invalid post-LLM transition.")
        save_state(workspace, state)
        return state

    state.status = "execution"
    state.proposed_patches = propose_patches(clean_prompt, state.project_context, state.llm_response)
    state.execution_trace = execute_response(
        state.llm_response,
        post_report,
        state.boris_constraints_report,
    )
    state.result = state.execution_trace.result
    state.add_log("Execution completed only after all layers passed.")
    save_state(workspace, state)

    state.status = "done" if state.execution_trace.executed else "blocked"
    state.add_log("Full layered trace saved locally.")
    save_state(workspace, state)
    return state


def _summarize_sima(snapshot: SIMAOutput) -> str:
    facts = " ".join(snapshot.observed_facts)
    unknowns = ", ".join(snapshot.unknowns) if snapshot.unknowns else "none"
    return f"{facts} Unknowns: {unknowns}."


def package_context(
    prompt: str,
    snapshot: SIMAOutput,
    transition_report: BOISTransitionReport,
    constraints: BORISConstraintsReport,
    project_context: dict,
) -> dict:
    return {
        "prompt": prompt,
        "sima": snapshot.model_dump(mode="json"),
        "bois_pre_check": transition_report.model_dump(mode="json"),
        "boris_constraints": constraints.model_dump(mode="json"),
        "project_context": project_context,
        "llm_role": "proposal_generator_only",
    }


def propose_patches(prompt: str, project_context: dict, llm_response: LLMResponse) -> list[ProposedPatch]:
    lower = prompt.lower()
    if not any(word in lower for word in ("patch", "change", "edit", "add", "fix", "implement", "добавь", "измени", "почини")):
        return []

    snippets = project_context.get("selected_snippets") or {}
    if not isinstance(snippets, dict) or not snippets:
        return []

    file_path = next(iter(snippets))
    original = str(snippets[file_path])
    addition = (
        "\n\n"
        "# BORIS proposed change\n"
        f"# Request: {' '.join(prompt.split())}\n"
    )
    updated = original.rstrip("\n") + addition + "\n"
    diff = _unified_diff(file_path, original, updated)
    return [
        ProposedPatch(
            file_path=file_path,
            diff=diff,
            summary=f"Proposed local-only patch for {file_path}. Review before applying.",
        )
    ]


def _unified_diff(file_path: str, original: str, updated: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )


def create_app(workspace: Path | None = None) -> FastAPI:
    app = FastAPI(title="BORIS Business MVP")
    app.state.workspace = (workspace or Path.cwd()).resolve()
    load_dotenv(app.state.workspace)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return UI_HTML

    @app.get("/api/state")
    def get_state() -> dict:
        return load_state(app.state.workspace).model_dump(mode="json")

    @app.post("/api/tasks")
    def create_task(request: TaskRequest) -> dict:
        state = TaskState(
            prompt=" ".join(request.prompt.strip().split()),
            project_context=request.project_context,
            status="created",
        )
        state.add_log("Server task created from desktop request.")
        save_state(app.state.workspace, state)
        return state.model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/plan")
    def plan_task(task_id: str) -> dict:
        current = load_state(app.state.workspace)
        _ensure_task(task_id, current)
        state = run_pipeline(current.prompt, app.state.workspace, current.project_context, task_id=current.task_id)
        return state.model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/patch")
    def patch_task(task_id: str) -> dict:
        current = load_state(app.state.workspace)
        _ensure_task(task_id, current)
        if current.status not in {"done", "blocked"}:
            current = run_pipeline(current.prompt, app.state.workspace, current.project_context, task_id=current.task_id)
        return {
            "task_id": current.task_id,
            "status": current.status,
            "proposed_patches": [patch.model_dump(mode="json") for patch in current.proposed_patches],
        }

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        current = load_state(app.state.workspace)
        _ensure_task(task_id, current)
        return current.model_dump(mode="json")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model": model_name(),
            "openai_configured": has_openai_key(),
        }

    @app.post("/task")
    def create_task_contract(request: TaskRequest) -> dict:
        state = TaskState(prompt=" ".join(request.prompt.strip().split()), project_context=request.project_context, status="created")
        state.add_log("Task created and persisted; run step is pending.")
        save_state(app.state.workspace, state)
        return state.model_dump(mode="json")

    @app.post("/task/run")
    def run_task_contract(request: TaskRunRequest | None = None) -> dict:
        current = load_state(app.state.workspace)
        prompt = request.prompt if request and request.prompt is not None else current.prompt
        state = run_pipeline(prompt, app.state.workspace, current.project_context, task_id=current.task_id)
        return state.model_dump(mode="json")

    @app.get("/task/state")
    def get_task_state_contract() -> dict:
        return load_state(app.state.workspace).model_dump(mode="json")

    @app.post("/api/reset")
    def reset() -> dict:
        state = TaskState(status="idle")
        state.add_log("State reset.")
        save_state(app.state.workspace, state)
        return state.model_dump(mode="json")

    return app


def _ensure_task(task_id: str, state: TaskState) -> None:
    if state.task_id != task_id:
        raise HTTPException(status_code=404, detail="Task not found")


app = create_app()


UI_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BORIS Business MVP</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #657282;
      --line: #d9dee7;
      --accent: #1f7a5c;
      --accent-dark: #165f47;
      --code: #101820;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    main { max-width: 1120px; margin: 0 auto; padding: 28px; }
    header { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 20px; }
    h1 { margin: 0; font-size: 28px; font-weight: 720; letter-spacing: 0; }
    .subtitle { color: var(--muted); margin-top: 6px; font-size: 14px; }
    .status {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 8px 12px;
      color: var(--muted);
      min-width: 140px;
      text-align: center;
    }
    .grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    label { display: block; font-weight: 650; margin-bottom: 8px; }
    textarea {
      width: 100%;
      min-height: 150px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      font: inherit;
      line-height: 1.45;
      color: var(--text);
      background: #fbfcfd;
    }
    .actions { display: flex; gap: 10px; margin-top: 12px; }
    button {
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button.primary { background: var(--accent); color: white; }
    button.primary:hover { background: var(--accent-dark); }
    button.secondary { border: 1px solid var(--line); background: white; color: var(--text); }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
      border-radius: 8px;
      padding: 14px;
      background: var(--code);
      color: #eef4f8;
      line-height: 1.45;
    }
    #output { min-height: 220px; }
    #trace { min-height: 260px; margin-top: 10px; }
    .meta { display: grid; gap: 10px; color: var(--muted); font-size: 14px; }
    .log { display: grid; gap: 8px; margin-top: 12px; max-height: 260px; overflow: auto; }
    .log div { border-top: 1px solid var(--line); padding-top: 8px; line-height: 1.35; }
    @media (max-width: 840px) {
      main { padding: 18px; }
      header { align-items: flex-start; flex-direction: column; }
      .grid { grid-template-columns: 1fr; }
      .status { text-align: left; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>BORIS Business MVP</h1>
        <div class="subtitle">SIMA -> BOIS -> BORIS -> LLM -> BOIS -> EXECUTION</div>
      </div>
      <div class="status" id="status">idle</div>
    </header>
    <div class="grid">
      <section>
        <label for="prompt">Task prompt</label>
        <textarea id="prompt" placeholder="Example: explain what this project does"></textarea>
        <div class="actions">
          <button class="primary" id="run">Run</button>
          <button class="secondary" id="reset">Reset</button>
        </div>
        <label style="margin-top:16px;">Output</label>
        <pre id="output">No task has run yet.</pre>
        <label style="margin-top:16px;">Layer trace</label>
        <pre id="trace">No trace yet.</pre>
      </section>
      <section>
        <label>Task state</label>
        <div class="meta">
          <div><strong>ID:</strong> <span id="taskId">-</span></div>
          <div><strong>Updated:</strong> <span id="updated">-</span></div>
          <div><strong>Analysis:</strong> <span id="analysis">-</span></div>
        </div>
        <label style="margin-top:16px;">Logs</label>
        <div class="log" id="logs"></div>
      </section>
    </div>
  </main>
  <script>
    const promptEl = document.getElementById("prompt");
    const statusEl = document.getElementById("status");
    const outputEl = document.getElementById("output");
    const traceEl = document.getElementById("trace");
    const taskIdEl = document.getElementById("taskId");
    const updatedEl = document.getElementById("updated");
    const analysisEl = document.getElementById("analysis");
    const logsEl = document.getElementById("logs");
    const runButton = document.getElementById("run");
    const resetButton = document.getElementById("reset");

    function render(state) {
      statusEl.textContent = state.status || "idle";
      taskIdEl.textContent = state.task_id || "-";
      updatedEl.textContent = state.updated_at || "-";
      analysisEl.textContent = state.analysis || "-";
      outputEl.textContent = state.result || stopText(state) || "No task has run yet.";
      traceEl.textContent = JSON.stringify({
        sima_output: state.sima_output,
        bois_transition_report: state.bois_transition_report,
        boris_constraints_report: state.boris_constraints_report,
        context_package: state.context_package,
        llm_response: state.llm_response,
        execution_trace: state.execution_trace,
        stop_events: state.stop_events
      }, null, 2);
      logsEl.innerHTML = "";
      (state.logs || []).forEach((entry) => {
        const row = document.createElement("div");
        row.textContent = `${entry.timestamp}  ${entry.message}`;
        logsEl.appendChild(row);
      });
    }

    function stopText(state) {
      if (!state.stop_events || state.stop_events.length === 0) return "";
      return state.stop_events.map((event) => `STOP at ${event.layer}: ${event.reason}`).join("\\n");
    }

    async function loadState() {
      const response = await fetch("/api/state");
      render(await response.json());
    }

    runButton.addEventListener("click", async () => {
      runButton.disabled = true;
      statusEl.textContent = "running";
      try {
        const response = await fetch("/api/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: promptEl.value })
        });
        render(await response.json());
      } finally {
        runButton.disabled = false;
      }
    });

    resetButton.addEventListener("click", async () => {
      const response = await fetch("/api/reset", { method: "POST" });
      render(await response.json());
    });

    loadState();
  </script>
</body>
</html>
"""
