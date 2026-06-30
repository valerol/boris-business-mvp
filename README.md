# BORIS Business MVP

BORIS Business MVP is split into two runtime surfaces:

- **Server Core**: FastAPI service with SIMA, BOIS, BORIS, OpenAI access, task state, plans, and patch proposals.
- **MacOS Desktop Local File Agent**: Electron app that reads only a user-selected project folder, sends selected context to the server, shows returned plans/diffs, and applies patches locally only after approval.

The desktop app must never contain or use the OpenAI API key.



## Project Documentation

The documents in `docs/` are the canonical source of truth for project architecture, product requirements, security boundaries, and development rules.

- [Architecture Contract](docs/ARCHITECTURE.md)
- [Product Requirements](docs/PRD.md)
- [Security Model](docs/SECURITY.md)
- [Development Rules](docs/DEVELOPMENT.md)

## Quick desktop start

Run the local MacOS desktop app:

```bash
cd /Users/lera/web/boris-business-mvp/desktop
npm install
npm run dev
```

Build the MacOS app bundle:

```bash
cd /Users/lera/web/boris-business-mvp/desktop
npm run build:mac
```

Before using the desktop app, set `BORIS_SERVER_URL` in the app settings to either your deployed Vercel API URL or local server URL:

```text
https://your-project.vercel.app
http://127.0.0.1:8765
```

## 1. Run Server Locally

Install the Python package once:

```bash
cd /Users/lera/web/boris-business-mvp
python3 -m pip install -e .
```

Start the server:

```bash
boris run --no-browser --port 8765
```

Server API:

```text
GET  /api/health
POST /api/tasks
POST /api/tasks/{task_id}/plan
POST /api/tasks/{task_id}/patch
GET  /api/tasks/{task_id}
```

## 2. Configure `.env`

Create `.env` in the server project root:

```bash
OPENAI_API_KEY=your_server_side_key
OPENAI_MODEL=gpt-4o-mini
```

`.env` is ignored by Git and must not be committed. The server loads this file at startup. The `/api/health` endpoint reports the model name and whether a key is configured, but never returns the key.

## 3. Run Desktop App

Install desktop dependencies:

```bash
cd /Users/lera/web/boris-business-mvp/desktop
npm install
```

Run the MacOS desktop app:

```bash
npm run dev
```

The desktop app does not start the server core. Start the server separately, then point the desktop settings to the server URL.

## 4. Configure `BORIS_SERVER_URL`

In the desktop app settings, set:

```text
http://127.0.0.1:8765
```

You can also launch Electron with:

```bash
BORIS_SERVER_URL=http://127.0.0.1:8765 npm run dev
```

The desktop stores this setting in local app storage.

## 5. Build MacOS App

From the desktop directory:

```bash
npm run build:mac
```

This uses Electron and electron-builder. The current build target is a MacOS directory build for local MVP packaging.


## Deploy server to Vercel

Only the **server core** is deployed to Vercel. The Electron desktop app stays local and talks to the deployed API through `BORIS_SERVER_URL`.

Vercel files:

```text
api/index.py
vercel.json
requirements.txt
```

Deploy steps:

1. Create a Vercel project from this repository.
2. Set environment variables in Vercel project settings:

```bash
OPENAI_API_KEY=your_server_side_key
OPENAI_MODEL=gpt-4o-mini
```

3. Deploy the project.
4. Verify the deployed server:

```bash
curl https://your-project.vercel.app/api/health
```

Expected response shape:

```json
{
  "status": "ok",
  "model": "gpt-4o-mini",
  "openai_configured": true,
  "state_persistence": "memory_ephemeral"
}
```

The API key is never returned by the server.

Vercel MVP state note: task state uses in-memory serverless storage on Vercel. It is intentionally non-persistent and may reset between cold starts or function instances. A database should be added later for production persistence.

Desktop configuration after deploy:

```text
BORIS_SERVER_URL=https://your-project.vercel.app
```

The desktop app continues to scan files locally and sends only selected `project_context` snippets to the deployed server.

## 6. Security Model

- OpenAI API access exists only in the server core.
- The desktop app never reads `.env` and contains no OpenAI API key.
- The server never directly accesses the user's selected local filesystem.
- The local file agent only scans a folder selected by the user.
- The local file agent ignores sensitive files and directories:
  - `.env`
  - `.env.*`
  - `id_rsa`
  - `id_ed25519`
  - `*.pem`
  - `*.key`
  - `node_modules`
  - `.git`
  - `__pycache__`
- The desktop sends only `project_context` to the server:
  - `root_path`
  - `file_tree`
  - `selected_snippets`
  - `ignored_files_count`
- Patches are applied locally only after explicit user approval.
- Patch application creates backups before modifying files.
- Patch application rejects paths outside the selected project root and rejects ignored sensitive files.

## Layered Flow

Every server plan follows:

```text
SIMA -> BOIS -> BORIS -> Context Packaging -> LLM -> BOIS -> Execution Trace
```

The LLM is proposal-only. It cannot bypass BOIS, bypass BORIS, or execute actions.
