const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function fixture(t, error = null) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zaowu-admin-session-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const handlers = new Map();
  const frame = { url: "http://127.0.0.1:8787/" };
  const contents = { mainFrame: frame };
  const event = { sender: contents, senderFrame: frame };
  let calls = 0;
  const value = { activated: true, token: "fixture-management-token-123456" };
  const context = {
    module: { exports: {} },
    require: (name) => {
      if (name === "node:child_process") return {
        execFile: (_file, _args, _options, callback) => {
          calls += 1;
          if (error) callback(error);
          else callback(null, JSON.stringify(value), "");
        },
      };
      if (name === "node:util") return {
        promisify: (fn) => (...args) => new Promise((resolve, reject) => {
          fn(...args, (err, stdout) => err ? reject(err) : resolve({ stdout }));
        }),
      };
      return require(name);
    },
    URL,
    process: { env: {} },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../src/admin-session.js"), "utf8"), context);
  context.module.exports.registerAdminSession({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    userData: root, bundleDir: root, getWindow: () => ({ webContents: contents }),
    baseURL: "http://127.0.0.1:8787",
  });
  return { handlers, event, calls: () => calls };
}

test("only the trusted main frame can restore the management session", async (t) => {
  const f = fixture(t);
  const restore = f.handlers.get("zaowu-provider:restore-session");
  await assert.rejects(restore({ ...f.event, senderFrame: { url: "http://127.0.0.1:8787/" } }), /仅允许/);
  await assert.rejects(restore({ ...f.event, senderFrame: { url: "https://example.com/" } }), /仅允许/);
  assert.equal(f.calls(), 0);
  assert.equal((await restore(f.event)).activated, true);
});

test("explicit logout disables automatic login until manual activation", async (t) => {
  const f = fixture(t);
  const preference = f.handlers.get("zaowu-provider:automatic-login");
  const restore = f.handlers.get("zaowu-provider:restore-session");
  preference(f.event, false);
  assert.equal(await restore(f.event), null);
  assert.equal(f.calls(), 0);
  preference(f.event, true);
  assert.equal((await restore(f.event)).activated, true);
});

test("failed helper diagnostics cannot leak a session token or card", async (t) => {
  const f = fixture(t, new Error("secret-card-and-token"));
  await assert.rejects(f.handlers.get("zaowu-provider:restore-session")(f.event), (error) =>
    error.message.includes("已保存卡密未能验证") && !error.message.includes("secret-card-and-token"));
});
