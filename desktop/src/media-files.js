const path = require("node:path");
const { app, ipcMain, session, dialog, shell } = require("electron");
const { localMediaFile, saveMediaCopy } = require(app.isPackaged
  ? path.join(process.resourcesPath, "local-media.cjs")
  : "../../desktop-shared/local-media.cjs");

function registerMediaFiles({ getWindow, getBackend, resourceDir }) {
  const checkSender = (event) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== getBackend()?.baseURL) throw new Error("仅允许造物主窗口操作媒体文件");
  };
  const resolve = async (id) => {
    if (typeof id !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(id)) throw new Error("媒体资源标识无效");
    // 归属和对象路径由登录态后端确认；渲染进程不能提交任意磁盘路径。
    const response = await session.defaultSession.fetch(`${getBackend().baseURL}/api/resources/${encodeURIComponent(id)}`, {
      redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("媒体资源不存在或不可访问");
    const value = await response.json();
    if (value.code !== 0 || !value.data?.resource) throw new Error("媒体资源不存在或不可访问");
    const resource = value.data.resource;
    if (resource.provider !== "local") return null;
    if (resource.status !== "ready") throw new Error("媒体文件尚未保存完成");
    return localMediaFile(resourceDir, resource.objectKey);
  };
  ipcMain.handle("zaowu:media:inspect", async (event, id) => {
    checkSender(event);
    return { local: Boolean(await resolve(id)) };
  });
  ipcMain.handle("zaowu:media:reveal", async (event, id) => {
    checkSender(event);
    const file = await resolve(id);
    if (!file) throw new Error("此媒体尚未保存到本地，请先下载");
    shell.showItemInFolder(file);
    return true;
  });
  ipcMain.handle("zaowu:media:save-as", async (event, id, name) => {
    checkSender(event);
    const file = await resolve(id);
    if (!file) throw new Error("此媒体尚未保存到本地，请先下载");
    return saveMediaCopy({ file, name, window: getWindow(), dialog });
  });
}

module.exports = { registerMediaFiles };
