const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("zaowuDesktop", {
  getLocalProviderConnection: () => ipcRenderer.invoke("zaowu:provider:get"),
  saveLocalProviderConnection: (value) => ipcRenderer.invoke("zaowu:provider:save", value),
  openLocalProvider: () => ipcRenderer.invoke("zaowu:provider:open"),
  getFileStorage: () => ipcRenderer.invoke("zaowu:storage:get"),
  chooseFileStorage: () => ipcRenderer.invoke("zaowu:storage:choose"),
  openFileStorage: (kind) => ipcRenderer.invoke("zaowu:storage:open", kind),
  restartForFileStorage: () => ipcRenderer.invoke("zaowu:storage:restart"),
});
