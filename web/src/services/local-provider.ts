const STORAGE_KEY = "zaowu:local-provider-url";
const TOKEN_KEY = "zaowu:local-provider-admin-token";

export type LocalProviderHealth = {
    ok: boolean;
    service?: string;
    activated: boolean;
    dev_mode?: boolean;
    license?: {
        project?: string;
        expires_at?: string | null;
        in_grace_period?: boolean;
        error?: string;
    };
};

export type LocalProviderAccount = {
    id: string;
    name: string;
    provider: string;
    credential_kind?: string;
    credit_profile?: string;
    enabled: number;
    balance: number | null;
    video_balance: number | null;
    image_balance: number | null;
    checked_at?: string | null;
    video_checked_at?: string | null;
    image_checked_at?: string | null;
    status: string;
    auth_state?: string;
    auth_error?: string;
    check_error?: string;
    q4_trials?: { remaining_by_resolution?: Record<string, number> };
    image_trials?: Record<string, unknown>;
    claw_state?: { status?: string; test_message_status?: string };
    can_login?: boolean;
};

export type LocalProviderOverview = {
    activated: boolean;
    client_connected?: boolean;
    accounts: LocalProviderAccount[];
    tasks: Array<{ status: string; count: number }>;
    providers?: Array<{ id: string; provider: string; name: string; rate: number }>;
    wan_settings?: { poll_interval: number; task_timeout: number; min_balance: number };
    cookie_settings?: { check_concurrency: number; claw_message_workers: number };
};

export type LocalProviderSession = {
    token: string;
    session_expires_at?: string;
};

export function getLocalProviderUrl() {
    try {
        return window.localStorage.getItem(STORAGE_KEY) || "http://127.0.0.1:8787";
    } catch {
        return "http://127.0.0.1:8787";
    }
}

export function saveLocalProviderUrl(value: string) {
    const normalized = normalizeLocalProviderUrl(value);
    window.localStorage.setItem(STORAGE_KEY, normalized);
    return normalized;
}

export function getLocalProviderToken() {
    try {
        return window.sessionStorage.getItem(TOKEN_KEY) || "";
    } catch {
        return "";
    }
}

export function clearLocalProviderToken() {
    window.sessionStorage.removeItem(TOKEN_KEY);
}

function setLocalProviderToken(token: string) {
    window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function normalizeLocalProviderUrl(value: string) {
    const parsed = new URL(value.trim());
    if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) throw new Error("本地生成服务地址无效");
    parsed.pathname = parsed.pathname.replace(/\/+$/u, "");
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString().replace(/\/$/u, "");
}

async function request<T>(baseUrl: string, path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    const token = getLocalProviderToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${normalizeLocalProviderUrl(baseUrl)}${path}`, { ...init, headers });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (response.status === 401) clearLocalProviderToken();
        const detail = typeof body.detail === "string" ? body.detail : `本地生成服务请求失败（${response.status}）`;
        throw new Error(detail);
    }
    return body as T;
}

export function getLocalProviderHealth(baseUrl: string) {
    return request<LocalProviderHealth>(baseUrl, "/api/health");
}

export function activateLocalProvider(baseUrl: string, license: string) {
    return request<LocalProviderSession>(baseUrl, "/api/admin/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license }),
    }).then((result) => {
        setLocalProviderToken(result.token);
        return result;
    });
}

export function getLocalProviderOverview(baseUrl: string) {
    return request<LocalProviderOverview>(baseUrl, "/api/admin/overview");
}

export function checkLocalProviderAccounts(baseUrl: string, accountIds: string[] = []) {
    return request<{ checked: number; failed: number }>(baseUrl, "/api/admin/accounts/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_ids: accountIds, concurrency: 6 }),
    });
}

export function claimWanRewards(baseUrl: string, accountIds: string[] = []) {
    return request(baseUrl, "/api/admin/wan/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_ids: accountIds, categories: ["daily", "weekly"], include_activities: true }),
    });
}

export function importLocalProviderAccounts(baseUrl: string, files: File[], image = false) {
    const form = new FormData();
    form.append("provider", "vidu-cookie");
    form.append("credit_profile", image ? "image" : "video");
    files.forEach((file) => form.append("files", file, file.name));
    return request<{ count: number; skipped: unknown[]; failed: Array<{ file: string; error: string }> }>(baseUrl, "/api/admin/cookie-accounts/import", {
        method: "POST",
        body: form,
    });
}

export function importWanAccounts(baseUrl: string, files: File[]) {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    return request<{ count: number; skipped: unknown[]; failed: Array<{ file: string; error: string }> }>(baseUrl, "/api/admin/accounts/import", {
        method: "POST",
        body: form,
    });
}
