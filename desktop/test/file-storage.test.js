const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const storage = require("../../desktop-shared/storage.cjs");

test("both clients read one setting; migration preserves backups and rejects conflicts", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zaowu-storage-"));
  const saved = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = path.join(root, "local");
  t.after(() => {
    if (saved === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = saved;
    fs.rmSync(root, { recursive: true, force: true });
  });
  const first = await storage.readStorage(path.join(root, "documents"));
  const legacy = path.join(root, "legacy");
  fs.mkdirSync(path.join(legacy, "users", "fixture"), { recursive: true });
  fs.writeFileSync(path.join(legacy, "users", "fixture", "image.jpg"), "image bytes");
  const prepared = await storage.prepareCanvasStorage(path.join(root, "documents"), legacy);
  const key = path.join("users", "fixture", "image.jpg");
  assert.equal(fs.readFileSync(path.join(prepared.resources, key), "utf8"), "image bytes");
  assert.ok(fs.existsSync(path.join(legacy, key)));
  await storage.recordDirectory("provider", first.results);
  const next = path.join(root, "chosen");
  await storage.saveStorage(next);
  const fromOtherClient = await storage.readStorage(path.join(root, "different-documents"));
  assert.equal(fromOtherClient.root, next);
  const moved = await storage.prepareCanvasStorage(path.join(root, "documents"), legacy);
  assert.equal(fs.readFileSync(path.join(moved.resources, key), "utf8"), "image bytes");
  assert.ok(fs.existsSync(path.join(prepared.resources, key)));
  // Repeated launches do not bring back deleted files from a backup.
  fs.unlinkSync(path.join(moved.resources, key));
  await storage.prepareCanvasStorage(path.join(root, "documents"), legacy);
  assert.ok(!fs.existsSync(path.join(moved.resources, key)));
  const conflict = path.join(root, "conflict");
  fs.mkdirSync(path.join(conflict, "users", "fixture"), { recursive: true });
  fs.writeFileSync(path.join(conflict, key), "different");
  await assert.rejects(storage.copyMediaTree(legacy, conflict), /同名/);
  assert.equal(fs.readFileSync(path.join(conflict, key), "utf8"), "different");
  await assert.rejects(storage.copyMediaTree(legacy, path.join(legacy, "nested")), /相互包含/);
  fs.writeFileSync(path.join(storage.configDirectory(), "storage.json"), "{}");
  await assert.rejects(storage.readStorage(path.join(root, "documents")), /配置版本/);
});

test("copy refuses symbolic links instead of copying outside the media tree", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zaowu-links-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const outside = path.join(root, "outside");
  fs.mkdirSync(source);
  fs.mkdirSync(outside);
  fs.symlinkSync(outside, path.join(source, "linked"), "junction");
  await assert.rejects(storage.copyMediaTree(source, path.join(root, "target")), /链接/);
});
