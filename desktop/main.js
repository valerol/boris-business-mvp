const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");

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
  const filePatches = parseUnifiedDiff(patch.diff);
  if (filePatches.length === 0) {
    throw new Error("No patch targets found.");
  }

  const backups = [];
  const targets = [];
  for (const filePatch of filePatches) {
    const target = filePatch.target;
    if (isIgnored(target)) {
      throw new Error(`Refusing to patch ignored or sensitive file: ${target}`);
    }
    const full = safeResolve(root, target);
    targets.push(target);
    if (fs.existsSync(full)) {
      const backup = `${full}.boris-backup-${Date.now()}`;
      fs.copyFileSync(full, backup);
      backups.push(path.relative(root, backup));
    } else {
      fs.mkdirSync(path.dirname(full), { recursive: true });
    }
    applyFilePatch(full, filePatch);
  }

  return {
    applied: true,
    targets,
    backups,
    message: "Patch applied locally after approval."
  };
}

function parseUnifiedDiff(diff) {
  const lines = diff.split(/\r?\n/);
  const patches = [];
  let current = null;

  for (const line of lines) {
    if (line.startsWith("--- ")) {
      if (current) patches.push(current);
      current = { source: line.slice(4).trim(), target: null, hunks: [] };
      continue;
    }
    if (line.startsWith("+++ ")) {
      if (!current) throw new Error("Malformed patch: target without source.");
      current.target = cleanDiffPath(line.slice(4).trim());
      continue;
    }
    if (current && line.startsWith("@@")) {
      current.hunks.push([]);
      continue;
    }
    if (current && current.hunks.length > 0) {
      current.hunks[current.hunks.length - 1].push(line);
    }
  }
  if (current) patches.push(current);
  return patches.filter((item) => item.target);
}

function cleanDiffPath(rawPath) {
  if (rawPath === "/dev/null") return null;
  if (rawPath.startsWith("b/")) return rawPath.slice(2);
  if (rawPath.startsWith("a/")) return rawPath.slice(2);
  return rawPath;
}

function applyFilePatch(fullPath, filePatch) {
  const exists = fs.existsSync(fullPath);
  const original = exists ? fs.readFileSync(fullPath, "utf8") : "";
  const originalLines = original.length ? original.split(/\r?\n/) : [];
  if (original.endsWith("\n")) originalLines.pop();

  const output = [];
  let originalIndex = 0;

  for (const hunk of filePatch.hunks) {
    for (const line of hunk) {
      if (line === "\ No newline at end of file") continue;
      const marker = line[0];
      const value = line.slice(1);
      if (marker === " ") {
        if (originalLines[originalIndex] !== value) {
          throw new Error(`Patch context mismatch in ${filePatch.target}.`);
        }
        output.push(value);
        originalIndex += 1;
      } else if (marker === "-") {
        if (originalLines[originalIndex] !== value) {
          throw new Error(`Patch removal mismatch in ${filePatch.target}.`);
        }
        originalIndex += 1;
      } else if (marker === "+") {
        output.push(value);
      }
    }
  }

  while (originalIndex < originalLines.length) {
    output.push(originalLines[originalIndex]);
    originalIndex += 1;
  }

  fs.writeFileSync(fullPath, output.join("\n") + "\n", "utf8");
}
