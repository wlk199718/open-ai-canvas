const { ipcMain, session } = require("electron");
const { createTextModelManager } = require("./managed-text-models");

function registerManagedTextModels({ getWindow, getBackend }) {
  const sync = createTextModelManager();
  const requestFor = async (event) => {
    const window = getWindow();
    const baseURL = getBackend()?.baseURL;
    if (!window || event.sender !== window.webContents ||
        event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== baseURL ||
        !/^http:\/\/127\.0\.0\.1:\d+$/.test(baseURL)) {
      throw new Error("仅允许造物主窗口同步智能体模型");
    }
    // Pin the authentication cookie for this whole operation. A renderer account
    // switch must not apply one user's configuration under another session.
    const cookies = await session.defaultSession.cookies.get({ url: baseURL, name: "open_ai_canvas_session" });
    if (cookies.length !== 1) throw new Error("本地登录已失效，请重新打开客户端");
    const cookie = `open_ai_canvas_session=${cookies[0].value}`;
    return async (method, route, body) => {
      const response = await fetch(`${baseURL}/api${route}`, {
        method, redirect: "error", signal: AbortSignal.timeout(30000),
        headers: { Cookie: cookie, "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const result = await response.json().catch(() => null);
      // Upstream validation messages can contain connection details. Keep IPC
      // errors independent of secret-bearing request/response bodies.
      if (!response.ok || result?.code !== 0) {
        throw new Error(`智能体模型同步失败（HTTP ${response.status}），请检查个人渠道的地址、协议及本地登录状态`);
      }
      return result.data;
    };
  };
  ipcMain.handle("zaowu:models:manage-text", async (event, input) => {
    return sync(await requestFor(event), input);
  });
  ipcMain.handle("zaowu:models:managed-text-ids", async (event) => {
    const request = await requestFor(event);
    const { user } = await request("GET", "/auth/session");
    if (user?.username !== "local" || user?.role !== "admin" || user?.status !== "active") {
      throw new Error("仅允许当前桌面本地管理员读取智能体模型");
    }
    const ids = [];
    for (let page = 1; ; page++) {
      const result = await request("GET", `/admin/channels?keyword=${encodeURIComponent("造物桌面智能体/")}&limit=100&page=${page}`);
      ids.push(...result.channels.filter((channel) => channel.userId === user.id && channel.scope === "system" &&
        /^造物桌面智能体\/[a-f0-9]{24}$/.test(channel.name)).map((channel) => channel.id));
      if (page * 100 >= result.total || result.channels.length < 100) break;
    }
    return ids;
  });
}

module.exports = { registerManagedTextModels };
