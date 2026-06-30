const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("borisAgent", {
  defaultServerUrl: () => ipcRenderer.invoke("agent:defaultServerUrl"),
  selectProject: () => ipcRenderer.invoke("agent:selectProject"),
  scanProject: () => ipcRenderer.invoke("agent:scanProject"),
  applyPatch: (patch) => ipcRenderer.invoke("agent:applyPatch", patch)
});
