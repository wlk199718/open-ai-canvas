const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");

test("storage IPC validates the main frame and blocks restart while tasks are active", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zaowu-storage-ipc-"));
  const oldEnv = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = path.join(root, "local");
  const originalLoad = Module._load;
  const handlers = new Map();
  const frame = { url: "http://127.0.0.1:7094/settings" };
  const window = { webContents: { mainFrame: frame } };
  const event = { sender: window.webContents, senderFrame: frame };
  let tasks = [{ id: "busy" }];
  let stopped = 0;
  let relaunched = 0;
  let quitted = 0;
  let selection;
  const electron = {
    app: { isPackaged: false, getPath: () => path.join(root, "docs"),
      relaunch: () => relaunched++, quit: () => quitted++ },
    ipcMain: { handle: (key, handler) => handlers.set(key, handler) },
    dialog: { showOpenDialog: async () => ({ canceled: !selection, filePaths: selection ? [selection] : [] }) },
    shell: { openPath: async () => "" },
    session: { defaultSession: { fetch: async () => ({ ok: true, json: async () => ({ code: 0, data: tasks }) }) } },
  };
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return electron;
    if (request === "./provider-client") return { stopProviderForStorage: async () => { stopped++; } };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { registerFileStorage } = require("../src/file-storage");
  Module._load = originalLoad;
  t.after(() => {
    if (oldEnv === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = oldEnv;
    fs.rmSync(root, { recursive: true, force: true });
  });
  const storage = require("../../desktop-shared/storage.cjs");
  const settings = await storage.prepareCanvasStorage(path.join(root, "docs"), path.join(root, "legacy"));
  await storage.recordDirectory("provider", settings.results);
  registerFileStorage({ getWindow: () => window, getBackend: () => ({ baseURL: "http://127.0.0.1:7094" }), currentResources: settings.resources });
  const get = handlers.get("zaowu:storage:get");
  assert.throws(() => get({ sender: {}, senderFrame: frame }), /主窗口/);
  assert.throws(() => get({ sender: window.webContents, senderFrame: { ...frame } }), /主窗口/);
  frame.url = "http://example.invalid/";
  assert.throws(() => get(event), /主窗口/);
  frame.url = "http://127.0.0.1:7094/settings";
  assert.equal((await get(event)).pending, false);
  selection = path.join(root, "chosen");
  const chosen = await handlers.get("zaowu:storage:choose")(event);
  assert.equal(chosen.root, selection);
  assert.equal(chosen.activeResources, settings.resources);
  assert.equal(chosen.pending, true);
  await assert.rejects(handlers.get("zaowu:storage:restart")(event), /任务正在执行/);
  assert.equal(stopped, 0);
  tasks = [];
  assert.equal(await handlers.get("zaowu:storage:restart")(event), true);
  assert.deepEqual([stopped, relaunched, quitted], [1, 1, 1]);
});
