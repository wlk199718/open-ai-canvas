import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemeName = "light" | "dark";

type ThemeStore = {
    theme: ThemeName;
    setTheme: (theme: ThemeName) => void;
};

const DEFAULT_THEME: ThemeName = "dark";
const VALID_THEMES: ThemeName[] = ["light", "dark"];

export const useThemeStore = create<ThemeStore>()(
    persist(
        (set) => ({
            theme: DEFAULT_THEME,
            // 写入前校验：非法 theme 直接忽略，防止 syncRemoteUserData 等路径绕过 merge 写入坏值
            setTheme: (next) => set((state) => (VALID_THEMES.includes(next) ? { theme: next } : state)),
        }),
        {
            // 更换存储键，让桌面端覆盖安装后从黑色默认主题开始；新版本内的手动选择仍会持久化。
            name: "yingce:theme_store_v2",
            // 持久化恢复校验：旧版本/坏 session 写入的非法值回退到默认主题，
            // 避免 canvasThemes[非法值] = undefined 触发 "reading 'node'" 崩溃
            merge: (persisted, current) => {
                const stored = (persisted || {}) as Partial<ThemeStore>;
                const theme = VALID_THEMES.includes(stored.theme as ThemeName)
                    ? (stored.theme as ThemeName)
                    : DEFAULT_THEME;
                return { ...current, theme };
            },
        },
    ),
);
