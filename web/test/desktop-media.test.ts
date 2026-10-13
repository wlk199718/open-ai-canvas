import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { desktopMediaId, inspectDesktopMedia, openOrDownloadMedia, saveMediaAs } from "../src/services/desktop-media";

describe("desktop media operations", () => {
    let originalWindow: PropertyDescriptor | undefined;
    let originalDocument: PropertyDescriptor | undefined;
    const calls: string[] = [];
    let local = true;
    let failed = false;
    const media = { storageKey: "resource:image-1", fileName: "image.png" };
    beforeEach(() => {
        originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
        originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
        calls.length = 0;
        local = true;
        failed = false;
        Object.defineProperty(globalThis, "window", { configurable: true, value: {
            location: { origin: "http://127.0.0.1:7094" },
            zaowuDesktop: {
                inspectMedia: async (id: string) => { calls.push(`inspect:${id}`); if (failed) throw new Error("文件不存在"); return { local }; },
                revealMedia: async (id: string) => { calls.push(`reveal:${id}`); return true; },
                saveMediaAs: async (id: string, name: string) => { calls.push(`save:${id}:${name}`); return { saved: true }; },
            },
        } });
        Object.defineProperty(globalThis, "document", { configurable: true, value: {
            createElement: () => ({ style: {}, click: () => calls.push("download"), remove: () => {} }),
            body: { appendChild: () => {} },
        } });
    });
    afterEach(() => {
        if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
        else Reflect.deleteProperty(globalThis, "window");
        if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
        else Reflect.deleteProperty(globalThis, "document");
    });
    test("resolves resource IDs only from resource keys or same-origin file URLs", () => {
        expect(desktopMediaId(media)).toBe("image-1");
        expect(desktopMediaId({ url: "/api/resources/video-1/file" })).toBe("video-1");
        expect(desktopMediaId({ url: "https://example.com/api/resources/video-1/file" })).toBe("");
        expect(desktopMediaId({ url: "file:///C:/secret" })).toBe("");
        expect(desktopMediaId({ url: "data:image/png;base64,fixture" })).toBe("");
    });
    test("local result opens its folder without downloading a second copy", async () => {
        await openOrDownloadMedia(media);
        expect(calls).toEqual(["inspect:image-1", "reveal:image-1"]);
    });
    test("save as uses the desktop dialog and requested filename", async () => {
        expect(await saveMediaAs(media)).toEqual({ saved: true });
        expect(calls).toEqual(["inspect:image-1", "save:image-1:image.png"]);
    });
    test("external results and ordinary browser sessions retain browser download", async () => {
        await openOrDownloadMedia({ url: "https://example.com/image.png", fileName: "image.png" });
        expect(calls).toEqual(["download"]);
        window.zaowuDesktop = undefined;
        expect(await inspectDesktopMedia(media)).toEqual({ local: false });
    });
    test("an unavailable local file reports failure without silently downloading again", async () => {
        failed = true;
        await expect(openOrDownloadMedia(media)).rejects.toThrow("文件不存在");
        expect(calls).toEqual(["inspect:image-1"]);
    });
});
