const { createHash } = require("node:crypto");

const TEXT_PROTOCOLS = new Set(["chat-completion", "openai-response", "claude-api", "gemini-generate-content"]);
const hash = (value) => createHash("sha256").update(value).digest("hex").slice(0, 24);

// Serialize admission configuration, including retries after a partial save.
// The reserved name recovers the mapping without storing keys or a second ID database.
function createTextModelManager() {
  let tail = Promise.resolve();
  return (request, input) => {
    const work = tail.then(() => sync(request, input));
    tail = work.catch(() => {});
    return work;
  };
}

async function sync(request, input) {
  if (!input || typeof input.userId !== "string" || !input.userId ||
      typeof input.sourceId !== "string" || !input.sourceId || input.sourceId.length > 200 ||
      typeof input.model !== "string" || !input.model.trim() || input.model.length > 160 ||
      typeof input.baseUrl !== "string" || input.baseUrl.length > 2048 ||
      typeof input.apiKey !== "string" || !input.apiKey.trim() || input.apiKey.length > 16384 ||
      input.enabled === false || !TEXT_PROTOCOLS.has(input.protocol)) {
    throw new Error("请先在个人渠道填写有效的文本模型地址、密钥和请求协议");
  }
  if (!input.capabilityConfig?.text) throw new Error("文本模型缺少能力参数，请更新桌面端或检查模型设置");
  const session = await request("GET", "/auth/session");
  if (session.user?.id !== input.userId || session.user?.username !== "local" ||
      session.user?.role !== "admin" || session.user?.status !== "active") {
    throw new Error("仅允许当前桌面本地管理员同步智能体模型，请重新打开客户端");
  }
  const name = `造物桌面智能体/${hash(JSON.stringify([session.user.id, input.sourceId]))}`;
  const page = await request("GET", `/admin/channels?keyword=${encodeURIComponent(name)}&limit=100`);
  const matches = page.channels.filter((channel) => channel.name === name);
  if (matches.length > 1) throw new Error("智能体渠道配置重复，请检查本地模型配置");
  let channel = matches[0];
  if (channel && (channel.userId !== session.user.id || channel.scope !== "system")) {
    throw new Error("智能体渠道归属不匹配，请检查本地模型配置");
  }
  const connection = {
    name, baseUrl: input.baseUrl, apiKey: input.apiKey.trim(),
    headers: input.headers || [], enabled: true, useGlobalConcurrency: true,
  };
  if (channel) {
    ({ channel } = await request("PATCH", `/admin/channels/${encodeURIComponent(channel.id)}`, connection));
  } else {
    ({ channel } = await request("POST", "/admin/channels", { ...connection, models: [] }));
  }
  const root = `/admin/channels/${encodeURIComponent(channel.id)}/models`;
  const { models } = await request("GET", root);
  const existing = models.find((model) => model.modelKey === input.model.trim());
  const modelRequest = {
    modelKey: input.model.trim(), providerModelKey: input.model.trim(),
    displayName: input.model.trim(), channelLabel: String(input.name || "").slice(0, 80),
    capability: "text", protocol: input.protocol, billingMode: "fixed_request",
    unitPriceMicrocredits: 0, inputTokenPriceMicrocredits: 0,
    outputTokenPriceMicrocredits: 0, cachedTokenPriceMicrocredits: 0,
    priceConfigured: true, enabled: true,
    capabilityConfig: input.capabilityConfig,
  };
  await request(existing ? "PATCH" : "POST", existing ? `${root}/${encodeURIComponent(existing.id)}` : root, modelRequest);
  return { channelId: channel.id, channelModelKey: modelRequest.modelKey };
}

module.exports = { createTextModelManager };
