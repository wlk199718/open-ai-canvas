const fs = require("node:fs");
const path = require("node:path");
const { execFile, spawn } = require("node:child_process");
const { promisify } = require("node:util");
const run = promisify(execFile);

async function providerExecutable() {
  const configured = process.env.ZAOWU_PROVIDER_CLIENT_EXE;
  if (configured && fs.existsSync(configured)) return path.resolve(configured);
  try {
    const { stdout } = await run("reg.exe", ["query", "HKCU\\Software\\Zaowu\\Provider", "/v", "InstallDir"], {
      encoding: "utf8", windowsHide: true, timeout: 5000,
    });
    const match = stdout.match(/InstallDir\s+REG_SZ\s+([^\r\n]+)/);
    if (match) {
      const executable = path.join(match[1].trim(), "造物生成服务.exe");
      if (fs.existsSync(executable)) return executable;
    }
  } catch {
    // The provider is optional until its standalone client is installed.
  }
  return null;
}

async function openProvider(background = false) {
  const executable = await providerExecutable();
  if (!executable) {
    if (background) return { opened: false };
    throw new Error("尚未安装造物生成服务客户端，请先安装后连接");
  }
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  await new Promise((resolve, reject) => {
    const child = spawn(executable, background ? ["--background"] : [], {
      detached: true, stdio: "ignore", windowsHide: true, env,
    });
    child.once("error", () => reject(new Error("无法启动造物生成服务客户端")));
    child.once("spawn", () => { child.unref(); resolve(); });
  });
  return { opened: true };
}

async function importProviderConnection() {
  const executable = await providerExecutable();
  if (!executable) return null;
  const helper = path.join(path.dirname(executable), "resources", "bundle", "zaowu-provider", "zaowu-provider.exe");
  if (!fs.existsSync(helper)) return null;
  try {
    const { stdout } = await run(helper, ["--desktop-connection"], {
      encoding: "utf8", windowsHide: true, timeout: 10000, maxBuffer: 16384,
    });
    return JSON.parse(stdout);
  } catch {
    // Never propagate child-process diagnostics: stdout can contain a token.
    return null;
  }
}

async function stopProviderForStorage() {
  let response;
  try {
    response = await fetch("http://127.0.0.1:8787/api/health", { signal: AbortSignal.timeout(2000) });
  } catch { return; }
  if (!response.ok) throw new Error("无法确认生成服务状态");
  const health = await response.json();
  if (health.service !== "zaowu" || !Number.isInteger(health.active_tasks)) {
    throw new Error("请先更新生成服务客户端，再应用保存位置");
  }
  if (health.active_tasks > 0) throw new Error("生成服务还有任务正在执行，请结束后再应用保存位置");
  const executable = await providerExecutable();
  if (!executable) throw new Error("找不到生成服务客户端，请先退出正在运行的服务");
  const environment = { ...process.env };
  delete environment.ELECTRON_RUN_AS_NODE;
  await run(executable, ["--quit-for-storage"], {
    windowsHide: true, timeout: 20000, env: environment,
  });
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      await fetch("http://127.0.0.1:8787/api/health", { signal: AbortSignal.timeout(1000) });
    } catch { return; }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("生成服务未退出，请等待任务结束后重试");
}

module.exports = { providerExecutable, openProvider, importProviderConnection, stopProviderForStorage };
