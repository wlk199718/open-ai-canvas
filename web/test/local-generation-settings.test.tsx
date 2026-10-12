import { describe, expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";

import { LocalGenerationSettingsPane } from "../src/pages/settings/local-generation-settings-pane";

function source(path: string) {
    return readFileSync(resolve(import.meta.dir, "../src", path), "utf8");
}

describe("local generation settings surface", () => {
    test("renders a single address input, connection status and the management launcher", () => {
        const markup = renderToStaticMarkup(<LocalGenerationSettingsPane />);
        expect(markup).toContain("本地生成服务");
        expect(markup).toContain("127.0.0.1");
        expect(markup).toContain("连接并同步模型");
        expect(markup).toContain("打开管理客户端");
        expect(markup).toContain('role="status"');
        expect(markup).toContain("激活卡密");
        expect(markup.match(/<input\b/g)).toHaveLength(1);
        expect(markup).not.toContain('type="password"');
        for (const label of ["账号池", "上游任务", "问题诊断", "更换卡密"]) expect(markup).not.toContain(label);
    });

    test("adds a desktop-only section and uses the bridge without browser activation or handshake", () => {
        const settings = source("pages/settings/index.tsx");
        const pane = source("pages/settings/local-generation-settings-pane.tsx");
        const service = source("services/local-generation.ts");
        expect(settings).toContain('import.meta.env.VITE_DESKTOP === "1" ? [{ key: "local-generation"');
        expect(settings).toContain('"local-generation": <SettingsPane><LocalGenerationSettingsPane /></SettingsPane>');
        expect(pane).toContain("getLocalProviderConnection()");
        expect(pane).toContain("applyLocalGenerationConnection(connection)");
        expect(pane).toContain("openLocalProvider?.()");
        expect(pane).not.toContain("fetch(");
        expect(pane).not.toContain("saveLocalProviderConnection");
        expect(service).not.toContain("/api/admin/");
        expect(service).not.toContain("connection-ticket");
        expect(service).not.toContain("getLocalProviderToken");
    });

    test("removes old page files and every navigation entry while retaining plugins and creation history", () => {
        for (const path of ["pages/local-control/index.tsx", "pages/local-control/local-control.css"]) {
            expect(existsSync(resolve(import.meta.dir, "../src", path))).toBe(false);
        }
        for (const path of ["router.tsx", "components/layout/workspace-sidebar-nav.tsx", "components/layout/workspace-account-card.tsx", "components/layout/workspace-sidebar-footer.tsx", "components/layout/workspace-top-bar.tsx", "lib/user-session.ts"]) {
            expect(source(path)).not.toContain("local-control");
            expect(source(path)).not.toContain("本地控制中心");
        }
        const router = source("router.tsx");
        const sidebar = source("components/layout/workspace-sidebar-nav.tsx");
        expect(router).toContain('to="/settings?section=local-generation"');
        expect(router).toContain('path: "/plugins"');
        expect(router).toContain('path: "/tasks"');
        expect(sidebar).toContain('toolItem("plugins", "/plugins")');
        expect(sidebar).toContain('title: "创作历史"');
        expect(source("vite-env.d.ts")).toContain("openLocalProvider: () => Promise<{ opened: boolean }>");
    });
});
