const assert = require("node:assert/strict");
const test = require("node:test");
const { createTextModelManager } = require("../src/managed-text-models");

const input = {
  userId: "local-user", sourceId: "personal-gpt", name: "GPT",
  baseUrl: "https://example.com/v1", apiKey: "fixture-key",
  model: "fixture-text", protocol: "openai-response",
  capabilityConfig: { version: 1, text: { streaming: true, contextWindowTokens: 128000, maxOutputTokens: 16384,
    references: { promptMaxChars: 100000, maxImages: 0, maxImageBytes: 0, maxVideos: 0, maxVideoBytes: 0 } } },
};
function backend() {
  const channels = [], models = [], calls = [];
  let failModel = false;
  let user = { id: "local-user", username: "local", role: "admin", status: "active" };
  const request = async (method, route, body) => {
    calls.push({ method, route, body });
    if (route === "/auth/session") return { user };
    if (route.startsWith("/admin/channels?")) return { channels };
    if (route === "/admin/channels") {
      const channel = { ...body, id: `channel-${channels.length}`, userId: user.id, scope: "system" };
      channels.push(channel);
      return { channel };
    }
    const channelId = route.split("/")[3];
    if (!route.includes("/models")) {
      const channel = channels.find((item) => item.id === channelId);
      Object.assign(channel, body);
      return { channel };
    }
    if (method === "GET") return { models: models.filter((item) => item.channelId === channelId) };
    if (failModel) { failModel = false; throw new Error("model-save-failed"); }
    if (method === "PATCH") Object.assign(models.find((item) => item.id === route.split("/")[5]), body);
    else models.push({ ...body, id: `model-${models.length}`, channelId });
    return { model: models.at(-1) };
  };
  return { request, channels, models, calls, failNextModel: () => { failModel = true; }, setUser: (value) => { user = value; } };
}

test("personal settings become a zero-cost managed text reference, and edits update the same channel", async () => {
  const b = backend(), sync = createTextModelManager();
  const first = await sync(b.request, input);
  const second = await sync(b.request, { ...input, apiKey: "new-fixture-key", baseUrl: "https://new.example.com/v1", protocol: "claude-api" });
  assert.deepEqual(first, second);
  assert.equal(b.channels.length, 1);
  assert.equal(b.channels[0].apiKey, "new-fixture-key");
  assert.equal(b.models.length, 1);
  assert.equal(b.models[0].protocol, "claude-api");
  assert.equal(b.models[0].priceConfigured, true);
  assert.equal(b.models[0].unitPriceMicrocredits, 0);
  assert.equal(b.models[0].enabled, true);
  assert.ok(!JSON.stringify(first).includes("fixture-key"));
});

test("concurrent admission and retry after partial failure reuse the channel and model", async () => {
  const b = backend(), sync = createTextModelManager();
  b.failNextModel();
  await assert.rejects(sync(b.request, input), /model-save-failed/);
  const results = await Promise.all([sync(b.request, input), sync(b.request, input)]);
  assert.deepEqual(results[0], results[1]);
  assert.equal(b.channels.length, 1);
  assert.equal(b.models.length, 1);
});

test("different personal channels and actors cannot share a managed configuration", async () => {
  const b = backend(), sync = createTextModelManager();
  await sync(b.request, input);
  const other = backend();
  other.setUser({ id: "second", username: "local", role: "admin", status: "active" });
  await sync(other.request, { ...input, userId: "second" });
  assert.notEqual(b.channels[0].name, other.channels[0].name);
  b.channels[0].userId = "second";
  await assert.rejects(sync(b.request, input), /归属不匹配/);
});

test("empty credentials, disabled channels, media protocols, wrong user and non-admin are rejected", async () => {
  for (const patch of [{ apiKey: "" }, { enabled: false }, { protocol: "zaowu-local-wan" }, { userId: "another" }]) {
    const b = backend();
    await assert.rejects(createTextModelManager()(b.request, { ...input, ...patch }));
    assert.equal(b.channels.length, 0);
  }
  const b = backend();
  b.setUser({ id: input.userId, username: "local", role: "user", status: "active" });
  await assert.rejects(createTextModelManager()(b.request, input), /本地管理员/);
  assert.equal(b.channels.length, 0);
});
