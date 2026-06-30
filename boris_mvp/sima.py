from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


STATE_FILE = "state.json"


class SIMAOutput(BaseModel):
    observed_facts: list[str] = Field(default_factory=list)
    system_state: dict[str, str | int | bool] = Field(default_factory=dict)
    unknowns: list[str] = Field(default_factory=list)
    active_context: dict[str, str | list[str]] = Field(default_factory=dict)
    clear_intent: bool = False
    intent_type: str = "unknown"
    missing_intent_fields: list[str] = Field(default_factory=list)
    referent_unresolved: bool = False


def analyze_reality(prompt: str, workspace: Path, project_context: dict | None = None) -> SIMAOutput:
    context = project_context or {}
    files = _context_files(context) if context else _list_workspace_files(workspace)
    root_label = str(context.get("root_path") or workspace.resolve())
    ignored_files_count = int(context.get("ignored_files_count") or 0)
    snippet_names = sorted((context.get("selected_snippets") or {}).keys())
    intent = _classify_intent(prompt, files, snippet_names)
    observed_facts = [
        f"Prompt length: {len(prompt)} characters.",
        f"Context root: {root_label}",
        f"Context files: {len(files)}",
        f"Ignored local files: {ignored_files_count}",
        f"Selected snippets: {len(snippet_names)}",
        f"Intent type: {intent['intent_type']}",
        f"Clear intent: {intent['clear_intent']}",
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
        clear_intent=bool(intent["clear_intent"]),
        intent_type=str(intent["intent_type"]),
        missing_intent_fields=list(intent["missing_intent_fields"]),
        referent_unresolved=bool(intent["referent_unresolved"]),
    )


def _classify_intent(prompt: str, files: list[str], snippet_names: list[str]) -> dict:
    lower = prompt.lower().strip()
    words = [word.strip(".,!?;:()[]{}\"'") for word in lower.split()]
    missing: list[str] = []
    referent_words = {"это", "так", "обратно", "его", "ее", "их", "this", "that", "it", "back"}
    has_referent_word = any(word in referent_words for word in words)

    if not lower:
        return {
            "clear_intent": False,
            "intent_type": "unknown",
            "missing_intent_fields": ["prompt"],
            "referent_unresolved": True,
        }

    if any(word in lower for word in ("restore", "восстанов", "верни", "вернуть", "rollback")):
        intent_type = "restore"
    elif any(word in lower for word in ("explain", "describe", "what", "объясни", "почему", "расскажи")):
        intent_type = "explain"
    elif any(word in lower for word in ("add", "edit", "fix", "change", "implement", "исправь", "добавь", "измени", "почини")):
        intent_type = "edit_files"
    elif any(word in lower for word in ("save", "write", "create", "создай", "сохрани", "запиши")):
        intent_type = "edit_files"
    elif "?" in lower:
        intent_type = "answer"
    else:
        intent_type = "unknown"

    mentioned_files = _mentioned_files(lower, files)
    mentioned_snippets = _mentioned_files(lower, snippet_names)
    has_context = bool(files or snippet_names)
    referent_resolved = False

    if has_referent_word:
        referent_resolved = len(files) == 1 or len(snippet_names) == 1
    elif mentioned_files or mentioned_snippets:
        referent_resolved = True
    elif intent_type in {"answer", "unknown", "restore"}:
        referent_resolved = False
    else:
        referent_resolved = has_context and len(words) >= 4

    if intent_type == "restore":
        if not mentioned_files and not referent_resolved:
            missing.append("object_to_restore")
        missing.append("restore_source_or_checkpoint")
    elif intent_type == "edit_files":
        if not mentioned_files and not mentioned_snippets and not _asks_to_create_file_list(lower):
            missing.append("target_file_or_create_target")
    elif intent_type == "explain":
        if not mentioned_files and not mentioned_snippets and has_referent_word:
            missing.append("object_to_explain")
    elif intent_type == "answer":
        if has_referent_word or len(words) <= 3:
            missing.append("question_subject")
    else:
        missing.append("intent_type")

    context_dependent_short = len(words) <= 3 and (has_referent_word or intent_type in {"answer", "unknown", "restore"})
    unresolved = (has_referent_word and not referent_resolved) or context_dependent_short
    clear = not missing and not unresolved

    return {
        "clear_intent": clear,
        "intent_type": intent_type,
        "missing_intent_fields": missing,
        "referent_unresolved": unresolved,
    }


def _mentioned_files(lower_prompt: str, files: list[str]) -> list[str]:
    mentioned = []
    for file_name in files:
        lowered = file_name.lower()
        basename = lowered.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        if lowered and (lowered in lower_prompt or basename in lower_prompt or stem in lower_prompt):
            mentioned.append(file_name)
    return mentioned


def _asks_to_create_file_list(lower_prompt: str) -> bool:
    create_words = ("создай", "создать", "сохрани", "сохранить", "save", "write", "create")
    list_words = ("список", "list", "перечень", "files", "файлов")
    return any(word in lower_prompt for word in create_words) and any(word in lower_prompt for word in list_words)


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
