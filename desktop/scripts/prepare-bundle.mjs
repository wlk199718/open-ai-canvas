// 把后端、前端、Agent 运行时、官方插件包、ffmpeg 收集到 desktop/bundle，供开发运行与打包使用。
// 用法：node scripts/prepare-bundle.mjs   （FFMPEG_DIR 可指定 ffmpeg 所在目录）
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync } from "node:fs";
import { createHash } from "node:crypto";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const desktopDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(desktopDir, "..");
const bundle = join(desktopDir, "bundle");
const exe = process.platform === "win32" ? ".exe" : "";

function need(path, hint) {
  if (!existsSync(path)) {
    console.error(`缺少 ${path}\n  ${hint}`);
    process.exit(1);
  }
}

const backendBin = join(repo, "desktop-build", `zaowu-backend${exe}`);
need(backendBin, "先在 backend 目录执行：CGO_ENABLED=0 go build -trimpath -ldflags \"-s -w\" -o ../desktop-build/zaowu-backend.exe ./cmd/server");
need(join(repo, "web", "dist", "index.html"), "先在 web 目录执行：VITE_DESKTOP=1 bunx vite build");
// GPL 守卫：桌面版不得携带 @ffmpeg/core（GPL）的 wasm，必须用 VITE_DESKTOP=1 构建前端
const gplWasm = readdirSync(join(repo, "web", "dist", "assets")).filter((f) => /^ffmpeg-core.*\.(wasm|js)$/i.test(f));
if (gplWasm.length && process.env.ALLOW_GPL_FFMPEG !== "1") {
  console.error(`web/dist 含 GPL 授权的 ffmpeg.wasm（${gplWasm.join(", ")}），请在 web 目录用 VITE_DESKTOP=1 bunx vite build 重新构建`);
  process.exit(1);
}

// 不做大目录删除（部分环境会把删除重定向到回收站并超时）：旧 bundle 改名移入 .trash，需要时手动清理
if (existsSync(bundle)) {
  const trash = join(desktopDir, ".trash");
  mkdirSync(trash, { recursive: true });
  renameSync(bundle, join(trash, `bundle-${Date.now()}`));
  console.log("旧 bundle 已移入 desktop/.trash，可手动清理");
}
mkdirSync(join(bundle, "bin"), { recursive: true });

cpSync(backendBin, join(bundle, "bin", `zaowu-backend${exe}`));
cpSync(join(repo, "web", "dist"), join(bundle, "web"), { recursive: true });

// Agent 运行时：生产依赖按 lockfile 哈希缓存安装，再过滤复制进 bundle
const piSrc = join(repo, "backend", "agent-runtime", "pi");
const piDst = join(bundle, "agent-runtime", "pi");
const lockHash = createHash("sha256").update(readFileSync(join(piSrc, "package-lock.json"))).digest("hex").slice(0, 12);
const piCache = join(desktopDir, ".cache", `pi-${lockHash}`);
if (!existsSync(join(piCache, "node_modules"))) {
  mkdirSync(piCache, { recursive: true });
  for (const f of ["package.json", "package-lock.json"]) cpSync(join(piSrc, f), join(piCache, f));
  execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], {
    cwd: piCache,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
}
mkdirSync(piDst, { recursive: true });
for (const f of ["agent-runtime.mjs", "package.json", "package-lock.json"]) cpSync(join(piSrc, f), join(piDst, f));
// 瘦身：跳过运行时不会读取的文件（source map、类型声明、@types）。
// 注意 pi-coding-agent 运行时会解析自身 docs/、examples/ 目录，必须保留。
const skipRuntimeFile = (src) => {
  const name = basename(src);
  return name !== "@types" && !/\.(map|d\.ts|d\.mts|d\.cts)$/.test(name);
};
cpSync(join(piCache, "node_modules"), join(piDst, "node_modules"), { recursive: true, filter: skipRuntimeFile });

// 官方插件：后端只读取 *.yingce-plugin 包，源码目录不打进安装包
const pluginDst = join(bundle, "plugin-packages");
mkdirSync(pluginDst, { recursive: true });
for (const name of readdirSync(join(repo, "plugin-packages"))) {
  if (name.toLowerCase().endsWith(".yingce-plugin")) cpSync(join(repo, "plugin-packages", name), join(pluginDst, name));
}

// ffmpeg / ffprobe 及其动态库
const ffmpegDir = process.env.FFMPEG_DIR;
if (ffmpegDir && existsSync(join(ffmpegDir, `ffmpeg${exe}`))) {
  // 分发合规：GPL 构建（含 libx264 等）会让整个安装包受 GPL 约束，默认拒绝
  // stdin 必须 ignore：部分 Windows 终端下继承 stdin 会让 spawnSync 报 EBUSY
  const version = execFileSync(join(ffmpegDir, `ffmpeg${exe}`), ["-hide_banner", "-version"], {
    encoding: "utf8",
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (/--enable-(gpl|nonfree)/.test(version) && process.env.ALLOW_GPL_FFMPEG !== "1") {
    console.error("FFMPEG_DIR 指向 GPL/nonfree 构建，请改用 LGPL 构建（或设置 ALLOW_GPL_FFMPEG=1 仅供内部使用）");
    process.exit(1);
  }
  const ffDst = join(bundle, "ffmpeg");
  mkdirSync(ffDst, { recursive: true });
  for (const name of readdirSync(ffmpegDir)) {
    if (name.startsWith("ffplay")) continue;
    if (name.startsWith("ffmpeg") || name.startsWith("ffprobe") || name.endsWith(".dll") || name.endsWith(".dylib") || name.includes(".so")) {
      cpSync(join(ffmpegDir, name), join(ffDst, name));
    }
  }
} else {
  console.warn("未设置 FFMPEG_DIR，安装包不含 ffmpeg；运行时将使用系统 PATH 中的 ffmpeg。");
}

console.log(`bundle 已生成：${bundle}`);
