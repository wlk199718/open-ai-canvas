const assert = require("node:assert/strict");
const Module = require("node:module");
const test = require("node:test");
const { promisify } = require("node:util");

test("storage restart launches Electron without Node mode and refuses active provider tasks", async (t) => {
  const originalLoad = Module._load;
  const originalFetch = global.fetch;
  const previous = process.env.ELECTRON_RUN_AS_NODE;
  process.env.ELECTRON_RUN_AS_NODE = "1";
  let active = 1;
  let calls = 0;
  let stopped = false;
  const childProcess = {
    execFile(executable, args, options, callback) {
      calls++;
      if (executable === "reg.exe") {
        callback(null, "InstallDir    REG_SZ    D:\\provider", "");
      } else {
        assert.equal(executable, "D:\\provider\\造物生成服务.exe");
        assert.deepEqual(args, ["--quit-for-storage"]);
        assert.ok(!Object.hasOwn(options.env, "ELECTRON_RUN_AS_NODE"));
        stopped = true;
        callback(null, "", "");
      }
    },
    spawn() { throw new Error("Unexpected spawn"); },
  };
  childProcess.execFile[promisify.custom] = (executable, args, options) => new Promise((resolve, reject) => {
    childProcess.execFile(executable, args, options, (error, stdout, stderr) => {
      if (error) reject(error);
      else resolve({ stdout, stderr });
    });
  });
  Module._load = function (request, parent, isMain) {
    if (request === "node:child_process") return childProcess;
    if (request === "node:fs") return { existsSync: () => true };
    return originalLoad.call(this, request, parent, isMain);
  };
  delete require.cache[require.resolve("../src/provider-client")];
  const client = require("../src/provider-client");
  Module._load = originalLoad;
  global.fetch = async () => {
    if (stopped) throw new Error("Closed");
    return { ok: true, json: async () => ({ service: "zaowu", active_tasks: active }) };
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (previous === undefined) delete process.env.ELECTRON_RUN_AS_NODE;
    else process.env.ELECTRON_RUN_AS_NODE = previous;
  });
  await assert.rejects(client.stopProviderForStorage(), /任务正在执行/);
  assert.equal(calls, 0);
  active = 0;
  await client.stopProviderForStorage();
  assert.ok(stopped);
});
