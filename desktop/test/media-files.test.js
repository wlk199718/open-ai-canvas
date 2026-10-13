const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const { localMediaFile, saveMediaCopy } = require("../../desktop-shared/local-media.cjs");

test("local media requires an existing file inside the resolved root and rejects junction escapes", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "zaowu-media-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const media = path.join(root, "media");
  const outside = path.join(root, "outside");
  await fs.mkdir(media);
  await fs.mkdir(outside);
  await fs.writeFile(path.join(media, "video.mp4"), "fixture-video");
  await fs.writeFile(path.join(outside, "image.png"), "fixture-image");
  assert.equal(await localMediaFile(media, "video.mp4"), await fs.realpath(path.join(media, "video.mp4")));
  await assert.rejects(localMediaFile(media, "../outside/image.png"), /保存目录/);
  await assert.rejects(localMediaFile(media, path.join(outside, "image.png")), /标识无效/);
  await assert.rejects(localMediaFile(media, "missing.png"), /不存在/);
  await fs.symlink(outside, path.join(media, "linked"), process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(localMediaFile(media, "linked/image.png"), /保存目录/);
});

test("save as copies the original bytes, cancellation keeps files unchanged and same-path save is harmless", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "zaowu-copy-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const file = path.join(root, "original.mp4");
  const target = path.join(root, "copy.mp4");
  await fs.writeFile(file, Buffer.from([0, 3, 5, 255]));
  let selected = { canceled: true };
  const dialog = { showSaveDialog: async (_window, options) => {
    assert.equal(path.extname(options.defaultPath), ".mp4");
    return selected;
  } };
  assert.deepEqual(await saveMediaCopy({ file, dialog }), { saved: false });
  selected = { canceled: false, filePath: target };
  assert.deepEqual(await saveMediaCopy({ file, name: "test.png", dialog }), { saved: true });
  assert.deepEqual(await fs.readFile(target), await fs.readFile(file));
  selected = { canceled: false, filePath: file };
  assert.deepEqual(await saveMediaCopy({ file, dialog }), { saved: true });
  assert.deepEqual(await fs.readFile(file), Buffer.from([0, 3, 5, 255]));
});

test("media IPC enforces sender and backend ownership before reveal or copy", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "zaowu-media-ipc-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "user"));
  const local = path.join(root, "user", "image.png");
  await fs.writeFile(local, "fixture-image");
  const frame = { url: "http://127.0.0.1:7094/canvas" };
  const window = { webContents: { mainFrame: frame } };
  const event = { sender: window.webContents, senderFrame: frame };
  const handlers = new Map();
  let resource = { provider: "local", objectKey: "user/image.png", status: "ready" };
  let allowed = true;
  let revealed;
  const originalLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return {
      app: { isPackaged: false },
      ipcMain: { handle: (name, fn) => handlers.set(name, fn) },
      session: { defaultSession: { fetch: async () => ({ ok: allowed, json: async () => ({ code: allowed ? 0 : 1, data: { resource } }) }) } },
      shell: { showItemInFolder: (file) => { revealed = file; } },
      dialog: { showSaveDialog: async () => ({ canceled: true }) },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { registerMediaFiles } = require("../src/media-files");
  Module._load = originalLoad;
  registerMediaFiles({ getWindow: () => window, getBackend: () => ({ baseURL: "http://127.0.0.1:7094" }), resourceDir: root });
  const inspect = handlers.get("zaowu:media:inspect");
  await assert.rejects(inspect({ sender: {}, senderFrame: frame }, "image-1"), /主窗口/);
  await assert.rejects(inspect({ sender: window.webContents, senderFrame: { ...frame } }, "image-1"), /主窗口/);
  frame.url = "https://example.com";
  await assert.rejects(inspect(event, "image-1"), /主窗口/);
  frame.url = "http://127.0.0.1:7094/canvas";
  await assert.rejects(inspect(event, "../../secret"), /标识无效/);
  assert.deepEqual(await inspect(event, "image-1"), { local: true });
  await handlers.get("zaowu:media:reveal")(event, "image-1");
  assert.equal(revealed, await fs.realpath(local));
  assert.deepEqual(await handlers.get("zaowu:media:save-as")(event, "image-1", "name.png"), { saved: false });
  allowed = false;
  await assert.rejects(inspect(event, "another-user"), /不可访问/);
  allowed = true;
  resource = { ...resource, provider: "oss" };
  assert.deepEqual(await inspect(event, "remote-1"), { local: false });
  await assert.rejects(handlers.get("zaowu:media:reveal")(event, "remote-1"), /先下载/);
  resource = { ...resource, provider: "local", objectKey: "../escape" };
  await assert.rejects(inspect(event, "image-1"), /保存目录/);
});
