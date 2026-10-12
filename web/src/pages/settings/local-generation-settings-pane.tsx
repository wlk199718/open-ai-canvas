import { App, Button, Input } from "antd";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { SettingsRow } from "@/components/ui/product/settings-row";
import {
    applyLocalGenerationConnection,
    getLocalGenerationUrl,
    normalizeLocalGenerationUrl,
    saveLocalGenerationUrl,
} from "@/services/local-generation";

export function LocalGenerationSettingsPane() {
    const { message } = App.useApp();
    const [baseUrl, setBaseUrl] = useState(getLocalGenerationUrl);
    const [status, setStatus] = useState<"checking" | "disconnected" | "connected" | "error">("checking");
    const [modelCount, setModelCount] = useState(0);
    const [error, setError] = useState("");
    const [opening, setOpening] = useState(false);

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            try {
                const connection = await window.zaowuDesktop?.getLocalProviderConnection();
                if (cancelled) return;
                if (!connection) {
                    setStatus("disconnected");
                    return;
                }
                const address = normalizeLocalGenerationUrl(connection.baseUrl);
                setBaseUrl(address);
                const count = await applyLocalGenerationConnection(connection);
                if (cancelled) return;
                setModelCount(count);
                setStatus("connected");
            } catch (cause) {
                if (cancelled) return;
                setError(cause instanceof Error ? cause.message : "本地生成服务连接失败，请重新连接");
                setStatus("error");
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const syncModels = async () => {
        setStatus("checking");
        setError("");
        try {
            const address = saveLocalGenerationUrl(baseUrl);
            setBaseUrl(address);
            const connection = await window.zaowuDesktop?.getLocalProviderConnection();
            if (!connection) throw new Error("请先打开管理客户端并激活卡密，然后重新连接");
            if (normalizeLocalGenerationUrl(connection.baseUrl) !== address) {
                throw new Error("地址与管理客户端提供的连接不一致，请填写管理客户端的 127.0.0.1 服务地址");
            }
            const count = await applyLocalGenerationConnection(connection);
            setModelCount(count);
            setStatus("connected");
            message.success(`已连接本地生成服务，同步 ${count} 个模型`);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "本地生成服务连接失败，请重新连接");
            setStatus("error");
        }
    };

    const openManager = async () => {
        setOpening(true);
        try {
            const result = await window.zaowuDesktop?.openLocalProvider?.();
            if (!result?.opened) throw new Error("管理客户端未能打开，请确认已安装");
        } catch (cause) {
            message.error(cause instanceof Error ? cause.message : "无法打开管理客户端");
        } finally {
            setOpening(false);
        }
    };

    return (
        <>
            <div className="settings-pane-header">
                <div className="min-w-0">
                    <h2>本地生成服务</h2>
                    <p>连接本机生成服务，将可用模型同步到创作页面。</p>
                </div>
            </div>
            <div className="settings-section">
                <section className="settings-preference-block">
                    <SettingsRow
                        label="服务地址"
                        className="flex-wrap sm:flex-nowrap"
                        description="仅支持 127.0.0.1 本机地址，请与管理客户端的服务地址保持一致。"
                        control={
                            <Input
                                aria-label="本地生成服务地址"
                                value={baseUrl}
                                disabled={status === "checking"}
                                onChange={(event) => {
                                    setBaseUrl(event.target.value);
                                    setStatus("disconnected");
                                    setError("");
                                }}
                                placeholder="http://127.0.0.1:8787"
                            />
                        }
                        controlClassName="w-full sm:w-[320px]"
                    />
                    <div className="py-4 text-sm" role="status" aria-live="polite">
                        {status === "checking" ? "正在连接本地生成服务…" : status === "connected" ? `已连接 · 已同步 ${modelCount} 个模型` : "未连接"}
                        {error ? <p className="mt-2 text-destructive">{error}</p> : null}
                        <p className="mt-2 text-muted-foreground">首次使用请打开管理客户端并激活卡密，完成后点击连接。已保存的连接会自动恢复。</p>
                    </div>
                    <div className="flex flex-wrap gap-2 pb-3">
                        <Button type="primary" icon={<RefreshCw className="size-4" />} loading={status === "checking"} onClick={() => void syncModels()}>
                            {status === "connected" ? "同步模型" : "连接并同步模型"}
                        </Button>
                        <Button icon={<ExternalLink className="size-4" />} loading={opening} onClick={() => void openManager()}>打开管理客户端</Button>
                    </div>
                </section>
            </div>
        </>
    );
}
