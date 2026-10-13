import { useQuery } from "@tanstack/react-query";
import { desktopMediaId, inspectDesktopMedia } from "@/services/desktop-media";
import { getActiveUserScope } from "@/lib/user-scope";

export function useDesktopMedia(storageKey?: string, url?: string, enabled = true) {
    const media = { storageKey, url };
    const id = desktopMediaId(media);
    const desktop = typeof window !== "undefined" && Boolean(window.zaowuDesktop?.inspectMedia);
    const query = useQuery({
        queryKey: ["desktop-media", getActiveUserScope(), id],
        queryFn: () => inspectDesktopMedia(media),
        enabled: enabled && desktop && Boolean(id),
        staleTime: 10_000,
        retry: false,
    });
    const checking = enabled && desktop && Boolean(id) && query.isPending;
    return { local: query.data?.local === true, checking, unavailable: query.isError, label: checking ? "检查本地文件…" : query.isError ? "文件不可访问" : query.data?.local ? "打开所在文件夹" : "下载" };
}
