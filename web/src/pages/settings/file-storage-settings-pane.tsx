import { App, Button, Input } from "antd";
import { FolderOpen, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { SettingsRow } from "@/components/ui/product/settings-row";
import {
    chooseFileStorage, getFileStorage, openFileStorage, restartForFileStorage,
    type FileStorageSettings,
} from "@/services/desktop-file-storage";

export function FileStorageSettingsPane() {
    const { message } = App.useApp();
    const [settings, setSettings] = useState<FileStorageSettings | null>(null);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    useEffect(() => {
        let cancelled = false;
        const reload = () => getFileStorage().then((value) => {
            if (!cancelled) setSettings(value);
        }).catch((cause) => {
            if (!cancelled) setError(cause instanceof Error ? cause.message : "读取文件存储失败");
        });
        void reload();
        const timer = window.setInterval(() => void reload(), 5000);
        return () => { cancelled = true; window.clearInterval(timer); };
    }, []);
    const run = async (key: string, action: () => Promise<unknown>) => {
        setBusy(key);
        setError("");
        try { await action(); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "文件存储操作失败"); }
        finally { setBusy(""); }
    };
    return <>
        <div className="settings-pane-header"><div className="min-w-0">
            <h2>文件存储</h2>
            <p>造物与生成服务共用一个保存位置，自动分开整理生成结果和项目素材。</p>
        </div></div>
        <div className="settings-section">
            <section className="settings-preference-block">
                <SettingsRow label="保存位置" description="更换后重启两个客户端生效，已有文件会复制并校验，原目录保留备份。"
                    className="flex-wrap" controlClassName="w-full sm:w-[480px]"
                    control={<Input aria-label="文件保存位置" readOnly value={settings?.root || ""} placeholder="正在读取保存位置…" />} />
                <div className="flex flex-wrap gap-2 py-4">
                    <Button disabled={!settings || !!busy} loading={busy === "choose"} icon={<FolderOpen className="size-4" />}
                        onClick={() => void run("choose", async () => {
                            const value = await chooseFileStorage();
                            setSettings(value);
                            if (value.pending) message.success("保存位置已设置，重启后迁移并生效");
                        })}>选择文件夹</Button>
                    <Button disabled={!settings || !!busy} icon={<FolderOpen className="size-4" />}
                        onClick={() => void run("open", () => openFileStorage("root"))}>打开保存位置</Button>
                    {settings?.pending && <Button type="primary" loading={busy === "restart"} disabled={!!busy}
                        icon={<RefreshCw className="size-4" />}
                        onClick={() => void run("restart", restartForFileStorage)}>重启并应用</Button>}
                </div>
                {settings && <div className="grid gap-4 py-4">
                    <SettingsRow label="生成结果" description="中间服务下载的图片和视频。"
                        className="flex-wrap" controlClassName="w-full sm:w-[480px]"
                        control={<div className="flex min-w-0 items-center gap-2">
                            <Input aria-label="当前生成结果目录" readOnly value={settings.activeResults || "生成服务尚未就绪"} />
                            <Button disabled={!settings.activeResults || !!busy} aria-label="打开生成结果目录"
                                icon={<FolderOpen className="size-4" />} onClick={() => void run("results", () => openFileStorage("results"))} />
                        </div>} />
                    <SettingsRow label="项目素材" description="造物素材库、上传文件和视频播放副本。"
                        className="flex-wrap" controlClassName="w-full sm:w-[480px]"
                        control={<div className="flex min-w-0 items-center gap-2">
                            <Input aria-label="当前项目素材目录" readOnly value={settings.activeResources} />
                            <Button disabled={!!busy} aria-label="打开项目素材目录"
                                icon={<FolderOpen className="size-4" />} onClick={() => void run("resources", () => openFileStorage("resources"))} />
                        </div>} />
                </div>}
                <div role="status" aria-live="polite" className="py-4 text-sm text-foreground/65">
                    {settings?.pending ? "新位置待应用。请先结束生成任务，再点击「重启并应用」。" : settings ? "两个客户端使用统一保存位置。" : ""}
                    <p className="mt-2">账号、卡密、数据库和项目记录继续保存在应用数据目录。两类文件分别管理，当前仍各自保存副本。</p>
                    {error && <p role="alert" className="mt-2 text-destructive">{error}</p>}
                </div>
            </section>
        </div>
    </>;
}
