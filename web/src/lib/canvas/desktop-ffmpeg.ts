import { apiBaseURL } from "@/services/api/request";

// 与 @ffmpeg/ffmpeg 的 FFmpeg 实例保持同形的最小接口，调用方（合并/裁剪/时间线导出）无需区分实现。
export type FFmpegLogEvent = { type?: string; message: string };
export type FFmpegLike = {
    writeFile(path: string, data: Uint8Array | string): Promise<boolean>;
    readFile(path: string): Promise<Uint8Array | string>;
    deleteFile(path: string): Promise<boolean>;
    exec(args: string[]): Promise<number>;
    on(event: "log", callback: (event: FFmpegLogEvent) => void): void;
    off(event: "log", callback: (event: FFmpegLogEvent) => void): void;
    terminate(): void;
};

// 桌面端实现：文件暂存在内存，exec 时连同参数上传到后端，由随包的 LGPL ffmpeg 执行，
// 避免在桌面发行版中分发 GPL 授权的 @ffmpeg/core wasm。
export class DesktopFFmpeg implements FFmpegLike {
    private files = new Map<string, Uint8Array>();
    private listeners = new Set<(event: FFmpegLogEvent) => void>();

    async writeFile(path: string, data: Uint8Array | string) {
        this.files.set(path, typeof data === "string" ? new TextEncoder().encode(data) : data);
        return true;
    }

    async readFile(path: string) {
        const data = this.files.get(path);
        if (!data) throw new Error(`文件不存在：${path}`);
        return data;
    }

    async deleteFile(path: string) {
        return this.files.delete(path);
    }

    on(_event: "log", callback: (event: FFmpegLogEvent) => void) {
        this.listeners.add(callback);
    }

    off(_event: "log", callback: (event: FFmpegLogEvent) => void) {
        this.listeners.delete(callback);
    }

    terminate() {
        this.files.clear();
        this.listeners.clear();
    }

    async exec(args: string[]) {
        const output = args[args.length - 1];
        // 只上传参数中引用到的文件（-i 输入、滤镜里的字幕文件、concat 列表及其条目）。
        const referenced = new Set<string>();
        for (const name of this.files.keys()) {
            if (name !== output && args.some((arg) => arg === name || arg.includes(name))) referenced.add(name);
        }
        for (const name of [...referenced]) {
            if (!name.toLowerCase().endsWith(".txt")) continue;
            const text = new TextDecoder().decode(this.files.get(name));
            for (const match of text.matchAll(/^file\s+'([^']*)'/gm)) {
                if (this.files.has(match[1])) referenced.add(match[1]);
            }
        }
        const form = new FormData();
        form.append("args", JSON.stringify(args));
        for (const name of referenced) form.append(name, new Blob([this.files.get(name) as BlobPart]), name);

        const response = await fetch(`${String(apiBaseURL).replace(/\/+$/, "")}/desktop/ffmpeg/exec`, { method: "POST", body: form, credentials: "include" });
        const exitHeader = response.headers.get("X-Ffmpeg-Exit-Code");
        if (exitHeader === null) {
            const detail = await response.text().catch(() => "");
            throw new Error(`本地 ffmpeg 调用失败（${response.status}）${detail ? "：" + detail : ""}`);
        }
        const exitCode = Number(exitHeader);
        if (exitCode === 0) {
            this.files.set(output, new Uint8Array(await response.arrayBuffer()));
            return 0;
        }
        const body = (await response.json().catch(() => ({}))) as { logs?: string };
        for (const line of String(body.logs || "").split(/\r?\n/)) {
            if (line) this.listeners.forEach((listener) => listener({ type: "stderr", message: line }));
        }
        return exitCode;
    }
}
