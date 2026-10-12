const path = require("node:path");
const { app, ipcMain, dialog, shell, session } = require("electron");
const { stopProviderForStorage } = require("./provider-client");
const storage = require(app.isPackaged
  ? path.join(process.resourcesPath, "storage.cjs")
  : "../../desktop-shared/storage.cjs");

function registerFileStorage({ getWindow, getBackend, currentResources }) {
  let busy = false;
  const checkSender = (event) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents ||
        event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== getBackend()?.baseURL) {
      throw new Error("仅允许造物主窗口管理文件存储");
    }
  };
  const snapshot = async () => {
    const value = await storage.readStorage(app.getPath("documents"));
    const activeResults = await storage.activeDirectory("provider", null);
    return {
      ...value, activeResources: currentResources, activeResults,
      pending: value.resources !== currentResources || value.results !== activeResults,
    };
  };
  ipcMain.handle("zaowu:storage:get", (event) => { checkSender(event); return snapshot(); });
  ipcMain.handle("zaowu:storage:choose", async (event) => {
    checkSender(event);
    if (busy) throw new Error("文件存储正在处理，请稍后重试");
    busy = true;
    try {
      const value = await snapshot();
      const selected = await dialog.showOpenDialog(getWindow(), {
        title: "选择造物文件保存位置", defaultPath: value.root,
        properties: ["openDirectory", "createDirectory"],
      });
      if (!selected.canceled && selected.filePaths.length === 1) {
        const root = selected.filePaths[0];
        for (const source of [currentResources, value.activeResults].filter(Boolean)) {
          const relative = path.relative(source, root);
          const reverse = path.relative(root, source);
          const contains = (item) => item === "" || (!path.isAbsolute(item) && item !== ".." && !item.startsWith(`..${path.sep}`));
          // The existing shared root is allowed; nested media roots are not.
          if (root !== value.root && (contains(relative) || contains(reverse))) {
            throw new Error("请选择独立文件夹，不能包含现有素材目录或位于其内部");
          }
        }
        await storage.saveStorage(root);
      }
      return snapshot();
    } finally { busy = false; }
  });
  ipcMain.handle("zaowu:storage:open", async (event, kind) => {
    checkSender(event);
    const value = await snapshot();
    const targets = { root: value.root, results: value.activeResults, resources: value.activeResources };
    if (!Object.hasOwn(targets, kind) || !targets[kind]) throw new Error("目录尚未就绪");
    const error = await shell.openPath(targets[kind]);
    if (error) throw new Error("无法打开保存目录");
    return true;
  });
  ipcMain.handle("zaowu:storage:restart", async (event) => {
    checkSender(event);
    if (busy) throw new Error("文件存储正在处理，请稍后重试");
    busy = true;
    try {
      const response = await session.defaultSession.fetch(`${getBackend().baseURL}/api/tasks?activeOnly=true&pageSize=1`);
      if (!response.ok) throw new Error("无法确认造物任务状态，请稍后重试");
      const value = await response.json();
      if (value.code !== 0 || !Array.isArray(value.data)) throw new Error("无法确认造物任务状态");
      if (value.data.length) throw new Error("造物还有任务正在执行，请结束后再应用保存位置");
      await stopProviderForStorage();
      app.relaunch();
      app.quit();
      return true;
    } finally { busy = false; }
  });
}

module.exports = { registerFileStorage };
