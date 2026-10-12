// 后端子进程管理：选端口、注入环境变量、等待就绪、优雅退出。
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function backendPort(dataDir) {
  const portFile = path.join(dataDir, "desktop-port.json");
  if (fs.existsSync(portFile)) {
    const saved = JSON.parse(fs.readFileSync(portFile, "utf8"));
    if (!Number.isInteger(saved.port) || saved.port < 1024 || saved.port > 65535) {
      throw new Error("桌面本地地址配置无效");
    }
    return saved.port;
  }
  // 浏览器缓存按 origin 隔离。沿用升级前最新的 IndexedDB 地址，
  // 避免随机端口让已有创作记录和素材缓存在重启后变得不可见。
  const indexedDB = path.join(path.dirname(dataDir), "IndexedDB");
  const candidates = fs.existsSync(indexedDB)
    ? fs.readdirSync(indexedDB).flatMap((name) => {
        const match = /^http_127\.0\.0\.1_(\d+)\.indexeddb\.leveldb$/.exec(name);
        return match ? [{ port: Number(match[1]), modified: fs.statSync(path.join(indexedDB, name)).mtimeMs }] : [];
      }).sort((a, b) => b.modified - a.modified)
    : [];
  const port = candidates.length ? candidates[0].port : await freePort();
  fs.writeFileSync(portFile, JSON.stringify({ port }), { mode: 0o600, flag: "wx" });
  return port;
}

async function waitReady(baseURL, child, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`后端提前退出，退出码 ${child.exitCode}`);
    try {
      const res = await fetch(`${baseURL}/api/health/startup`);
      if (res.ok) return;
    } catch {
      // 尚未监听，继续等待
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error("等待后端就绪超时");
}

async function startBackend({ bundleDir, dataDir, logDir, resourceDir }) {
  const exe = process.platform === "win32" ? ".exe" : "";
  const bin = path.join(bundleDir, "bin", `zaowu-backend${exe}`);
  if (!fs.existsSync(bin)) throw new Error(`找不到后端程序：${bin}`);
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });

  const port = await backendPort(dataDir);
  const baseURL = `http://127.0.0.1:${port}`;
  const ffmpegDir = path.join(bundleDir, "ffmpeg");
  const pathParts = [process.env.PATH || ""];
  if (fs.existsSync(ffmpegDir)) pathParts.unshift(ffmpegDir);

  const env = {
    ...process.env,
    PATH: pathParts.join(path.delimiter),
    CANVAS_BACKEND_ADDR: `127.0.0.1:${port}`,
    CANVAS_BACKEND_DATA_DIR: dataDir,
    CANVAS_RESOURCE_DIR: resourceDir || path.join(dataDir, "resources"),
    CANVAS_DATABASE_DRIVER: "sqlite",
    CANVAS_WEB_DIR: path.join(bundleDir, "web"),
    CANVAS_PI_RUNTIME_DIR: path.join(bundleDir, "agent-runtime", "pi"),
    CANVAS_OFFICIAL_PLUGIN_DIR: path.join(bundleDir, "plugin-packages"),
    CANVAS_CORS_ORIGINS: baseURL,
    CANVAS_ALLOWED_PRIVATE_UPSTREAM_HOSTS: ["127.0.0.1", ...(process.env.CANVAS_ALLOWED_PRIVATE_UPSTREAM_HOSTS || "").split(",")].filter(Boolean).join(","),
    CANVAS_REGISTRATION_ENABLED: "false",
    CANVAS_SHUTDOWN_ON_STDIN_EOF: "1",
    CANVAS_DESKTOP_FFMPEG: "1",
    // Agent 运行时直接复用 Electron 内置 Node
    CANVAS_NODE_BIN: process.execPath,
    ELECTRON_RUN_AS_NODE: "1",
    GIN_MODE: "release",
    CANVAS_LOG_LEVEL: process.env.CANVAS_LOG_LEVEL || "info",
  };

  // 日志直接交给子进程的文件句柄，主进程先退出也不会丢最后几行
  const logFd = fs.openSync(path.join(logDir, "backend.log"), "a");
  const child = spawn(bin, [], { env, cwd: dataDir, stdio: ["pipe", logFd, logFd], windowsHide: true });
  fs.closeSync(logFd);

  await waitReady(baseURL, child);
  return { child, baseURL, stop: (graceMs) => stopBackend(child, graceMs) };
}

// 关闭 stdin 触发后端优雅退出（Windows 无法发送 SIGTERM），超时后强杀。
function stopBackend(child, graceMs = 15000) {
  return new Promise((resolve) => {
    if (!child || child.exitCode !== null) return resolve();
    const timer = setTimeout(() => {
      child.kill();
      resolve();
    }, graceMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    child.stdin.end();
  });
}

module.exports = { startBackend, backendPort };
