import { describe, expect, test } from "bun:test";
import { initializeDesktopTextPresets, isPendingDesktopTextPreset } from "../src/lib/desktop-text-presets";
import {
    createModelChannel,
    defaultConfig,
    normalizeConfigSnapshot,
    resolveModelRequestConfig,
    useConfigStore,
} from "../src/stores/use-config-store";

describe("desktop text presets", () => {
    test("seeds the current four groups with editable connections and no credentials", () => {
        const config = initializeDesktopTextPresets(defaultConfig, true);
        expect(config.channels.map(({ name, models }) => [name, models.length])).toEqual([
            ["GPT", 4], ["Gemini", 8], ["Claude", 6], ["国产", 21],
        ]);
        expect(config.textModels).toHaveLength(39);
        expect(config.imageModels).toEqual([]);
        expect(config.videoModels).toEqual([]);
        expect(config.textModel).toBe("zaowu-text-gpt::gpt-6-sol");
        for (const channel of config.channels) {
            expect(channel.baseUrl).toBe("https://www.nexotoken.net/v1");
            expect(channel.scope).toBe("user");
            expect(channel.apiKey).toBe("");
            expect(channel.secretKey).toBe("");
            expect(channel.headers).toEqual([]);
        }
        expect(useConfigStore.getState().isAiConfigReady(config, config.textModel)).toBe(false);
    });

    test("routes each family through its configured protocol", () => {
        const config = initializeDesktopTextPresets(defaultConfig, true);
        for (const [channel, model, protocol, format] of [
            ["gpt", "gpt-6-sol", "openai-response", "openai"],
            ["gemini", "gemini-3.1-pro-preview", "chat-completion", "openai"],
            ["claude", "claude-opus-5", "claude-api", "claude"],
            ["domestic", "deepseek-v4-pro", "chat-completion", "openai"],
        ]) {
            const resolved = resolveModelRequestConfig(config, `zaowu-text-${channel}::${model}`);
            expect(resolved.interfaceType).toBe(protocol);
            expect(resolved.apiFormat).toBe(format);
        }
    });

    test("unused preset keys do not block configuring other services and become validated after key entry", () => {
        const config = initializeDesktopTextPresets(defaultConfig, true);
        const preset = config.channels[0];
        expect(isPendingDesktopTextPreset(preset)).toBe(true);
        expect(isPendingDesktopTextPreset({ ...preset, apiKey: "fixture-user-key" })).toBe(false);
        expect(isPendingDesktopTextPreset({ ...preset, id: "custom-text-channel" })).toBe(false);
        expect(isPendingDesktopTextPreset({ ...preset, scope: "system" })).toBe(false);
        expect(useConfigStore.getState().isAiConfigReady(config, config.textModel)).toBe(false);
    });

    test("preserves edited URL, key, selection and deletions across snapshot restoration", () => {
        const seeded = initializeDesktopTextPresets(defaultConfig, true);
        const edited = normalizeConfigSnapshot({ config: {
            ...seeded,
            textModel: "zaowu-text-domestic::kimi-k3",
            channels: seeded.channels.filter((channel) => channel.name !== "Claude").map((channel) => ({
                ...channel, baseUrl: "https://example.com/v1", apiKey: "fixture-user-key",
            })),
        } }).config;
        const restored = normalizeConfigSnapshot(JSON.parse(JSON.stringify({ config: edited }))).config;
        expect(initializeDesktopTextPresets(restored, true)).toBe(restored);
        expect(restored.channels).toHaveLength(3);
        expect(restored.channels[0].baseUrl).toBe("https://example.com/v1");
        expect(restored.channels[0].apiKey).toBe("fixture-user-key");
        expect(restored.textModel).toBe("zaowu-text-domestic::kimi-k3");
        expect(useConfigStore.getState().isAiConfigReady(restored, restored.textModel)).toBe(true);

        const cleared = normalizeConfigSnapshot({ config: { ...restored, channels: [] } }).config;
        expect(initializeDesktopTextPresets(cleared, true).channels).toEqual([]);
    });

    test.each(["user", "system"] as const)("keeps existing %s text configuration without duplicates", (scope) => {
        const existing = normalizeConfigSnapshot({ config: {
            ...defaultConfig,
            channels: [createModelChannel({
                id: "existing", name: "Existing", models: ["gpt-6-sol"],
                baseUrl: "https://example.com/v1", apiKey: "fixture-existing-key", scope,
                modelCosts: [{ model: "gpt-6-sol", capability: "text", protocol: "openai-response", billingMode: "fixed_request", unitPriceMicrocredits: 0 }],
            })],
        } }).config;
        const config = initializeDesktopTextPresets(existing, true);
        expect(config.channels).toEqual(existing.channels);
        expect(config.textModel).toBe(existing.textModel);
        expect(config.desktopTextPresetsVersion).toBe(1);
    });

    test("adds presets to a media-only desktop without changing its selections", () => {
        const existing = normalizeConfigSnapshot({ config: {
            ...defaultConfig,
            channels: [createModelChannel({
                id: "zaowu-local-image", name: "本地图片", models: ["3.2_pro_m"],
                modelCosts: [{ model: "3.2_pro_m", capability: "image", billingMode: "fixed_request", unitPriceMicrocredits: 0 }],
            })],
        } }).config;
        const config = initializeDesktopTextPresets(existing, true);
        expect(config.channels).toHaveLength(5);
        expect(config.imageModel).toBe(existing.imageModel);
        expect(config.textModels).toHaveLength(39);
    });

    test("leaves web deployments unchanged and does not share mutable presets across users", () => {
        expect(initializeDesktopTextPresets(defaultConfig, false)).toBe(defaultConfig);
        const firstUser = initializeDesktopTextPresets(defaultConfig, true);
        firstUser.channels[0].apiKey = "fixture-first-user-key";
        firstUser.channels[0].models.pop();
        const nextUser = initializeDesktopTextPresets(defaultConfig, true);
        expect(nextUser.channels[0].apiKey).toBe("");
        expect(nextUser.channels[0].models).toHaveLength(4);
        expect(defaultConfig.channels).toEqual([]);
    });
});
