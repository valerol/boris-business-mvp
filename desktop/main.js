const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawnSync } = require("child_process");

let selectedRoot = null;

function createWindow() {
  const win = new BrowserWindow({
    width: 1220,
    height: 860,
    minWidth: 980,
    minHeight: 680,
    title: "BORIS Business MVP",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

ipcMain.handle("agent:defaultServerUrl", () => {
  return process.env.BORIS_SERVER_URL || "http://127.0.0.1:8765";
});

ipcMain.handle("agent:selectProject", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory"],
    title: "Select project folder"
  });
  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }
  selectedRoot = path.resolve(result.filePaths[0]);
  return selectedRoot;
});

ipcMain.handle("agent:scanProject", () => {
  ensureRoot();
  return buildProjectContext(selectedRoot);
});

ipcMain.handle("agent:applyPatch", (_event, patch) => {
  ensureRoot();
  return applyPatch(selectedRoot, patch);
});

function ensureRoot() {
  if (!selectedRoot) {
    throw new Error("No project folder selected.");
  }
}

const ignoredNames = new Set([
  ".env",
  "id_rsa",
  "id_ed25519",
  "node_modules",
  ".git",
  "__pycache__"
]);

const textExtensions = new Set([
  ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".toml",
  ".yaml", ".yml", ".html", ".css", ".scss", ".sh", ".rs", ".go", ".java"
]);

function isIgnored(relativePath) {
  const parts = relativePath.split(path.sep);
  const base = parts[parts.length - 1];
  if (base.startsWith(".env.")) return true;
  if (base.endsWith(".pem") || base.endsWith(".key")) return true;
  return parts.some((part) => ignoredNames.has(part));
}

function isTextFile(filePath) {
  return textExtensions.has(path.extname(filePath).toLowerCase());
}

function safeResolve(root, relativePath) {
  const resolved = path.resolve(root, relativePath);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    throw new Error(`Path escapes selected project root: ${relativePath}`);
  }
  return resolved;
}

function buildProjectContext(root) {
  const fileTree = [];
  const selectedSnippets = {};
  let ignoredFilesCount = 0;

  function walk(current) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      const relative = path.relative(root, full);
      if (isIgnored(relative)) {
        ignoredFilesCount += 1;
        continue;
      }
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.isFile()) continue;
      fileTree.push(relative);
      if (Object.keys(selectedSnippets).length < 8 && isTextFile(full)) {
        const content = fs.readFileSync(full, "utf8");
        selectedSnippets[relative] = content.split(/\r?\n/).slice(0, 80).join("\n");
      }
      if (fileTree.length >= 300) return;
    }
  }

  walk(root);
  return {
    root_path: root,
    file_tree: fileTree,
    selected_snippets: selectedSnippets,
    ignored_files_count: ignoredFilesCount
  };
}

function applyPatch(root, patch) {
  if (!patch || typeof patch.diff !== "string") {
    throw new Error("Patch diff is missing.");
  }
  const targets = parsePatchTargets(patch.diff);
  if (targets.length === 0) {
    throw new Error("No patch targets found.");
  }
  for (const target of targets) {
    if (isIgnored(target)) {
      throw new Error(`Refusing to patch ignored or sensitive file: ${target}`);
    }
    safeResolve(root, target);
  }

  const check = spawnSync("git", ["apply", "--check", "-"], {
    cwd: root,
    input: patch.diff,
    encoding: "utf8"
  });
  if (check.status !== 0) {
    throw new Error(check.stderr || "Patch validation failed.");
  }

  const backups = [];
  for (const target of targets) {
    const full = safeResolve(root, target);
    if (fs.existsSync(full)) {
      const backup = `${full}.boris-backup-${Date.now()}`;
      fs.copyFileSync(full, backup);
      backups.push(path.relative(root, backup));
    }
  }

  const applied = spawnSync("git", ["apply", "-"], {
    cwd: root,
    input: patch.diff,
    encoding: "utf8"
  });
  if (applied.status !== 0) {
    throw new Error(applied.stderr || "Patch apply failed.");
  }

  return {
    applied: true,
    targets,
    backups,
    message: "Patch applied locally after approval."
  };
}

function parsePatchTargets(diff) {
  const targets = [];
  for (const line of diff.split(/\r?\n/)) {
    if (!line.startsWith("+++ b/")) continue;
    const target = line.slice("+++ b/".length).trim();
    if (target && target !== "/dev/null") {
      targets.push(target);
    }
  }
  return [...new Set(targets)];
}
