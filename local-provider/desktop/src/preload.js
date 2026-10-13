const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("zaowuProvider", {
  restoreSession: () => ipcRenderer.invoke("zaowu-provider:restore-session"),
  setAutomaticLogin: (enabled) => ipcRenderer.invoke("zaowu-provider:automatic-login", enabled),
  getFileStorage: () => ipcRenderer.invoke("zaowu-provider:storage:get"),
  openResultDirectory: () => ipcRenderer.invoke("zaowu-provider:storage:open"),
  revealResult: (taskId, token) => ipcRenderer.invoke("zaowu-provider:media:reveal", taskId, token),
  saveResultAs: (taskId, token) => ipcRenderer.invoke("zaowu-provider:media:save-as", taskId, token),
});
