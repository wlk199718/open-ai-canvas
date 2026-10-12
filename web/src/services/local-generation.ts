import { defaultModelCapabilityConfig, type ModelCapabilityConfig } from "@/lib/model-capabilities";
import { useConfigStore, createModelChannel, normalizeConfigSnapshot, encodeChannelModel, type ModelChannel } from "@/stores/use-config-store";

export type LocalGenerationConnection = { baseUrl: string; token: string };
export type LocalGenerationModel = {
    id: string;
    name: string;
    provider: "image" | "wan-video" | "q4-video" | "claw-video";
    ratios: string[];
    resolutions?: string[];
    max_references: number;
    duration_min?: number;
    duration_max?: number;
};

const providerDefinitions = {
    image: { name: "本地图片", protocol: "zaowu-local-image" },
    "q4-video": { name: "本地 Q4 视频", protocol: "zaowu-local-q4" },
    "claw-video": { name: "本地 Claw 视频", protocol: "zaowu-local-claw" },
    "wan-video": { name: "本地 Wan 视频", protocol: "zaowu-local-wan" },
};

const LOCAL_GENERATION_URL_KEY = "zaowu:local-provider-url";
export const DEFAULT_LOCAL_GENERATION_URL = "http://127.0.0.1:8787";

export function normalizeLocalGenerationUrl(value: string) {
    let url: URL;
    try {
        url = new URL(value.trim());
    } catch {
        throw new Error("请使用 127.0.0.1 本机服务地址");
    }
    if (url.hostname !== "127.0.0.1" || !["http:", "https:"].includes(url.protocol) || url.username || url.password) {
        throw new Error("请使用 127.0.0.1 本机服务地址");
    }
    return url.origin;
}

export function getLocalGenerationUrl() {
    try {
        return normalizeLocalGenerationUrl(window.localStorage.getItem(LOCAL_GENERATION_URL_KEY) || DEFAULT_LOCAL_GENERATION_URL);
    } catch {
        return DEFAULT_LOCAL_GENERATION_URL;
    }
}

export function saveLocalGenerationUrl(value: string) {
    const baseUrl = normalizeLocalGenerationUrl(value);
    window.localStorage.setItem(LOCAL_GENERATION_URL_KEY, baseUrl);
    return baseUrl;
}

function capabilityFor(model: LocalGenerationModel): ModelCapabilityConfig {
    const profile = defaultModelCapabilityConfig(providerDefinitions[model.provider].protocol, model.id);
    if (model.provider === "image") {
        const image = profile.image!;
        image.references = { promptMaxChars: 8000, maxImages: model.max_references, maxImageBytes: 20 * 1024 * 1024, maskSupported: false };
        image.size = { parameter: "aspect_ratio", values: model.ratios, default: "1:1", allowCustom: false };
        image.quality = { supported: true, values: model.resolutions || ["1080p"], default: "1080p" };
        image.maxOutputs = 1;
        image.transparentBackground.supported = false;
        return { version: 1, image };
    }
    const video = profile.video!;
    video.references = {
        promptMaxChars: 8000, minImages: model.provider === "wan-video" ? 1 : 0,
        maxImages: model.max_references, maxImageBytes: 20 * 1024 * 1024,
        maxVideos: 0, maxVideoBytes: 0, maxVideoDurationSeconds: 0,
        maxAudios: 0, maxAudioBytes: 0, maxAudioDurationSeconds: 0,
    };
    video.duration = { selection: "range", min: model.duration_min, max: model.duration_max, step: 1, default: Math.max(model.duration_min || 1, 5) };
    video.ratios = model.ratios;
    video.defaultRatio = "16:9";
    video.resolutions = model.resolutions || ["1080p"];
    video.defaultResolution = video.resolutions.includes("720p") ? "720p" : video.resolutions[0];
    video.operations = model.max_references
        ? (model.provider === "wan-video"
            ? ["image_to_video", "reference_to_video"]
            : ["text_to_video", "image_to_video", "reference_to_video"])
        : ["text_to_video"];
    video.defaultOperation = video.operations[0];
    video.generateAudio.supported = false;
    video.watermark.supported = false;
    return { version: 1, video };
}

export function buildLocalGenerationChannels(connection: LocalGenerationConnection, models: LocalGenerationModel[]): ModelChannel[] {
    return (Object.keys(providerDefinitions) as Array<LocalGenerationModel["provider"]>).flatMap((provider) => {
        const items = models.filter((model) => model.provider === provider);
        if (!items.length) return [];
        const definition = providerDefinitions[provider];
        return [createModelChannel({
            id: `zaowu-local-${provider}`, name: definition.name,
            baseUrl: connection.baseUrl, apiKey: connection.token, interfaceType: definition.protocol,
            models: items.map((model) => model.id),
            modelCosts: items.map((model) => ({
                model: model.id, displayName: model.name, capability: provider === "image" ? "image" : "video",
                protocol: definition.protocol, billingMode: "fixed_request", unitPriceMicrocredits: 0,
                capabilityConfig: capabilityFor(model),
            })),
        })];
    });
}

async function readModels(connection: LocalGenerationConnection) {
    let response: Response;
    try {
        response = await fetch(`${connection.baseUrl}/api/v1/models`, {
            headers: { Authorization: `Bearer ${connection.token}` },
            credentials: "omit",
            redirect: "error",
            signal: AbortSignal.timeout(10_000),
        });
    } catch {
        throw new Error("无法连接本地生成服务，请打开管理客户端后在设置→本地生成服务中重新连接");
    }
    if (!response.ok) throw new Error(`生成服务连接失败（${response.status}），请在设置→本地生成服务中重新连接`);
    const result = await response.json() as { data: LocalGenerationModel[] };
    if (!Array.isArray(result.data) || !result.data.length) throw new Error("本地生成服务没有返回模型");
    return result.data;
}

export async function applyLocalGenerationConnection(connection: LocalGenerationConnection) {
    connection = { baseUrl: normalizeLocalGenerationUrl(connection.baseUrl), token: connection.token.trim() };
    if (!connection.token) throw new Error("请先在管理客户端激活卡密，再到设置→本地生成服务连接");
    const models = await readModels(connection);
    const channels = buildLocalGenerationChannels(connection, models);
    const current = useConfigStore.getState().config;
    const firstImage = channels.find((channel) => channel.interfaceType === "zaowu-local-image");
    const firstVideo = channels.find((channel) => channel.interfaceType === "zaowu-local-q4");
    useConfigStore.getState().replaceConfig(normalizeConfigSnapshot({ config: {
        ...current,
        channels: [...current.channels.filter((channel) => !channel.id.startsWith("zaowu-local-")), ...channels],
        imageModel: current.imageModel || (firstImage ? encodeChannelModel(firstImage.id, firstImage.models[0]) : ""),
        videoModel: current.videoModel || (firstVideo ? encodeChannelModel(firstVideo.id, firstVideo.models[0]) : ""),
    } }).config);
    return models.length;
}

export async function restoreLocalGenerationConnection() {
    const connection = await window.zaowuDesktop?.getLocalProviderConnection();
    if (connection) await applyLocalGenerationConnection(connection);
}
