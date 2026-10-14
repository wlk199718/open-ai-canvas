// 造物桌面端主进程：启动 Go 后端 → 自动登录本地账号 → 打开窗口；退出时优雅关闭后端。
const { app, BrowserWindow, dialog, shell, session } = require("electron");
const path = require("node:path");
const { startBackend } = require("./backend");
const { ensureLocalSession } = require("./local-account");
const { registerLocalProvider } = require("./local-provider");
const { openProvider } = require("./provider-client");
const { registerFileStorage } = require("./file-storage");
const { registerMediaFiles } = require("./media-files");
const { registerManagedTextModels } = require("./managed-text-ipc");
const storage = require(app.isPackaged
  ? path.join(process.resourcesPath, "storage.cjs")
  : "../../desktop-shared/storage.cjs");

let backend = null;
let mainWindow = null;
let quitting = false;

migrateLegacyUserData();

// 产品曾名「影策」：userData 按 productName 命名，改名后把旧目录整体改名过来，
// 保留本地账号、数据库和素材。仅在新目录尚不存在时执行，失败则继续用空目录启动。
function migrateLegacyUserData() {
  const fs = require("node:fs");
  const target = app.getPath("userData");
  const legacy = path.join(app.getPath("appData"), "影策");
  try {
    if (fs.existsSync(legacy) && !fs.existsSync(target)) fs.renameSync(legacy, target);
  } catch (err) {
    console.error("迁移旧版数据目录失败：", err);
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else if (process.argv.includes("--quit") || process.argv.includes("--quit-for-update")) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    if (argv.includes("--quit") || argv.includes("--quit-for-update")) {
      app.quit();
      return;
    }
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(boot);
}

function bundleDir() {
  // 打包后位于 resources/bundle；开发时位于 desktop/bundle
  return app.isPackaged ? path.join(process.resourcesPath, "bundle") : path.join(__dirname, "..", "bundle");
}

async function boot() {
  const userData = app.getPath("userData");
  try {
    const fileStorage = await storage.prepareCanvasStorage(app.getPath("documents"), path.join(userData, "data", "resources"));
    registerLocalProvider(userData, () => mainWindow, () => backend?.baseURL);
    registerFileStorage({ getWindow: () => mainWindow, getBackend: () => backend, currentResources: fileStorage.resources });
    registerMediaFiles({ getWindow: () => mainWindow, getBackend: () => backend, resourceDir: fileStorage.resources });
    registerManagedTextModels({ getWindow: () => mainWindow, getBackend: () => backend });
    await openProvider(true).catch(() => {});
    backend = await startBackend({
      bundleDir: bundleDir(),
      dataDir: path.join(userData, "data"),
      logDir: path.join(userData, "logs"),
      resourceDir: fileStorage.resources,
    });
  } catch (err) {
    dialog.showErrorBox("造物启动失败", `${err.message}\n\n日志目录：${path.join(userData, "logs")}`);
    app.exit(1);
    return;
  }

  const cookie = await ensureLocalSession(backend.baseURL, path.join(userData, "local-account.bin")).catch(() => "");
  if (cookie) {
    await session.defaultSession.cookies.set({
      url: backend.baseURL,
      name: "open_ai_canvas_session",
      value: cookie,
      httpOnly: true,
      sameSite: "lax",
      expirationDate: Math.floor(Date.now() / 1000) + 30 * 24 * 3600,
    });
  }

  createWindow();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    title: "造物",
    backgroundColor: "#111111",
    icon: path.join(__dirname, "..", "build", "icon.png"),
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  const origin = new URL(backend.baseURL).origin;
  // 站外链接交给系统浏览器，窗口内只允许本地后端页面
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(origin)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(origin)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.loadURL(backend.baseURL);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.on("window-all-closed", () => app.quit());

app.on("before-quit", (event) => {
  if (quitting || !backend) return;
  event.preventDefault();
  quitting = true;
  backend.stop(15000).finally(() => app.exit(0));
});
