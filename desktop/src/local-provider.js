const fs = require("node:fs");
const path = require("node:path");
const { ipcMain, safeStorage } = require("electron");
const { openProvider, importProviderConnection } = require("./provider-client");

function validateConnection(value) {
  const url = new URL(value.baseUrl);
  if (!["http:", "https:"].includes(url.protocol) || url.hostname !== "127.0.0.1" || url.username || url.password) {
    throw new Error("桌面生成服务必须使用 127.0.0.1 地址");
  }
  if (typeof value.token !== "string" || value.token.length < 20 || value.token.length > 256) {
    throw new Error("本地生成服务连接令牌无效");
  }
  return { baseUrl: url.origin, token: value.token };
}

function registerLocalProvider(userData, getWindow, getBackendURL) {
  const file = path.join(userData, "local-provider-connection.bin");
  const save = (value) => {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("系统无法安全保存连接令牌");
    const connection = validateConnection(value);
    fs.writeFileSync(file, safeStorage.encryptString(JSON.stringify(connection)), { mode: 0o600 });
    return true;
  };
  const imported = process.env.ZAOWU_LOCAL_PROVIDER_CONNECTION_FILE;
  if (imported) {
    save(JSON.parse(fs.readFileSync(imported, "utf8")));
    fs.unlinkSync(imported);
    delete process.env.ZAOWU_LOCAL_PROVIDER_CONNECTION_FILE;
  }
  const checkSender = (event) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== getBackendURL()) {
      throw new Error("仅允许造物主窗口管理本地生成连接");
    }
  };
  let importing = null;
  ipcMain.handle("zaowu:provider:get", async (event) => {
    checkSender(event);
    if (!importing) {
      importing = importProviderConnection().finally(() => { importing = null; });
    }
    const importedConnection = await importing;
    if (importedConnection) {
      save(importedConnection);
      return validateConnection(importedConnection);
    }
    if (!fs.existsSync(file)) return null;
    if (!safeStorage.isEncryptionAvailable()) throw new Error("系统无法解密连接令牌");
    return validateConnection(JSON.parse(safeStorage.decryptString(fs.readFileSync(file))));
  });
  ipcMain.handle("zaowu:provider:save", (event, value) => {
    checkSender(event);
    return save(value);
  });
  ipcMain.handle("zaowu:provider:open", (event) => {
    checkSender(event);
    return openProvider();
  });
}

module.exports = { registerLocalProvider };
