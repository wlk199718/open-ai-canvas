const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const Module = require("node:module");
const test = require("node:test");

test("provider result file actions require the trusted main frame and an authorized result", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "zaowu-result-ipc-"));
  const originalFetch = global.fetch;
  const originalLoad = Module._load;
  t.after(async () => { global.fetch = originalFetch; await fs.rm(root, { recursive: true, force: true }); });
  const result = path.join(root, "task-1.png");
  await fs.writeFile(result, "fixture-image");
  const frame = { url: "http://127.0.0.1:8787/" };
  const window = { webContents: { mainFrame: frame } };
  const event = { sender: window.webContents, senderFrame: frame };
  const handlers = new Map();
  let revealed;
  let allowed = true;
  let filename = "task-1.png";
  let cancelled = 0;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return {
      app: { isPackaged: false },
      ipcMain: { handle: (id, fn) => handlers.set(id, fn) },
      shell: { showItemInFolder: (file) => { revealed = file; } },
      dialog: { showSaveDialog: async () => ({ canceled: true }) },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { registerResultFiles } = require("../src/media-files");
  Module._load = originalLoad;
  global.fetch = async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8787/api/admin/tasks/task-1/result");
    assert.equal(options.headers.Authorization, "Bearer fixture-admin-session-token");
    assert.equal(options.redirect, "error");
    return { ok: allowed, headers: new Headers({ "content-disposition": `attachment; filename="${filename}"` }), body: { cancel: async () => { cancelled++; } } };
  };
  registerResultFiles({ getWindow: () => window, baseURL: "http://127.0.0.1:8787", getResultDir: () => root });
  const reveal = handlers.get("zaowu-provider:media:reveal");
  const token = "fixture-admin-session-token";
  await assert.rejects(reveal({ sender: {}, senderFrame: frame }, "task-1", token), /管理客户端/);
  await assert.rejects(reveal(event, "../../secret", token), /请求无效/);
  await assert.rejects(reveal(event, "task-1", ""), /请求无效/);
  await reveal(event, "task-1", token);
  assert.equal(revealed, await fs.realpath(result));
  assert.equal(cancelled, 1);
  assert.deepEqual(await handlers.get("zaowu-provider:media:save-as")(event, "task-1", token), { saved: false });
  allowed = false;
  await assert.rejects(reveal(event, "task-1", token), /会话已失效/);
  allowed = true;
  filename = "task-2.png";
  await assert.rejects(reveal(event, "task-1", token), /标识无效/);
  filename = "task-1.png";
  await fs.unlink(result);
  await assert.rejects(reveal(event, "task-1", token), /不存在/);
});
