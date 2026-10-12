import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
    applyLocalGenerationConnection,
    buildLocalGenerationChannels,
    DEFAULT_LOCAL_GENERATION_URL,
    getLocalGenerationUrl,
    normalizeLocalGenerationUrl,
    restoreLocalGenerationConnection,
    saveLocalGenerationUrl,
    type LocalGenerationConnection,
} from "../src/services/local-generation";
import { createModelChannel, normalizeConfigSnapshot, defaultConfig, useConfigStore } from "../src/stores/use-config-store";

describe("local generation catalog", () => {
    test("uses explicit capability metadata for vendor IDs and preserves supported limits", () => {
        const channels = buildLocalGenerationChannels({ baseUrl: "http://127.0.0.1:8787", token: "fixture-connection-token" }, [
            { provider: "image", id: "3.2_fast_m", name: "Nano Banana 2", ratios: ["1:1", "16:9"], resolutions: ["1080p", "2k"], max_references: 7 },
            { provider: "q4-video", id: "q4", name: "Q4", ratios: ["16:9"], resolutions: ["720p"], max_references: 15, duration_min: 4, duration_max: 10 },
            { provider: "claw-video", id: "claw", name: "Claw", ratios: ["16:9"], max_references: 0, duration_min: 5, duration_max: 15 },
            { provider: "wan-video", id: "dashscope-wan3.0-video", name: "Wan", ratios: ["16:9"], max_references: 9, duration_min: 2, duration_max: 30 },
        ]);
        const config = normalizeConfigSnapshot({ config: { ...defaultConfig, channels } }).config;
        expect(config.imageModels).toHaveLength(1);
        expect(config.videoModels).toHaveLength(3);
        expect(channels[0].modelCosts?.[0].capabilityConfig?.image?.maxOutputs).toBe(1);
        expect(channels[1].modelCosts?.[0].capabilityConfig?.video?.references.maxImages).toBe(15);
        expect(channels[2].modelCosts?.[0].capabilityConfig?.video?.references.maxImages).toBe(0);
        expect(channels[3].modelCosts?.[0].capabilityConfig?.video?.references.minImages).toBe(1);
        expect(channels[1].modelCosts?.[0].capabilityConfig?.video?.operations).toEqual(["text_to_video", "image_to_video", "reference_to_video"]);
        expect(channels[2].modelCosts?.[0].capabilityConfig?.video?.operations).toEqual(["text_to_video"]);
        expect(channels[3].modelCosts?.[0].capabilityConfig?.video?.operations).toEqual(["image_to_video", "reference_to_video"]);
        expect(channels[3].modelCosts?.[0].capabilityConfig?.video?.defaultOperation).toBe("image_to_video");
    });
});

describe("desktop local generation connection", () => {
    const connection: LocalGenerationConnection = { baseUrl: DEFAULT_LOCAL_GENERATION_URL, token: "fixture-encrypted-bridge-token" };
    const models = [{ provider: "image", id: "local-image", name: "本地图片模型", ratios: ["1:1"], max_references: 7 }];
    let originalWindow: PropertyDescriptor | undefined;
    let originalFetch: typeof fetch;
    let originalConfig: ReturnType<typeof useConfigStore.getState>["config"];
    let values: Map<string, string>;
    let requests: Array<{ url: string; init?: RequestInit }>;
    let bridgeReads: number;

    beforeEach(() => {
        originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
        originalFetch = globalThis.fetch;
        originalConfig = useConfigStore.getState().config;
        values = new Map();
        requests = [];
        bridgeReads = 0;
        Object.defineProperty(globalThis, "window", {
            configurable: true,
            value: {
                localStorage: {
                    getItem: (key: string) => values.get(key) ?? null,
                    setItem: (key: string, value: string) => values.set(key, value),
                    removeItem: (key: string) => values.delete(key),
                },
                zaowuDesktop: {
                    getLocalProviderConnection: async () => {
                        bridgeReads += 1;
                        return connection;
                    },
                },
            },
        });
        globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
            requests.push({ url: String(url), init });
            return Response.json({ data: models });
        }) as typeof fetch;
        useConfigStore.getState().replaceConfig(normalizeConfigSnapshot({ config: {
            ...defaultConfig,
            channels: [createModelChannel({ id: "personal", name: "个人渠道", models: ["personal-image"], interfaceType: "openai-image" })],
        } }).config);
    });

    afterEach(() => {
        useConfigStore.getState().replaceConfig(originalConfig);
        globalThis.fetch = originalFetch;
        if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
        else Reflect.deleteProperty(globalThis, "window");
    });

    test("normalizes loopback addresses and rejects other hosts and credentials", () => {
        expect(normalizeLocalGenerationUrl(" http://127.0.0.1:8788/ ")).toBe("http://127.0.0.1:8788");
        for (const address of ["http://localhost:8787", "https://example.com", "http://192.168.1.10", "file:///tmp/test", "http://user:password@127.0.0.1", "invalid"]) {
            expect(() => normalizeLocalGenerationUrl(address)).toThrow("127.0.0.1");
        }
    });

    test("persists only the nonsecret service address and rejects invalid cached addresses", () => {
        expect(getLocalGenerationUrl()).toBe(DEFAULT_LOCAL_GENERATION_URL);
        expect(saveLocalGenerationUrl("http://127.0.0.1:8788/")).toBe("http://127.0.0.1:8788");
        expect(getLocalGenerationUrl()).toBe("http://127.0.0.1:8788");
        values.set("zaowu:local-provider-url", "https://example.com");
        expect(getLocalGenerationUrl()).toBe(DEFAULT_LOCAL_GENERATION_URL);
        expect(() => saveLocalGenerationUrl("https://example.com")).toThrow("127.0.0.1");
    });

    test("restores through the desktop bridge and only fetches the model catalog", async () => {
        await restoreLocalGenerationConnection();
        expect(bridgeReads).toBe(1);
        expect(requests).toHaveLength(1);
        expect(requests[0].url).toBe(`${connection.baseUrl}/api/v1/models`);
        expect(new Headers(requests[0].init?.headers).get("Authorization")).toBe(`Bearer ${connection.token}`);
        expect(requests[0].init?.redirect).toBe("error");
        expect(useConfigStore.getState().config.channels.map((channel) => channel.id)).toEqual(["personal", "zaowu-local-image"]);
        expect(useConfigStore.getState().config.channels[1].apiKey).toBe(connection.token);
        expect([...values.values()].join("\n")).not.toContain(connection.token);
    });

    test("repeated synchronization replaces local channels while preserving personal channels", async () => {
        expect(await applyLocalGenerationConnection(connection)).toBe(1);
        expect(await applyLocalGenerationConnection(connection)).toBe(1);
        expect(useConfigStore.getState().config.channels.map((channel) => channel.id)).toEqual(["personal", "zaowu-local-image"]);
        expect([...values.values()].join("\n")).not.toContain(connection.token);
    });

    test("fresh installation configures local media defaults without adding text models", async () => {
        useConfigStore.getState().replaceConfig({ ...defaultConfig, channels: [] });
        globalThis.fetch = (async () => Response.json({ data: [
            { ...models[0], id: "nano2 lite" },
            { ...models[0], id: "3.2_pro_m", name: "Nano Banana Pro" },
            { provider: "wan-video", id: "wan", name: "Wan", ratios: ["16:9"], max_references: 1, duration_min: 5, duration_max: 10 },
            { provider: "q4-video", id: "q4", name: "Q4", ratios: ["16:9"], max_references: 7, duration_min: 5, duration_max: 10 },
        ] })) as typeof fetch;
        await restoreLocalGenerationConnection();
        const config = useConfigStore.getState().config;
        expect(config.imageModel).toBe("zaowu-local-image::3.2_pro_m");
        expect(config.videoModel).toBe("zaowu-local-q4-video::q4");
        expect(config.textModel).toBe("");
        expect(config.textModels).toEqual([]);
        expect([...values.values()].join("\n")).not.toContain(connection.token);
        useConfigStore.getState().updateConfig("imageModel", "zaowu-local-image::nano2 lite");
        useConfigStore.getState().updateConfig("videoModel", "zaowu-local-wan-video::wan");
        await restoreLocalGenerationConnection();
        expect(useConfigStore.getState().config.imageModel).toBe("zaowu-local-image::nano2 lite");
        expect(useConfigStore.getState().config.videoModel).toBe("zaowu-local-wan-video::wan");
    });

    test("video defaults use the available provider when Q4 is absent", async () => {
        useConfigStore.getState().replaceConfig({ ...defaultConfig, channels: [] });
        globalThis.fetch = (async () => Response.json({ data: [
            { provider: "wan-video", id: "wan", name: "Wan", ratios: ["16:9"], max_references: 1, duration_min: 5, duration_max: 10 },
        ] })) as typeof fetch;
        await restoreLocalGenerationConnection();
        const config = useConfigStore.getState().config;
        expect(config.videoModel).toBe("zaowu-local-wan-video::wan");
        expect(config.imageModel).toBe("");
        expect(config.textModel).toBe("");
    });

    test("missing desktop connection does not request a browser handshake", async () => {
        window.zaowuDesktop!.getLocalProviderConnection = async () => null;
        await restoreLocalGenerationConnection();
        expect(requests).toHaveLength(0);
        expect(useConfigStore.getState().config.channels.map((channel) => channel.id)).toEqual(["personal"]);
    });

    test("rejects invalid connection addresses and empty tokens before fetching", async () => {
        await expect(applyLocalGenerationConnection({ ...connection, baseUrl: "https://example.com" })).rejects.toThrow("127.0.0.1");
        await expect(applyLocalGenerationConnection({ ...connection, token: "" })).rejects.toThrow("管理客户端");
        expect(requests).toHaveLength(0);
    });

    test("failed model synchronization preserves the current configuration and points to settings", async () => {
        const before = useConfigStore.getState().config;
        globalThis.fetch = (async () => Response.json({}, { status: 401 })) as typeof fetch;
        await expect(applyLocalGenerationConnection(connection)).rejects.toThrow("设置→本地生成服务");
        expect(useConfigStore.getState().config).toBe(before);
        globalThis.fetch = (async () => Response.json({ data: [] })) as typeof fetch;
        await expect(applyLocalGenerationConnection(connection)).rejects.toThrow("没有返回模型");
        expect(useConfigStore.getState().config).toBe(before);
    });

    test("network failures show a Chinese reconnect message and preserve configuration", async () => {
        const before = useConfigStore.getState().config;
        globalThis.fetch = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
        await expect(applyLocalGenerationConnection(connection)).rejects.toThrow("无法连接本地生成服务");
        expect(useConfigStore.getState().config).toBe(before);
    });
});
