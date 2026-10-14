import { afterEach, describe, expect, test } from "bun:test";
import { agentModelFields, ensureAgentModelConfig, filterDesktopAgentCatalog } from "../src/services/desktop-agent-model";
import type { ModelCatalogResponse } from "../src/services/api/logical-models";
import { createModelChannel, defaultConfig, resolveModelRequestConfig } from "../src/stores/use-config-store";

const originalWindow = globalThis.window;
afterEach(() => { globalThis.window = originalWindow; });
function desktop(bridge?: (input: any) => Promise<any>) {
    let scope = "local-user";
    globalThis.window = {
        localStorage: { getItem: () => scope },
        ...(bridge ? { zaowuDesktop: { manageTextModel: bridge } } : {}),
    } as unknown as Window & typeof globalThis;
    return (value: string) => { scope = value; };
}
function config(scope: "user" | "system" = "user") {
    return { ...defaultConfig, model: "personal::fixture-text", channels: [createModelChannel({
        id: "personal", name: "GPT", scope, models: ["fixture-text"],
        apiKey: "fixture-key", interfaceType: "openai-response",
    })] };
}

describe("desktop Agent model admission", () => {
    test("syncs personal settings and submits only backend model references", async () => {
        let submitted: any;
        desktop(async (input) => { submitted = input; return { channelId: "managed", channelModelKey: input.model }; });
        const source = config();
        const managed = await ensureAgentModelConfig(source);
        expect(submitted.userId).toBe("local-user");
        expect(submitted.protocol).toBe("openai-response");
        expect(submitted.apiKey).toBe("fixture-key");
        expect(submitted.capabilityConfig.text.contextWindowTokens).toBeGreaterThan(0);
        expect(submitted.capabilityConfig.text.references.promptMaxChars).toBeGreaterThan(0);
        expect(resolveModelRequestConfig(managed, managed.model).channelId).toBe("managed");
        expect(resolveModelRequestConfig(managed, managed.model).apiKey).toBe("");
        expect(source.channels[0].scope).toBe("user");
        expect(source.channels[0].apiKey).toBe("fixture-key");
        expect(await agentModelFields(source, source.model)).toEqual({
            model: "fixture-text", channelId: "managed", channelModelKey: "fixture-text",
        });
    });
    test("system models retain their references without syncing keys", async () => {
        desktop(async () => { throw new Error("must not sync system channel"); });
        expect(await agentModelFields(config("system"), "personal::fixture-text")).toEqual({
            model: "fixture-text", channelId: "personal", channelModelKey: "fixture-text",
        });
    });
    test("web personal models, missing keys and unlisted models cannot be admitted", async () => {
        desktop();
        await expect(ensureAgentModelConfig(config())).rejects.toThrow("受管文本模型");
        desktop(async () => { throw new Error("unexpected bridge call"); });
        const source = config();
        await expect(ensureAgentModelConfig({ ...source, channels: [{ ...source.channels[0], apiKey: "" }] })).rejects.toThrow("有效密钥");
        await expect(ensureAgentModelConfig(source, "personal::missing")).rejects.toThrow("有效密钥");
    });
    test("failed sync and account switch stop admission before any Agent submission", async () => {
        desktop(async () => { throw new Error("sync-failed"); });
        await expect(ensureAgentModelConfig(config())).rejects.toThrow("sync-failed");
        const changeScope = desktop(async (input) => {
            changeScope("another");
            return { channelId: "managed", channelModelKey: input.model };
        });
        await expect(ensureAgentModelConfig(config())).rejects.toThrow("账号已切换");
    });
    test("hides managed duplicates by backend IDs even when public channel names are redacted", async () => {
        desktop();
        const catalog = { source: "system", channels: [
            { id: "mirror", name: "", displayName: "", models: [] },
            { id: "existing-system", name: "", displayName: "", models: [] },
        ] } as unknown as ModelCatalogResponse;
        expect(await filterDesktopAgentCatalog(catalog)).toBe(catalog);
        window.zaowuDesktop = { getManagedTextChannelIds: async () => ["mirror"] } as Window["zaowuDesktop"];
        const filtered = await filterDesktopAgentCatalog(catalog);
        expect(filtered.channels?.map((channel) => channel.id)).toEqual(["existing-system"]);
        expect(catalog.channels).toHaveLength(2);
    });
});
