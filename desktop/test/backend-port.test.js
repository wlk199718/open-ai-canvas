const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { backendPort } = require("../src/backend");

function workspace(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zaowu-port-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const data = path.join(root, "data");
  fs.mkdirSync(data);
  return { root, data };
}

test("desktop keeps the same browser origin across restarts", async (t) => {
  const { data } = workspace(t);
  const first = await backendPort(data);
  assert.ok(first >= 1024 && first <= 65535);
  assert.equal(await backendPort(data), first);
});

test("upgrade preserves the last existing browser cache origin", async (t) => {
  const { root, data } = workspace(t);
  const cache = path.join(root, "IndexedDB");
  fs.mkdirSync(cache);
  const old = path.join(cache, "http_127.0.0.1_7094.indexeddb.leveldb");
  const latest = path.join(cache, "http_127.0.0.1_6805.indexeddb.leveldb");
  fs.mkdirSync(old);
  fs.mkdirSync(latest);
  fs.utimesSync(old, 1000, 1000);
  fs.utimesSync(latest, 2000, 2000);
  assert.equal(await backendPort(data), 6805);
  fs.utimesSync(old, 3000, 3000);
  assert.equal(await backendPort(data), 6805);
});

test("invalid saved port fails instead of silently losing the cache origin", async (t) => {
  const { data } = workspace(t);
  fs.writeFileSync(path.join(data, "desktop-port.json"), '{"port":0}');
  await assert.rejects(backendPort(data), /配置无效/);
});
