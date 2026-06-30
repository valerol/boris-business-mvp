const serverUrlInput = document.getElementById("serverUrl");
const saveServerButton = document.getElementById("saveServer");
const healthButton = document.getElementById("health");
const healthOutput = document.getElementById("healthOutput");
const selectProjectButton = document.getElementById("selectProject");
const scanProjectButton = document.getElementById("scanProject");
const projectPath = document.getElementById("projectPath");
const contextOutput = document.getElementById("contextOutput");
const promptInput = document.getElementById("prompt");
const runButton = document.getElementById("run");
const statusEl = document.getElementById("status");
const traceEl = document.getElementById("trace");
const resultEl = document.getElementById("result");
const patchesEl = document.getElementById("patches");
const applyPatchButton = document.getElementById("applyPatch");
const logsEl = document.getElementById("logs");

let projectContext = null;
let lastTask = null;
let lastPatch = null;

function serverUrl() {
  return (localStorage.getItem("BORIS_SERVER_URL") || serverUrlInput.value || "").replace(/\/$/, "");
}

function setStatus(value) {
  statusEl.textContent = value;
}

function log(message) {
  const row = document.createElement("div");
  row.textContent = `${new Date().toISOString()}  ${message}`;
  logsEl.prepend(row);
  const existing = JSON.parse(localStorage.getItem("local_execution_log") || "[]");
  existing.unshift(row.textContent);
  localStorage.setItem("local_execution_log", JSON.stringify(existing.slice(0, 200)));
}

async function init() {
  const defaultUrl = await window.borisAgent.defaultServerUrl();
  serverUrlInput.value = localStorage.getItem("BORIS_SERVER_URL") || defaultUrl;
  const savedPath = localStorage.getItem("selected_project_path");
  if (savedPath) projectPath.textContent = savedPath;
  JSON.parse(localStorage.getItem("local_execution_log") || "[]").forEach((entry) => {
    const row = document.createElement("div");
    row.textContent = entry;
    logsEl.appendChild(row);
  });
}

saveServerButton.addEventListener("click", () => {
  localStorage.setItem("BORIS_SERVER_URL", serverUrlInput.value.replace(/\/$/, ""));
  log(`Saved BORIS_SERVER_URL: ${serverUrl()}`);
});

healthButton.addEventListener("click", async () => {
  try {
    const response = await fetch(`${serverUrl()}/api/health`);
    const payload = await response.json();
    healthOutput.textContent = `Server ok. Model: ${payload.model}. OpenAI configured: ${payload.openai_configured}`;
    log("Server health check succeeded.");
  } catch (error) {
    healthOutput.textContent = `Health check failed: ${error.message}`;
    log(`Server health check failed: ${error.message}`);
  }
});

selectProjectButton.addEventListener("click", async () => {
  const selected = await window.borisAgent.selectProject();
  if (!selected) return;
  projectPath.textContent = selected;
  localStorage.setItem("selected_project_path", selected);
  log(`Selected project folder: ${selected}`);
});

scanProjectButton.addEventListener("click", async () => {
  try {
    projectContext = await window.borisAgent.scanProject();
    contextOutput.textContent = JSON.stringify(projectContext, null, 2);
    log(`Scanned ${projectContext.file_tree.length} local files; ignored ${projectContext.ignored_files_count}.`);
  } catch (error) {
    contextOutput.textContent = error.message;
    log(`Scan failed: ${error.message}`);
  }
});

runButton.addEventListener("click", async () => {
  if (!projectContext) {
    log("Run blocked: scan a selected project folder first.");
    return;
  }
  setStatus("creating task");
  applyPatchButton.disabled = true;
  lastPatch = null;
  try {
    const createResponse = await fetch(`${serverUrl()}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: promptInput.value,
        project_context: projectContext
      })
    });
    const created = await createResponse.json();
    localStorage.setItem("last_task_id", created.task_id);
    log(`Created server task ${created.task_id}.`);

    setStatus("planning");
    const planResponse = await fetch(`${serverUrl()}/api/tasks/${created.task_id}/plan`, {
      method: "POST"
    });
    lastTask = await planResponse.json();

    setStatus(lastTask.status);
    renderTask(lastTask);
    log(`Task ${lastTask.task_id} completed with status ${lastTask.status}.`);
  } catch (error) {
    setStatus("error");
    log(`Run failed: ${error.message}`);
  }
});

applyPatchButton.addEventListener("click", async () => {
  if (!lastPatch) return;
  const approved = confirm("Apply this patch locally? A backup will be created first.");
  if (!approved) {
    log("Patch apply cancelled by user.");
    return;
  }
  try {
    const result = await window.borisAgent.applyPatch(lastPatch);
    log(result.message);
    patchesEl.textContent += `\n\nApplied locally:\n${JSON.stringify(result, null, 2)}`;
    applyPatchButton.disabled = true;
  } catch (error) {
    log(`Patch apply failed: ${error.message}`);
  }
});

function renderTask(task) {
  traceEl.textContent = JSON.stringify({
    sima_output: task.sima_output,
    bois_report: task.bois_report || task.bois_transition_report,
    boris_report: task.boris_report || task.boris_constraints_report,
    llm_response: task.llm_response,
    action_plan: task.action_plan,
    execution_trace: task.execution_trace,
    stop_events: task.stop_events
  }, null, 2);
  resultEl.textContent = task.result || "No result returned.";
  const patches = task.proposed_patches || [];
  patchesEl.textContent = patches.length ? patches.map((patch) => patch.diff).join("\n\n") : "No patches proposed.";
  lastPatch = patches[0] || null;
  applyPatchButton.disabled = !lastPatch;
}

init();
