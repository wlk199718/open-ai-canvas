const { app, BrowserWindow, Tray, Menu, dialog, shell, ipcMain } = require("electron");
const path = require("node:path");
const { startService } = require("./service");
const { registerAdminSession } = require("./admin-session");
const storage = require(app.isPackaged
  ? path.join(process.resourcesPath, "storage.cjs")
  : "../../../desktop-shared/storage.cjs");
const { createHash } = require("node:crypto");

let window = null;
let tray = null;
let service = null;
let quitting = false;
const background = process.argv.includes("--background");
let currentResults = null;

async function quitForStorage() {
  try {
    const response = await fetch(`${service?.baseURL || "http://127.0.0.1:8787"}/api/health`, { signal: AbortSignal.timeout(2000) });
    if (!response.ok || (await response.json()).active_tasks !== 0) return;
  } catch { return; }
  app.quit();
}

function showWindow() {
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else if (process.argv.includes("--quit") || process.argv.includes("--quit-for-storage")) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    if (argv.includes("--quit-for-storage")) void quitForStorage();
    else if (argv.includes("--quit")) app.quit();
    else if (!argv.includes("--background")) showWindow();
  });
  app.whenReady().then(async () => {
    const bundleDir = app.isPackaged ? path.join(process.resourcesPath, "bundle") : path.join(__dirname, "..", "bundle");
    try {
      const fileStorage = await storage.readStorage(app.getPath("documents"));
      service = await startService({ bundleDir, logDir: path.join(app.getPath("userData"), "logs"), resultDir: fileStorage.results });
      const healthResponse = await fetch(`${service.baseURL}/api/health`);
      if (!healthResponse.ok) throw new Error("无法读取生成服务状态");
      const health = await healthResponse.json();
      if (health.storage_directory_id === createHash("sha256").update(fileStorage.results).digest("hex")) {
        currentResults = fileStorage.results;
        await storage.recordDirectory("provider", currentResults);
      } else {
        currentResults = await storage.activeDirectory("provider", path.join(process.env.LOCALAPPDATA, "ZaowuProvider", "results"));
      }
      const checkStorageSender = (event) => {
        if (!window || event.sender !== window.webContents ||
            event.senderFrame !== window.webContents.mainFrame ||
            new URL(event.senderFrame.url).origin !== service.baseURL) throw new Error("仅允许管理客户端查看文件存储");
      };
      ipcMain.handle("zaowu-provider:storage:get", async (event) => {
        checkStorageSender(event);
        const value = await storage.readStorage(app.getPath("documents"));
        return { root: value.root, activeResults: currentResults, pending: value.results !== currentResults };
      });
      ipcMain.handle("zaowu-provider:storage:open", async (event) => {
        checkStorageSender(event);
        const error = await shell.openPath(currentResults);
        if (error) throw new Error("无法打开结果目录");
        return true;
      });
      registerAdminSession({
        ipcMain, userData: app.getPath("userData"), bundleDir,
        getWindow: () => window, baseURL: service.baseURL,
      });
      const icon = path.join(__dirname, "..", "build", "icon.png");
      window = new BrowserWindow({
        width: 1440, height: 960, minWidth: 1000, minHeight: 700,
        title: "造物生成服务", backgroundColor: "#111111",
        show: false, icon, autoHideMenuBar: true,
        webPreferences: {
          contextIsolation: true, nodeIntegration: false, sandbox: true,
          preload: path.join(__dirname, "preload.js"),
        },
      });
      const external = (url) => {
        if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
      };
      window.webContents.setWindowOpenHandler(({ url }) => {
        external(url);
        return { action: "deny" };
      });
      window.webContents.on("will-navigate", (event, url) => {
        if (new URL(url).origin !== service.baseURL) {
          event.preventDefault();
          external(url);
        }
      });
      window.on("close", (event) => {
        if (!quitting) {
          event.preventDefault();
          window.hide();
        }
      });
      window.once("ready-to-show", () => { if (!background) showWindow(); });
      await window.loadURL(service.baseURL);
      tray = new Tray(icon);
      tray.setToolTip("造物生成服务 · 本地图片与视频 API");
      tray.setContextMenu(Menu.buildFromTemplate([
        { label: "打开管理界面", click: showWindow },
        { type: "separator" },
        { label: "退出生成服务", click: () => app.quit() },
      ]));
      tray.on("double-click", showWindow);
    } catch (error) {
      dialog.showErrorBox("造物生成服务启动失败", `${error.message}\n日志目录：${path.join(app.getPath("userData"), "logs")}`);
      app.exit(1);
    }
  });
}

app.on("before-quit", (event) => {
  if (quitting || !service) return;
  event.preventDefault();
  quitting = true;
  service.stop().finally(() => app.exit(0));
});
