/// <reference types="vite/client" />

declare const __APP_VERSION__: string;
declare const __APP_CHANGELOG__: string;

interface Window {
    zaowuDesktop?: {
        openLocalProvider: () => Promise<{ opened: boolean }>;
        getLocalProviderConnection: () => Promise<import("./services/local-generation").LocalGenerationConnection | null>;
        saveLocalProviderConnection: (connection: import("./services/local-generation").LocalGenerationConnection) => Promise<boolean>;
        getFileStorage: () => Promise<import("./services/desktop-file-storage").FileStorageSettings>;
        chooseFileStorage: () => Promise<import("./services/desktop-file-storage").FileStorageSettings>;
        openFileStorage: (kind: "root" | "results" | "resources") => Promise<boolean>;
        restartForFileStorage: () => Promise<boolean>;
    };
}

interface ImportMetaEnv {
    readonly VITE_APP_VERSION?: string;
    readonly VITE_BUILD_COMMIT?: string;
    readonly VITE_BUILD_TIME?: string;
    /** 桌面端构建标记：为 "1" 时 ffmpeg 走后端 LGPL 版本，不打包 ffmpeg.wasm */
    readonly VITE_DESKTOP?: string;
}
