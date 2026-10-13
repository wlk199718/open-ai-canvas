import { App, Button } from "antd";
import { Download, FolderOpen, Save } from "lucide-react";
import { useDesktopMedia } from "@/hooks/use-desktop-media";
import { openOrDownloadMedia, saveMediaAs } from "@/services/desktop-media";
import type { BrowserMediaDownload } from "@/services/browser-download";

export function MediaFileActions({ media, disabled = false, primary = false }: { media: BrowserMediaDownload; disabled?: boolean; primary?: boolean }) {
    const { message } = App.useApp();
    const state = useDesktopMedia(media.storageKey, media.url, !disabled);
    const run = async (saveAs: boolean) => {
        try {
            if (saveAs) {
                const result = await saveMediaAs(media);
                if (result.saved) message.success("已另存为");
            } else {
                await openOrDownloadMedia(media);
            }
        } catch (error) {
            message.error(error instanceof Error ? error.message : "文件操作失败");
        }
    };
    return <>
        <Button type={primary ? "primary" : "default"} icon={state.local ? <FolderOpen className="size-3.5" /> : <Download className="size-3.5" />} disabled={disabled || state.checking || state.unavailable} onClick={() => void run(false)}>{state.label}</Button>
        {state.local ? <Button icon={<Save className="size-3.5" />} onClick={() => void run(true)}>另存为</Button> : null}
    </>;
}
