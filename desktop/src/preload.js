const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("zaowuDesktop", {
  manageTextModel: (value) => ipcRenderer.invoke("zaowu:models:manage-text", value),
  getManagedTextChannelIds: () => ipcRenderer.invoke("zaowu:models:managed-text-ids"),
  getLocalProviderConnection: () => ipcRenderer.invoke("zaowu:provider:get"),
  saveLocalProviderConnection: (value) => ipcRenderer.invoke("zaowu:provider:save", value),
  openLocalProvider: () => ipcRenderer.invoke("zaowu:provider:open"),
  getFileStorage: () => ipcRenderer.invoke("zaowu:storage:get"),
  chooseFileStorage: () => ipcRenderer.invoke("zaowu:storage:choose"),
  openFileStorage: (kind) => ipcRenderer.invoke("zaowu:storage:open", kind),
  restartForFileStorage: () => ipcRenderer.invoke("zaowu:storage:restart"),
  inspectMedia: (id) => ipcRenderer.invoke("zaowu:media:inspect", id),
  revealMedia: (id) => ipcRenderer.invoke("zaowu:media:reveal", id),
  saveMediaAs: (id, name) => ipcRenderer.invoke("zaowu:media:save-as", id, name),
});
