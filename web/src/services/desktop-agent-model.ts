import { getActiveUserScope } from "@/lib/user-scope";
import { defaultModelCapabilityConfig } from "@/lib/model-capabilities";
import type { ModelCatalogResponse } from "@/services/api/logical-models";
import {
    encodeChannelModel, logicalModelIDForConfig, modelOptionName,
    resolveModelChannel, resolveModelRequestConfig, type AiConfig,
} from "@/stores/use-config-store";

export type DesktopTextModelInput = {
    userId: string;
    sourceId: string;
    name: string;
    baseUrl: string;
    apiKey: string;
    headers?: Array<{ name: string; value: string }>;
    enabled?: boolean;
    model: string;
    protocol: string;
    capabilityConfig?: import("@/lib/model-capabilities").ModelCapabilityConfig;
};

export async function filterDesktopAgentCatalog(catalog: ModelCatalogResponse): Promise<ModelCatalogResponse> {
    const bridge = typeof window === "undefined" ? undefined : window.zaowuDesktop?.getManagedTextChannelIds;
    if (!bridge) return catalog;
    const scope = getActiveUserScope();
    const ids = new Set(await bridge());
    if (getActiveUserScope() !== scope) throw new DOMException("账号已切换", "AbortError");
    return { ...catalog, channels: catalog.channels?.filter((channel) => !ids.has(channel.id)) };
}

// The persisted personal configuration and picker values stay intact. Only the
// submitted request uses the backend's encrypted channel/model reference.
export async function ensureAgentModelConfig(config: AiConfig, selected = config.model): Promise<AiConfig> {
    const next = { ...config, model: selected };
    const request = resolveModelRequestConfig(next, selected);
    if (logicalModelIDForConfig(next) || request.channelId) return next;
    const bridge = typeof window === "undefined" ? undefined : window.zaowuDesktop?.manageTextModel;
    if (!bridge) throw new Error("智能体需要后端受管文本模型，请选择系统模型或更新造物桌面端");
    const scope = getActiveUserScope();
    const channel = resolveModelChannel(next, selected);
    const model = modelOptionName(selected);
    const cost = channel.modelCosts?.find((item) => item.model === model);
    const protocol = request.interfaceType || (request.apiFormat === "claude" ? "claude-api" : request.apiFormat === "gemini" ? "gemini-generate-content" : "chat-completion");
    if (!channel.models.includes(model) || channel.enabled === false || !request.apiKey.trim()) {
        throw new Error("请先在个人渠道填写当前文本模型的有效密钥");
    }
    const reference = await bridge({
        userId: scope, sourceId: channel.id, name: channel.name,
        baseUrl: request.baseUrl, apiKey: request.apiKey,
        headers: request.headers, enabled: channel.enabled,
        model, protocol,
        capabilityConfig: { version: 1, text: cost?.capabilityConfig?.text || defaultModelCapabilityConfig(protocol, model).text },
    });
    if (getActiveUserScope() !== scope) throw new DOMException("账号已切换", "AbortError");
    if (!reference.channelId || reference.channelModelKey !== model) throw new Error("智能体模型同步结果无效");
    return {
        ...next, model: encodeChannelModel(reference.channelId, model),
        channels: next.channels.map((item) => item.id === channel.id ? {
            ...item, id: reference.channelId, scope: "system", apiKey: "", secretKey: "", headers: [],
        } : item),
    };
}

export async function agentModelFields(config: AiConfig, selected: string) {
    const resolved = await ensureAgentModelConfig(config, selected);
    const logicalModelId = logicalModelIDForConfig(resolved);
    const request = resolveModelRequestConfig(resolved, resolved.model);
    return {
        model: modelOptionName(selected),
        ...(logicalModelId ? { logicalModelId } : { channelId: request.channelId, channelModelKey: request.model }),
    };
}
