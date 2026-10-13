import { defaultModelCapabilityConfig } from "@/lib/model-capabilities";
import type { ModelProtocol } from "@/lib/model-protocols";
import {
    createModelChannel,
    encodeChannelModel,
    normalizeConfigSnapshot,
    selectableModelsByCapability,
    type AiConfig,
    type ModelChannel,
} from "@/stores/use-config-store";

const PRESET_VERSION = 1;
const BASE_URL = "https://www.nexotoken.net/v1";
const TEXT_CHANNELS: Array<{ id: string; name: string; protocol: ModelProtocol; models: string[] }> = [
    {
        id: "zaowu-text-gpt",
        name: "GPT",
        protocol: "openai-response",
        models: ["gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"],
    },
    {
        id: "zaowu-text-gemini",
        name: "Gemini",
        protocol: "chat-completion",
        models: [
            "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-high", "gemini-3.1-pro-low", "gemini-3.1-pro-preview",
            "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
        ],
    },
    {
        id: "zaowu-text-claude",
        name: "Claude",
        protocol: "claude-api",
        models: [
            "claude-fable-5", "claude-fable-5-1", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-5", "claude-sonnet-5-5",
        ],
    },
    {
        id: "zaowu-text-domestic",
        name: "国产",
        protocol: "chat-completion",
        models: [
            "deepseek-v4-flash", "deepseek-v4-flash-0731", "deepseek-v4-pro", "deepseek-v4-pro-0813", "deepseek-v4.1-flash",
            "glm-5.2", "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "hy3", "hy4-preview",
            "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code", "kimi-k3", "mimo-v2.5-pro", "minimax-m3",
            "qwen3.7-plus", "qwen3.8-flash", "qwen3.8-max", "qwen3.8-omni-flash",
        ],
    },
];

export function isPendingDesktopTextPreset(channel: ModelChannel): boolean {
    return channel.scope === "user" && !channel.apiKey.trim() && TEXT_CHANNELS.some((preset) => preset.id === channel.id);
}

export function initializeDesktopTextPresets(config: AiConfig, isDesktop: boolean): AiConfig {
    if (!isDesktop || config.desktopTextPresetsVersion) return config;
    const initialized = { ...config, desktopTextPresetsVersion: PRESET_VERSION };
    // 初始化标记随用户配置保存，之后删除渠道或清空模型不会在下次启动时被重新添加。
    if (selectableModelsByCapability(config, "text").length) return initialized;
    const channels: ModelChannel[] = TEXT_CHANNELS.map(({ id, name, protocol, models }) => createModelChannel({
        id,
        name,
        baseUrl: BASE_URL,
        apiKey: "",
        apiFormat: "openai",
        scope: "user",
        models,
        modelCosts: models.map((model) => ({
            model,
            displayName: model,
            capability: "text",
            protocol,
            billingMode: "token",
            unitPriceMicrocredits: 0,
            inputTokenPriceMicrocredits: 0,
            outputTokenPriceMicrocredits: 0,
            cachedTokenPriceMicrocredits: 0,
            capabilityConfig: { version: 1, text: defaultModelCapabilityConfig(protocol, model).text },
        })),
    }));
    return normalizeConfigSnapshot({ config: {
        ...initialized,
        channels: [...config.channels, ...channels],
        textModel: encodeChannelModel("zaowu-text-gpt", "gpt-6-sol"),
    } }).config;
}
