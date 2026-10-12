export interface FileStorageSettings {
    root: string;
    results: string;
    resources: string;
    activeResults: string | null;
    activeResources: string;
    pending: boolean;
}

function desktop() {
    const bridge = window.zaowuDesktop;
    if (!bridge?.getFileStorage) throw new Error("请更新造物桌面端以管理文件存储");
    return bridge;
}

export async function getFileStorage() {
    return desktop().getFileStorage();
}

export async function chooseFileStorage() {
    return desktop().chooseFileStorage();
}

export async function openFileStorage(kind: "root" | "results" | "resources") {
    return desktop().openFileStorage(kind);
}

export async function restartForFileStorage() {
    return desktop().restartForFileStorage();
}
