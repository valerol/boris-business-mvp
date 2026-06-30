from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


STATE_FILE = "state.json"


class SIMAOutput(BaseModel):
    observed_facts: list[str] = Field(default_factory=list)
    system_state: dict[str, str | int | bool] = Field(default_factory=dict)
    unknowns: list[str] = Field(default_factory=list)
    active_context: dict[str, str | list[str]] = Field(default_factory=dict)


def analyze_reality(prompt: str, workspace: Path, project_context: dict | None = None) -> SIMAOutput:
    context = project_context or {}
    files = _context_files(context) if context else _list_workspace_files(workspace)
    root_label = str(context.get("root_path") or workspace.resolve())
    ignored_files_count = int(context.get("ignored_files_count") or 0)
    snippet_names = sorted((context.get("selected_snippets") or {}).keys())
    observed_facts = [
        f"Prompt length: {len(prompt)} characters.",
        f"Context root: {root_label}",
        f"Context files: {len(files)}",
        f"Ignored local files: {ignored_files_count}",
        f"Selected snippets: {len(snippet_names)}",
    ]

    unknowns: list[str] = []
    if not prompt.strip():
        unknowns.append("User intent is empty.")
    if not files:
        unknowns.append("Workspace has no observable project files.")

    return SIMAOutput(
        observed_facts=observed_facts,
        system_state={
            "workspace_exists": workspace.exists(),
            "file_count": len(files),
            "has_prompt": bool(prompt.strip()),
        },
        unknowns=unknowns,
        active_context={
            "prompt": prompt,
            "files": files[:20],
            "snippet_files": snippet_names[:20],
        },
    )


def _context_files(project_context: dict) -> list[str]:
    tree = project_context.get("file_tree") or []
    return [str(item) for item in tree if isinstance(item, str)]


def _list_workspace_files(workspace: Path, limit: int = 50) -> list[str]:
    ignored = {".git", "__pycache__", ".venv", "venv", "node_modules"}
    root = workspace.resolve()
    if not root.exists():
        return []

    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        if relative.name == STATE_FILE:
            continue
        files.append(relative.as_posix())
        if len(files) >= limit:
            break
    return files
