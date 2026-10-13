const path = require("node:path");
const { app, ipcMain, dialog, shell } = require("electron");
const { localMediaFile, saveMediaCopy } = require(app.isPackaged
  ? path.join(process.resourcesPath, "local-media.cjs")
  : "../../../desktop-shared/local-media.cjs");

function registerResultFiles({ getWindow, baseURL, getResultDir }) {
  const checkSender = (event) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== baseURL) throw new Error("仅允许管理客户端操作生成结果");
  };
  const resolve = async (taskId, token) => {
    if (typeof taskId !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(taskId) ||
        typeof token !== "string" || token.length < 20 || token.length > 1024) throw new Error("生成结果请求无效");
    const response = await fetch(`${baseURL}/api/admin/tasks/${encodeURIComponent(taskId)}/result`, {
      headers: { Authorization: `Bearer ${token}` }, redirect: "error", signal: AbortSignal.timeout(10000),
    });
    const disposition = response.headers.get("content-disposition") || "";
    await response.body?.cancel();
    if (!response.ok) throw new Error("生成结果不存在或管理会话已失效");
    const name = /filename="([A-Za-z0-9_.-]+)"/.exec(disposition)?.[1];
    if (!name || path.basename(name, path.extname(name)) !== taskId) throw new Error("生成结果文件标识无效");
    return localMediaFile(getResultDir(), name);
  };
  ipcMain.handle("zaowu-provider:media:reveal", async (event, taskId, token) => {
    checkSender(event);
    shell.showItemInFolder(await resolve(taskId, token));
    return true;
  });
  ipcMain.handle("zaowu-provider:media:save-as", async (event, taskId, token) => {
    checkSender(event);
    const file = await resolve(taskId, token);
    return saveMediaCopy({ file, window: getWindow(), dialog });
  });
}

module.exports = { registerResultFiles };
