const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const os = require("node:os");
const { createHash, randomUUID } = require("node:crypto");

function configDirectory() {
  return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), ".local", "share"), "Zaowu");
}

function validateRoot(root) {
  if (typeof root !== "string" || !path.isAbsolute(root) || root.includes("\0")) {
    throw new Error("请选择有效的绝对目录");
  }
  const resolved = path.resolve(root);
  if (resolved === path.parse(resolved).root) throw new Error("请选择磁盘内的文件夹");
  return resolved;
}

async function writeJSON(file, value) {
  await fsp.mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    await fsp.writeFile(temporary, JSON.stringify(value, null, 2), { mode: 0o600, flag: "wx" });
    await fsp.rename(temporary, file);
  } finally {
    await fsp.unlink(temporary).catch(() => {});
  }
}

async function readStorage(documents) {
  const file = path.join(configDirectory(), "storage.json");
  if (!fs.existsSync(file)) {
    await writeJSON(file, { version: 1, root: path.join(documents, "造物文件") });
  }
  const value = JSON.parse(await fsp.readFile(file, "utf8"));
  if (value.version !== 1) throw new Error("文件存储配置版本无效");
  const root = validateRoot(value.root);
  return { root, results: path.join(root, "生成结果"), resources: path.join(root, "项目素材") };
}

async function saveStorage(root) {
  root = validateRoot(root);
  await ensureDirectory(root);
  const probe = path.join(root, `.zaowu-write-${randomUUID()}`);
  try {
    await fsp.writeFile(probe, "", { flag: "wx" });
  } finally {
    await fsp.unlink(probe).catch(() => {});
  }
  await writeJSON(path.join(configDirectory(), "storage.json"), { version: 1, root });
}

async function ensureDirectory(directory) {
  const parent = path.dirname(directory);
  if (parent !== directory) await ensureDirectory(parent);
  if (!fs.existsSync(directory)) {
    await fsp.mkdir(directory).catch((error) => { if (error.code !== "EEXIST") throw error; });
  }
  const stat = await fsp.lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("保存路径不能包含链接或非目录对象");
}

function overlaps(a, b) {
  const relative = path.relative(a, b);
  return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

async function hashFile(file) {
  const hash = createHash("sha256");
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

// Preserve the source as a backup. A conflicting destination must never be
// overwritten, including when a previous interrupted migration is retried.
async function copyMediaTree(source, target) {
  source = path.resolve(source);
  target = path.resolve(target);
  if (source === target) return;
  if (overlaps(source, target) || overlaps(target, source)) throw new Error("新旧素材目录不能相互包含");
  await ensureDirectory(target);
  if (!fs.existsSync(source)) return;
  async function copyDirectory(from, to) {
    const stat = await fsp.lstat(from);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("迁移源不能包含链接或非目录对象");
    await ensureDirectory(to);
    for (const entry of await fsp.readdir(from, { withFileTypes: true })) {
      const oldFile = path.join(from, entry.name);
      const newFile = path.join(to, entry.name);
      if (entry.isDirectory()) {
        await copyDirectory(oldFile, newFile);
      } else if (entry.isFile()) {
        if (fs.existsSync(newFile)) {
          const destination = await fsp.lstat(newFile);
          if (!destination.isFile() || destination.isSymbolicLink() || await hashFile(oldFile) !== await hashFile(newFile)) {
            throw new Error(`目标目录存在不同内容的同名文件：${entry.name}`);
          }
          continue;
        }
        const temporary = path.join(to, `.zaowu-copy-${randomUUID()}.tmp`);
        try {
          await fsp.copyFile(oldFile, temporary, fs.constants.COPYFILE_EXCL);
          if (await hashFile(oldFile) !== await hashFile(temporary)) throw new Error("素材迁移校验失败");
          await fsp.link(temporary, newFile);
          if (await hashFile(oldFile) !== await hashFile(newFile)) throw new Error("素材迁移校验失败");
        } finally {
          await fsp.unlink(temporary).catch(() => {});
        }
      } else {
        throw new Error("迁移源不能包含链接或特殊文件");
      }
    }
  }
  await copyDirectory(source, target);
}

async function activeDirectory(kind, legacy) {
  const file = path.join(configDirectory(), `${kind}-storage.json`);
  if (!fs.existsSync(file)) return legacy;
  return validateRoot(JSON.parse(await fsp.readFile(file, "utf8")).directory);
}

async function recordDirectory(kind, directory) {
  await writeJSON(path.join(configDirectory(), `${kind}-storage.json`), { directory: validateRoot(directory) });
}

async function prepareCanvasStorage(documents, legacy) {
  const storage = await readStorage(documents);
  const previous = await activeDirectory("canvas", legacy);
  await copyMediaTree(previous, storage.resources);
  await recordDirectory("canvas", storage.resources);
  return storage;
}

module.exports = {
  readStorage, saveStorage, prepareCanvasStorage, activeDirectory, recordDirectory,
  copyMediaTree, configDirectory,
};
