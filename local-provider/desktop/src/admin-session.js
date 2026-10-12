const fs = require("node:fs");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const run = promisify(execFile);

function registerAdminSession({ ipcMain, userData, bundleDir, getWindow, baseURL }) {
  const preferenceFile = path.join(userData, "login-preferences.json");
  let restoring = null;
  const automaticLoginEnabled = () => {
    if (!fs.existsSync(preferenceFile)) return true;
    return JSON.parse(fs.readFileSync(preferenceFile, "utf8")).automaticLogin !== false;
  };
  const checkSender = (event) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents ||
        event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== baseURL) {
      throw new Error("仅允许管理客户端恢复登录");
    }
  };
  ipcMain.handle("zaowu-provider:automatic-login", (event, enabled) => {
    checkSender(event);
    if (typeof enabled !== "boolean") throw new Error("自动登录设置无效");
    fs.mkdirSync(userData, { recursive: true });
    fs.writeFileSync(preferenceFile, JSON.stringify({ automaticLogin: enabled }), { mode: 0o600 });
    return true;
  });
  ipcMain.handle("zaowu-provider:restore-session", async (event) => {
    checkSender(event);
    if (!automaticLoginEnabled()) return null;
    if (!restoring) {
      restoring = (async () => {
        const helper = path.join(bundleDir, "zaowu-provider", "zaowu-provider.exe");
        try {
          const { stdout } = await run(helper, ["--desktop-admin-session"], {
            encoding: "utf8", windowsHide: true, timeout: 35000, maxBuffer: 16384,
            env: { ...process.env, ZAOWU_PROVIDER_PORT: new URL(baseURL).port },
          });
          const value = JSON.parse(stdout);
          if (value === null) return null;
          if (value.activated !== true || typeof value.token !== "string" || value.token.length < 20) {
            throw new Error("Invalid management session");
          }
          return value;
        } catch {
          // Child diagnostics can contain the session token; never propagate them.
          throw new Error("已保存卡密未能验证，请检查网络或重新输入有效卡密");
        }
      })().finally(() => { restoring = null; });
    }
    return restoring;
  });
}

module.exports = { registerAdminSession };
