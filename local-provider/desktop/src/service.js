const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const baseURL = "http://127.0.0.1:8787";

async function health() {
  try {
    const response = await fetch(`${baseURL}/api/health`, { signal: AbortSignal.timeout(1500) });
    if (!response.ok) return null;
    const value = await response.json();
    if (value.service !== "zaowu" || value.api_version !== "v1") {
      throw new Error("8787 端口正在运行其他服务");
    }
    return value;
  } catch (error) {
    if (error.message === "8787 端口正在运行其他服务") throw error;
    return null;
  }
}

async function startService({ bundleDir, logDir, resultDir }) {
  if (await health()) return { baseURL, owned: false, stop: async () => {} };
  const executable = path.join(bundleDir, "zaowu-provider", "zaowu-provider.exe");
  if (!fs.existsSync(executable)) throw new Error("缺少本地生成服务程序，请重新安装客户端");
  fs.mkdirSync(logDir, { recursive: true });
  const log = fs.openSync(path.join(logDir, "service.log"), "a");
  const env = { ...process.env, ZAOWU_PROVIDER_OPEN_BROWSER: "0", ZAOWU_PROVIDER_HOST: "127.0.0.1", ZAOWU_PROVIDER_PORT: "8787" };
  if (resultDir) env.ZAOWU_PROVIDER_RESULT_DIR = resultDir;
  delete env.ZAOWU_PROVIDER_DEV_MODE;
  delete env.ZAOWU_PROVIDER_ALLOW_DEV_MODE;
  delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(executable, ["--desktop-service"], {
    cwd: bundleDir, env, stdio: ["pipe", log, log], windowsHide: true,
  });
  fs.closeSync(log);
  let spawnError = null;
  child.on("error", (error) => { spawnError = error; });
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (spawnError) throw new Error(`生成服务启动失败：${spawnError.message}`);
    if (child.exitCode !== null) throw new Error("生成服务提前退出，请查看客户端日志");
    if (await health()) return { baseURL, child, owned: true, stop: () => stopService(child) };
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  await stopService(child);
  throw new Error("生成服务启动超时，请查看客户端日志");
}

function stopService(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    const timer = setTimeout(() => { child.kill(); resolve(); }, 15000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
    child.stdin.end();
  });
}

module.exports = { startService, health };
