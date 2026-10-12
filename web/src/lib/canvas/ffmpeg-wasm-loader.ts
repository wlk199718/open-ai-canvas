import { FFmpeg } from "@ffmpeg/ffmpeg";
import ffmpegCoreURL from "@ffmpeg/core?url";
import ffmpegWasmURL from "@ffmpeg/core/wasm?url";

// Web 版使用 ffmpeg.wasm。单独成模块并仅通过动态 import 引用，
// 桌面构建（VITE_DESKTOP=1）时整个分支被裁掉，GPL 授权的 core/wasm 不会进入产物。
export async function loadWasmFFmpeg() {
    const ffmpeg = new FFmpeg();
    try {
        // 核心资产随前端同源发布，避免自部署环境首次合并依赖第三方 CDN。
        await ffmpeg.load({ coreURL: ffmpegCoreURL, wasmURL: ffmpegWasmURL });
    } catch (cause) {
        ffmpeg.terminate();
        throw cause;
    }
    return ffmpeg;
}
