const assert = require("node:assert/strict");
const Module = require("node:module");
const test = require("node:test");

test("text configuration IPC rejects foreign frames, pins authentication and never exposes server details", async (t) => {
  const handlers = new Map();
  let cookie = "fixture-session", serverError = false;
  const calls = [];
  const frame = { url: "http://127.0.0.1:7094/canvas" };
  const window = { webContents: { mainFrame: frame } };
  const event = { sender: window.webContents, senderFrame: frame };
  const originalLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return {
      ipcMain: { handle: (name, fn) => { handlers.set(name, fn); } },
      session: { defaultSession: { cookies: { get: async () => cookie ? [{ value: cookie }] : [] } } },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { registerManagedTextModels } = require("../src/managed-text-ipc");
  Module._load = originalLoad;
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    cookie = "changed-session";
    if (serverError) return { ok: false, status: 400, json: async () => ({ code: 1, msg: "secret-reflected-fixture-key" }) };
    let data;
    if (url.endsWith("/auth/session")) data = { user: { id: "local-user", username: "local", role: "admin", status: "active" } };
    else if (url.includes("?keyword=")) data = { channels: [] };
    else if (url.endsWith("/models")) data = options.method === "GET" ? { models: [] } : { model: { id: "model" } };
    else data = { channel: { id: "channel" } };
    return { ok: true, status: 200, json: async () => ({ code: 0, data }) };
  };
  registerManagedTextModels({ getWindow: () => window, getBackend: () => ({ baseURL: "http://127.0.0.1:7094" }) });
  const handle = handlers.get("zaowu:models:manage-text");
  const input = { userId: "local-user", sourceId: "gpt", baseUrl: "https://example.com/v1", apiKey: "fixture-key", model: "fixture-text", protocol: "openai-response",
    capabilityConfig: { version: 1, text: { contextWindowTokens: 128000, maxOutputTokens: 16384 } } };
  await assert.rejects(handle({ sender: {}, senderFrame: frame }, input), /主窗口/);
  await assert.rejects(handle({ sender: window.webContents, senderFrame: { ...frame } }, input), /主窗口/);
  frame.url = "https://example.com";
  await assert.rejects(handle(event, input), /主窗口/);
  assert.equal(calls.length, 0);
  frame.url = "http://127.0.0.1:7094/canvas";
  assert.deepEqual(await handle(event, input), { channelId: "channel", channelModelKey: "fixture-text" });
  assert.ok(calls.every((call) => call.options.headers.Cookie === "open_ai_canvas_session=fixture-session"));
  assert.ok(calls.every((call) => call.options.redirect === "error" && call.options.signal));
  assert.deepEqual(await handlers.get("zaowu:models:managed-text-ids")(event), []);
  serverError = true;
  await assert.rejects(handle(event, input), (error) => error.message.includes("HTTP 400") && !error.message.includes("secret-reflected"));
  cookie = "";
  await assert.rejects(handle(event, input), /登录已失效/);
});
