import { resourceIdFromStorageKey } from "@/services/api/resources";
import { downloadBrowserMedia, type BrowserMediaDownload } from "@/services/browser-download";

export function desktopMediaId(media: Pick<BrowserMediaDownload, "storageKey" | "url">): string {
    const id = resourceIdFromStorageKey(media.storageKey);
    if (id) return id;
    if (!media.url || typeof window === "undefined") return "";
    try {
        const url = new URL(media.url, window.location.origin);
        if (url.origin !== window.location.origin) return "";
        return /^\/api\/resources\/([A-Za-z0-9_-]+)\/file$/.exec(url.pathname)?.[1] || "";
    } catch { return ""; }
}

export async function inspectDesktopMedia(media: Pick<BrowserMediaDownload, "storageKey" | "url">): Promise<{ local: boolean }> {
    const id = desktopMediaId(media);
    if (!id || typeof window === "undefined" || !window.zaowuDesktop?.inspectMedia) return { local: false };
    return window.zaowuDesktop.inspectMedia(id);
}

export async function openOrDownloadMedia(media: BrowserMediaDownload) {
    const status = await inspectDesktopMedia(media);
    if (status.local) {
        await window.zaowuDesktop!.revealMedia(desktopMediaId(media));
        return;
    }
    await downloadBrowserMedia(media);
}

export async function saveMediaAs(media: BrowserMediaDownload) {
    const status = await inspectDesktopMedia(media);
    if (!status.local) throw new Error("此媒体尚未保存到本地，请先下载");
    return window.zaowuDesktop!.saveMediaAs(desktopMediaId(media), media.fileName);
}
