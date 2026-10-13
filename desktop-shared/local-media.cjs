const fs = require("node:fs/promises");
const path = require("node:path");

function inside(root, target) {
  const relative = path.relative(root, target);
  return relative !== "" && !path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`);
}

async function localMediaFile(root, key) {
  if (typeof key !== "string" || !key || path.isAbsolute(key) || key.includes("\0")) throw new Error("本地文件标识无效");
  const target = path.resolve(root, key);
  if (!inside(path.resolve(root), target)) throw new Error("文件不在媒体保存目录中");
  try {
    const [realRoot, realTarget] = await Promise.all([fs.realpath(root), fs.realpath(target)]);
    if (!inside(realRoot, realTarget)) throw new Error("文件不在媒体保存目录中");
    if (!(await fs.stat(realTarget)).isFile()) throw new Error("媒体文件不存在");
    return realTarget;
  } catch (error) {
    if (error.code === "ENOENT") throw new Error("本地媒体文件不存在，请检查保存位置或文件是否已被移动");
    throw error;
  }
}

async function saveMediaCopy({ file, name, window, dialog }) {
  const extension = path.extname(file);
  const suggested = path.basename(typeof name === "string" && name.trim() ? name.trim() : file).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_");
  const filename = `${path.basename(suggested, path.extname(suggested)) || "生成结果"}${extension}`;
  const selected = await dialog.showSaveDialog(window, {
    title: "另存为", defaultPath: path.join(path.dirname(file), filename),
    ...(extension ? { filters: [{ name: "媒体文件", extensions: [extension.slice(1)] }] } : {}),
  });
  if (selected.canceled || !selected.filePath) return { saved: false };
  if (path.resolve(selected.filePath) === path.resolve(file)) return { saved: true };
  await fs.copyFile(file, selected.filePath);
  return { saved: true };
}

module.exports = { localMediaFile, saveMediaCopy };
