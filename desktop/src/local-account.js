// 单用户免登录：首次启动自动创建本地管理员账号，凭据用系统钥匙串加密保存。
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { safeStorage } = require("electron");

const USERNAME = "local";

function loadOrCreatePassword(file) {
  if (fs.existsSync(file)) {
    const raw = fs.readFileSync(file);
    return safeStorage.isEncryptionAvailable() ? safeStorage.decryptString(raw) : raw.toString("utf8");
  }
  const password = crypto.randomBytes(24).toString("base64url");
  const data = safeStorage.isEncryptionAvailable() ? safeStorage.encryptString(password) : Buffer.from(password, "utf8");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, data, { mode: 0o600 });
  return password;
}

async function post(baseURL, route, body) {
  const res = await fetch(`${baseURL}/api${route}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: baseURL },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  return { res, json };
}

function sessionCookieValue(res) {
  const all = typeof res.headers.getSetCookie === "function" ? res.headers.getSetCookie() : [res.headers.get("set-cookie") || ""];
  for (const line of all) {
    const [pair] = line.split(";");
    const idx = pair.indexOf("=");
    if (idx > 0 && pair.slice(0, idx).trim() === "open_ai_canvas_session") return pair.slice(idx + 1).trim();
  }
  return "";
}

// 返回会话 cookie 值；失败时返回空串，界面会回到正常登录页。
async function ensureLocalSession(baseURL, credentialFile) {
  const password = loadOrCreatePassword(credentialFile);
  let { res, json } = await post(baseURL, "/auth/login", { username: USERNAME, password });
  if (res.ok && json.code === 0) return sessionCookieValue(res);
  ({ res, json } = await post(baseURL, "/auth/register", {
    username: USERNAME,
    displayName: "本地用户",
    password,
    acceptedTerms: true,
  }));
  if (res.ok && json.code === 0) return sessionCookieValue(res);
  console.warn("本地账号自动登录失败：", json.msg || res.status);
  return "";
}

module.exports = { ensureLocalSession };
