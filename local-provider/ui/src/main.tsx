import "@fontsource-variable/inter";
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, ArrowDownToLine, Check, CircleHelp, Clock3,
  Coins, ExternalLink, Film, Image, LayoutDashboard, LockKeyhole,
  LogOut, Play, RefreshCw, Settings2, ShieldCheck, SlidersHorizontal,
  Sparkles, Upload, Video, Wallet, X,
} from "lucide-react";
import "./style.css";
import { ServiceSettings, type RuntimeSettings, type SettingsSnapshot } from "./service-settings";
import { FileStorage } from "./file-storage";

type Account = { id: string; name: string; provider: string; credential_kind: string; credit_profile: string; enabled: number; balance: number | null; video_balance: number | null; image_balance: number | null; checked_at: string | null; video_checked_at: string | null; image_checked_at: string | null; status: string; auth_state: string; auth_error: string; check_error: string; q4_trials: { remaining_by_resolution?: Record<string, number>; checked_at?: string }; image_trials: Record<string, unknown>; claw_state: Record<string, unknown>; can_login: boolean; wan_state: WanRewardState };
type Model = { id: string; name: string; rate: number; duration_min: number; duration_max: number };
type ProviderModel = { id: string; provider: string; name: string; rate: number; duration_min: number; duration_max: number; resolutions?: string[]; ratios?: string[]; max_references?: number };
type Overview = { activated: boolean; dev_mode: boolean; client_connected: boolean; accounts: Account[]; tasks: { status: string; count: number }[]; models: Model[]; providers: ProviderModel[] } & SettingsSnapshot;
type Job = { id: string; provider: string; task_type: string; model: string; status: string; progress: number; account_id: string | null; upstream_id: string | null; error: string | null; created_at: string; result_ready: boolean };
type Reward = { key: string; title: string; category: string; period: string; reward: number | null; status: "claimed" | "claimable" | "pending" };
type ActivityOffer = { id: number; title: string; description: string; reward: number | null; registered: boolean };
type WanRewardState = { tasks?: Reward[]; activities?: ActivityOffer[]; experiences?: { id: string; title: string; status: string; task_id?: string }[]; trial?: { remaining?: number }; errors?: string[]; error?: string; checked_at?: string; last_action?: { error?: string; checked_at: string; results: { status: string; key?: string; operation?: string; activity_id?: number; redemption?: boolean; experience_id?: string; task_id?: string; reward?: number; error?: string }[] } };
type Health = { activated: boolean; dev_mode: boolean; service?: string };
type ActivationResult = { activated: boolean; token: string; session_expires_at: string };
type View = "overview" | "wan" | "video" | "image" | "tasks" | "settings";
const NAV: { id: View; label: string; icon: React.ElementType; section?: string }[] = [
  { id: "overview", label: "总览", icon: LayoutDashboard, section: "工作区" },
  { id: "tasks", label: "生成任务", icon: Activity },
  { id: "wan", label: "Wan 视频", icon: Film, section: "生成渠道" },
  { id: "video", label: "Q4 / Claw", icon: Video },
  { id: "image", label: "图片生成", icon: Image },
  { id: "settings", label: "服务设置", icon: Settings2, section: "系统" },
];
const STATUS: Record<string, string> = {
  queued: "排队中", preparing: "准备中", submitting: "正在提交", submission_unknown: "提交待核实", submitted: "已提交",
  processing: "生成中", downloading: "下载中", completed: "已完成", failed: "失败",
  poll_failed: "查询中断", download_failed: "下载失败", cancel_requested: "停止查询中", cancelled: "已停止",
};
let adminToken = sessionStorage.getItem("zaowu-admin-token") || "";
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...(adminToken ? { Authorization: `Bearer ${adminToken}` } : {}), ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401 || (response.status === 403 && path.startsWith("/admin/") && path !== "/admin/activate")) {
      adminToken = "";
      sessionStorage.removeItem("zaowu-admin-token");
    }
    const body = await response.json().catch(() => ({}));
    throw new Error(response.status === 401 ? "会话已失效，请重新输入卡密" : typeof body.detail === "string" ? body.detail : `请求失败 (${response.status})`);
  }
  return response.json();
}
const post = <T,>(path: string, data?: unknown) => api<T>(path, { method: "POST", ...(data !== undefined ? { body: JSON.stringify(data) } : {}) });
const fmtDate = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "尚未核查";
const shortId = (value: string) => value.slice(0, 8);

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [signedIn, setSignedIn] = useState(Boolean(adminToken));
  const [recoveringSession, setRecoveringSession] = useState(Boolean(window.zaowuProvider && !adminToken));
  const [license, setLicense] = useState("");
  const [view, setView] = useState<View>("overview");
  const [providerChannel, setProviderChannel] = useState<"q4-video" | "claw-video">("q4-video");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [wanRewardState, setWanRewardState] = useState<Record<string, WanRewardState>>({});
  const [redemptionCode, setRedemptionCode] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [generatedToken, setGeneratedToken] = useState("");
  const [accountFiles, setAccountFiles] = useState<File[]>([]);
  const accountFileInput = useRef<HTMLInputElement>(null);
  const [importFailures, setImportFailures] = useState<{ file: string; error: string }[]>([]);
  const [cookieFiles, setCookieFiles] = useState<File[]>([]);
  const cookieFileInput = useRef<HTMLInputElement>(null);
  const [cookieImportFailures, setCookieImportFailures] = useState<{ file: string; error: string }[]>([]);

  const reload = async () => {
    const [info, taskData] = await Promise.all([
      api<Overview>("/admin/overview"),
      api<{ tasks: Job[] }>("/admin/tasks"),
    ]);
    setOverview(info); setJobs(taskData.tasks);
  };
  useEffect(() => { api<typeof health>("/health").then(setHealth).catch((error) => setMessage(error.message)); }, []);
  useEffect(() => {
    if (signedIn || !window.zaowuProvider) return;
    let cancelled = false;
    setRecoveringSession(true);
    void window.zaowuProvider.restoreSession().then((result) => {
      if (cancelled || !result) return;
      adminToken = result.token;
      sessionStorage.setItem("zaowu-admin-token", adminToken);
      setHealth((current) => ({ ...current, activated: result.activated, dev_mode: false }));
      setRecoveringSession(false);
      setSignedIn(true);
      setMessage("");
    }).catch((error) => {
      if (!cancelled) setMessage(error instanceof Error ? error.message : "已保存卡密未能验证，请重新输入卡密");
    }).finally(() => {
      if (!cancelled) setRecoveringSession(false);
    });
    return () => { cancelled = true; };
  }, [signedIn]);
  useEffect(() => {
    if (!signedIn) return;
    const handleError = (error: Error) => {
      setMessage(error.message);
      if (!adminToken) setSignedIn(false);
    };
    reload().catch(handleError);
    const timer = window.setInterval(() => reload().catch(handleError), 5000);
    return () => window.clearInterval(timer);
  }, [signedIn]);
  const action = async (key: string, execute: () => Promise<unknown>, success: string) => {
    setBusy(key); setMessage("");
    try { const result = await execute(); setMessage(typeof result === "string" ? result : success); }
    catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
      if (!adminToken) setSignedIn(false);
    }
    finally {
      try { if (adminToken) await reload(); }
      catch (error) { setMessage(error instanceof Error ? error.message : "表格刷新失败"); if (!adminToken) setSignedIn(false); }
      setBusy("");
    }
  };
  const acceptActivation = (result: ActivationResult) => {
    adminToken = result.token;
    sessionStorage.setItem("zaowu-admin-token", adminToken);
    setSignedIn(true);
    setHealth((current) => current && ({ ...current, activated: result.activated }));
    setLicense("");
  };
  const activate = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("activate");
    setMessage("");
    try {
      const result = await post<ActivationResult>("/admin/activate", { license });
      await window.zaowuProvider?.setAutomaticLogin(true);
      acceptActivation(result);
      setMessage("卡密验证成功，已进入管理台");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "卡密验证失败");
    } finally {
      setBusy("");
    }
  };
  const importAccounts = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!accountFiles.length) {
      setMessage("请选择 .vault 或 .json 账号文件");
      return;
    }
    setBusy("import");
    setMessage("");
    setImportFailures([]);
    try {
      const body = new FormData();
      accountFiles.forEach((file) => body.append("files", file, file.name));
      const result = await api<{ count: number; imported: unknown[]; skipped: unknown[]; failed: { file: string; error: string }[] }>("/admin/accounts/import", { method: "POST", body });
      setImportFailures(result.failed);
      setAccountFiles((files) => files.filter((file) => result.failed.some((item) => item.file === file.name)));
      await reload();
      setMessage(`已导入 ${result.count} 个账号，跳过 ${result.skipped.length} 个，失败 ${result.failed.length} 个`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "批量导入失败");
      if (!adminToken) setSignedIn(false);
    } finally {
      setBusy("");
    }
  };
  const importCookieAccounts = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!cookieFiles.length) { setMessage("请选择 Cookie JSON 或 custom-video 账号包"); return; }
    setBusy("cookie-import"); setMessage("");
    setCookieImportFailures([]);
    try {
      const body = new FormData();
      body.append("provider", "vidu-cookie");
      body.append("credit_profile", view === "image" ? "image" : "video");
      cookieFiles.forEach((file) => body.append("files", file, file.name));
      const result = await api<{ count: number; skipped: unknown[]; failed: { file: string; error: string }[] }>("/admin/cookie-accounts/import", { method: "POST", body });
      setCookieImportFailures(result.failed);
      setCookieFiles((files) => files.filter((file) => result.failed.some((item) => item.file === file.name)));
      await reload();
      setMessage(`已导入 ${result.count} 个账号，跳过 ${result.skipped.length} 个，失败 ${result.failed.length} 个`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Cookie 账号导入失败");
    } finally { setBusy(""); }
  };
  const signOut = async () => {
    setBusy("logout");
    try {
      await window.zaowuProvider?.setAutomaticLogin(false);
      await post("/admin/logout");
      adminToken = "";
      sessionStorage.removeItem("zaowu-admin-token");
      setGeneratedToken("");
      setLicense("");
      setOverview(null);
      setJobs([]);
      setWanRewardState({});
      setLoginPassword("");
      setRedemptionCode("");
      setAccountFiles([]);
      setImportFailures([]);
      setCookieFiles([]);
      setCookieImportFailures([]);
      setSignedIn(false);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "退出失败，请重试");
      if (!adminToken) setSignedIn(false);
    } finally {
      setBusy("");
    }
  };
  const wanAccounts = overview?.accounts.filter((account) => account.provider === "wan-video") || [];
  const activeTasks = jobs.filter((job) => !["completed", "failed", "cancelled"].includes(job.status)).length;
  const totalBalance = wanAccounts.reduce((sum, account) => sum + (account.balance || 0), 0);
  const checkAccounts = (ids: string[], success = "账号核查完成", concurrency?: number) => action("check-accounts", async () => {
    const result = await post<{ checked: number; failed: number }>("/admin/accounts/check", { account_ids: ids, concurrency });
    if (result.failed) throw new Error(`核查完成：成功 ${result.checked} 个，失败 ${result.failed} 个；请查看账号状态`);
  }, success);
  const setProfile = (accountId: string, creditProfile: string) => action("profile", () => post(`/admin/accounts/${accountId}/credit-profile`, { credit_profile: creditProfile }), "积分用途已更新");
  const loadWanRewardState = async (accountIds: string[]) => {
    const entries = await Promise.all(accountIds.map(async (accountId) => {
      try {
        const data = await api<{ tasks: Reward[]; activities: ActivityOffer[]; experiences?: WanRewardState["experiences"]; trial?: WanRewardState["trial"]; errors?: string[] }>(`/admin/accounts/${accountId}/rewards`);
        return [accountId, { tasks: data.tasks || [], activities: data.activities || [], experiences: data.experiences || [], trial: data.trial, errors: data.errors || [], checked_at: new Date().toISOString() } satisfies WanRewardState] as const;
      } catch (error) {
        return [accountId, { tasks: [], activities: [], error: error instanceof Error ? error.message : "任务读取失败", checked_at: new Date().toISOString() } satisfies WanRewardState] as const;
      }
    }));
    setWanRewardState((current) => ({ ...current, ...Object.fromEntries(entries) }));
  };
  const claimWanPool = (categories: string[], options: { include_activities?: boolean; redemption_code?: string; experience_ids?: string[]; label?: string } = {}) => action("wan-claim", async () => {
    const result = await post<{ failed: number; claimed: number; checked: number; skipped: number; accounts: { error?: string; state?: WanRewardState }[] }>("/admin/wan/claim", {
      account_ids: wanAccounts.map((account) => account.id),
      categories,
      include_activities: options.include_activities === true,
      redemption_code: options.redemption_code || "",
      experience_ids: options.experience_ids || [],
    });
    setWanRewardState({});
    if (options.redemption_code && !result.failed && !result.skipped) setRedemptionCode("");
    const error = result.accounts.find((account) => account.error)?.error;
    return `处理 ${result.checked} 个账号，确认领取 ${result.claimed} 项，失败或待核实 ${result.failed} 个，跳过 ${result.skipped} 个${error ? `；${error}` : ""}。余额与表格已更新`;
  }, options.label || (categories.length === 2 ? "每日 / 每周积分领取完成，余额已刷新" : `${categories[0] === "daily" ? "每日" : "每周"}积分领取完成，余额已刷新`));
  const claimWanAccount = (account: Account) => action(`wan-claim-${account.id}`, async () => {
    const result = await post<{ ok: boolean; skipped?: boolean; reason?: string; results: { status: string }[] }>(`/admin/accounts/${account.id}/claim`, {
      categories: ["daily", "weekly"],
    });
    setWanRewardState((current) => { const next = { ...current }; delete next[account.id]; return next; });
    if (result.skipped) return result.reason || "账号已跳过";
    if (!result.ok) throw new Error("部分操作失败或待核实，余额和结果已更新，请查看账号明细");
    return `已确认领取 ${result.results.filter((item) => item.status === "claimed").length} 项，表格已更新`;
  }, `${account.name} 的可领取积分已处理`);
  const loginWan = (ids: string[] = []) => action("wan-login", async () => {
    try {
      const result = await post<{ succeeded: number; failed: number; results: { error?: string }[] }>("/admin/wan/login", { account_ids: ids, password: loginPassword });
      return `重新登录完成：成功 ${result.succeeded} 个，失败 ${result.failed} 个${result.failed ? `；${result.results.find((item) => item.error)?.error}` : ""}。余额已核查`;
    } finally { setLoginPassword(""); }
  }, "登录及余额核查完成");

  if (recoveringSession || !health || !health.activated || !signedIn) return (
    <div className="login-layout">
      <div className="login-brand"><div className="brand-mark"><LockKeyhole size={20} strokeWidth={2.2} /></div><span>造物<span className="brand-muted"> / ZAOWU</span></span></div>
      <div className="login-area">
        <div className="login-icon"><LockKeyhole size={26} /></div>
        <span className="eyebrow">PRODUCT ACTIVATION</span>
        <h1>{recoveringSession ? "正在恢复登录" : "输入激活卡密"}</h1>
        <p>{recoveringSession ? "正在验证本机加密保存的卡密，请稍候…" : window.zaowuProvider ? "首次输入 zaowu 卡密，之后启动将自动验证并进入管理台。" : "输入 zaowu 卡密，验证成功后直接进入管理台。"}</p>
        {!recoveringSession && <form onSubmit={activate}>
          <label htmlFor="license">激活卡密</label>
          <input id="license" type="password" required autoComplete="off" value={license} onChange={(event) => setLicense(event.target.value)} placeholder="输入卡密" />
          <button className="button primary wide" disabled={!health || busy === "activate"}>{!health ? "连接服务中…" : busy === "activate" ? "验证中…" : "验证并进入"}</button>
        </form>}
        {message && <p className="form-error">{message}</p>}
      </div>
      <div className="login-footer">卡密标识 · zaowu · 仅允许本机访问</div>
    </div>
  );

  return (
    <div className="shell">
      <header className="admin-header">
        <div className="admin-header-inner">
          <div className="admin-brand-wrap">
            <div className="admin-brand-icon"><Sparkles size={15} /></div>
            <strong className="admin-brand">造物</strong>
            <span className="admin-username">本地生成服务</span>
          </div>
          <nav className="admin-nav" aria-label="主导航">
            {NAV.map(({ id, label }) => <button type="button" key={id} onClick={() => setView(id)} className={`admin-nav-link ${view === id ? "active" : ""}`} aria-current={view === id ? "page" : undefined}>{label}{id === "tasks" && activeTasks > 0 && <span className="nav-badge">{activeTasks}</span>}</button>)}
          </nav>
          <div className="admin-header-right">
            <span className="service-state"><span className={`pulse ${overview?.activated ? "good" : "warn"}`} />{overview?.activated ? "已激活" : "待激活"}</span>
            <button type="button" className="admin-header-control admin-header-icon-btn" title="刷新数据" onClick={() => reload()}><RefreshCw size={14} /></button>
            <button type="button" className="admin-header-control" disabled={!!busy} onClick={() => void signOut()}><LogOut size={13} /> 退出</button>
          </div>
        </div>
      </header>
      <main className="admin-main">
        <div className="content">
          {message && <div className="notice"><span>{message}</span><button type="button" title="关闭提示" onClick={() => setMessage("")}><X size={16} /></button></div>}
          {view === "overview" && <>
            <div className="page-heading"><div><span className="eyebrow">WORKSPACE / OVERVIEW</span><h1>运行总览</h1><p>本地服务、账号和生成任务的实时状态。</p></div><button type="button" className="button outline" onClick={() => setView("settings")}><Settings2 size={16} /> 服务设置</button></div>
            <div className="metrics">
              <div className="metric"><div className="metric-label"><span>Wan 账号</span><Wallet size={17} /></div><strong>{wanAccounts.length}<span className="unit">个</span></strong><div className="metric-foot">{wanAccounts.filter((a) => a.status === "ready").length} 个已核查可用</div></div>
              <div className="metric"><div className="metric-label"><span>已核查余额</span><Coins size={17} /></div><strong>{totalBalance.toLocaleString()}<span className="unit">积分</span></strong><div className="metric-foot">以各账号最近一次核查结果为准</div></div>
              <div className="metric"><div className="metric-label"><span>进行中的任务</span><Activity size={17} /></div><strong>{activeTasks}<span className="unit">项</span></strong><div className="metric-foot">{jobs.filter((j) => j.status === "completed").length} 项已完成</div></div>
              <div className="metric"><div className="metric-label"><span>造物连接</span><ShieldCheck size={17} /></div><strong className="metric-text">{overview?.client_connected ? "令牌已配置" : "待配置"}</strong><div className="metric-foot">只允许本地客户端访问</div></div>
            </div>
            <div className="section-heading"><div><h2>最近任务</h2><p>从提交到下载，所有状态保留在本地。</p></div><button className="text-button" onClick={() => setView("tasks")}>查看全部 <ExternalLink size={15} /></button></div>
            <TaskTable jobs={jobs.slice(0, 6)} accounts={overview?.accounts || []} onResume={(id) => action("resume", () => post(`/admin/tasks/${id}/resume`), "已继续查询任务")} onCancel={(id) => action("cancel", () => post(`/admin/tasks/${id}/cancel`), "已停止本地任务")} onDownload={downloadResult} />
          </>}
          {view === "wan" && <>
            <div className="page-heading"><div><span className="eyebrow">PROVIDERS / WAN VIDEO</span><h1>Wan 视频</h1><p>批量管理账号、核查余额及领取每日 / 每周积分。</p></div><span className="badge green large"><span className="pulse good" /> 已接入</span></div>
             <section id="accounts" className="block">
              <div className="section-heading">
                <div><h2>Wan 账号池 <span className="badge neutral">{wanAccounts.length} 个账号</span></h2><p>核查、领取和登录后自动更新下方表格。积分以网站钱包最近一次核查为准。</p></div>
                <span className="badge green large">余额合计 {totalBalance.toLocaleString()} 积分</span>
              </div>
                <div className="section-actions wan-pool-toolbar" aria-label="账号池批量操作">
                  <button type="button" className="button outline" disabled={!!busy || !wanAccounts.length} onClick={() => checkAccounts(wanAccounts.map((account) => account.id), "全部余额核查完成")}><RefreshCw size={15} /> 核查全部</button>
                  <button type="button" className="button primary" disabled={!!busy || !wanAccounts.length} onClick={() => claimWanPool(["daily", "weekly"])}><Coins size={15} /> 领取积分</button>
                  <button type="button" className="button outline" disabled={!!busy || !wanAccounts.length} onClick={() => claimWanPool(["daily"])}><Coins size={15} /> 领取每日</button>
                  <button type="button" className="button outline" disabled={!!busy || !wanAccounts.length} onClick={() => claimWanPool(["weekly"])}><Coins size={15} /> 领取每周</button>
                  <button type="button" className="button outline" disabled={!!busy || !wanAccounts.length} onClick={() => claimWanPool([], { include_activities: true, label: "活动报名完成" })}><Sparkles size={15} /> 报名免费活动</button>
                  <button type="button" className="button outline" disabled={!!busy || !wanAccounts.length} onClick={() => action("wan-rewards-all", () => loadWanRewardState(wanAccounts.map((account) => account.id)), "任务状态已更新")}><RefreshCw size={15} /> 刷新任务</button>
                  <button type="button" className="button outline" disabled={!!busy || !wanAccounts.some((account) => account.status === "expired")} onClick={() => { if (window.confirm("确认清理 Wan 登录失效账号？本地会保留加密归档，余额不足账号会保留。")) action("clean-invalid", () => post("/admin/accounts/clean-invalid?provider=wan-video"), "Wan 失效账号已清理并归档"); }}><X size={15} /> 清理失效</button>
                </div>
              {busy && <p className="pool-operation" role="status"><RefreshCw size={14} className="spin" /> {busy === "wan-login" ? "正在登录并核查余额…" : busy.startsWith("wan-claim") ? "正在领取并复核到账，表格会逐步更新…" : "正在处理账号池操作…"}</p>}
              <form className="inline-form account-import-form" onSubmit={importAccounts}>
                <div className="field grow">
                  <label htmlFor="account-files">批量导入账号</label>
                  <input ref={accountFileInput} id="account-files" name="account-files" className="file-picker-input" type="file" accept=".vault,.json" multiple tabIndex={-1} disabled={!!busy} onChange={(event) => {
                    const files = Array.from(event.currentTarget.files || []);
                    if (files.length) { setAccountFiles(files); setImportFailures([]); }
                    event.currentTarget.value = "";
                  }} />
                  <div className="file-picker">
                    <button type="button" className="button outline" disabled={!!busy} onClick={() => accountFileInput.current?.click()}><Upload size={15} /> 选择账号文件</button>
                    <span className="file-picker-status" role="status">{accountFiles.length ? `已选择 ${accountFiles.length} 个文件` : "尚未选择文件"}</span>
                    {!!accountFiles.length && <button type="button" className="text-button" disabled={!!busy} onClick={() => { setAccountFiles([]); setImportFailures([]); }}>清空</button>}
                  </div>
                  {!!accountFiles.length && <ul className="import-file-list" aria-label="待导入文件">{accountFiles.map((file, index) => <li key={`${file.name}-${index}`} title={file.name}>{file.name}</li>)}</ul>}
                  <small className="field-hint">可一次选择多个 .vault / .json 文件，加密账号自动解密。</small>
                </div>
                <button className="button primary" disabled={!!busy || !accountFiles.length}><Upload size={16} /> {busy === "import" ? "导入中…" : "批量导入"}</button>
              </form>
              {!!importFailures.length && <div className="import-errors" role="alert"><strong>以下文件导入失败，请检查后重试</strong><ul>{importFailures.map((item, index) => <li key={`${item.file}-${index}`}><span>{item.file}</span>：{item.error}</li>)}</ul></div>}
              <details className="account-tools"><summary>登录恢复、兑换码与免费体验</summary><div className="account-tools-body">
                <form className="inline-form" onSubmit={(event) => { event.preventDefault(); loginWan(); }}>
                  <div className="field grow"><label htmlFor="wan-login-password">Wan 账号登录密码</label><input id="wan-login-password" type="password" autoComplete="off" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} placeholder="仅用于本次恢复上游账号登录，使用后清空" /><small className="field-hint">只处理登录失效账号，导入文件需包含邮箱。</small></div>
                  <span className="field-hint">登录并发数使用服务设置中的 Wan 配置。</span>
                  <button className="button outline" disabled={!loginPassword || !!busy || !wanAccounts.some((account) => account.status === "expired" && account.can_login)}>登录失效账号</button>
                </form>
                <div className="inline-form"><div className="field grow"><label htmlFor="wan-redemption-code">兑换码</label><input id="wan-redemption-code" maxLength={128} autoComplete="off" value={redemptionCode} onChange={(event) => setRedemptionCode(event.target.value)} placeholder="输入后对可用账号核销" /></div><button type="button" className="button outline" disabled={!redemptionCode.trim() || !!busy || !wanAccounts.length} onClick={() => claimWanPool([], { redemption_code: redemptionCode.trim(), label: "兑换完成" })}>兑换码领取</button></div>
                <div className="experience-actions"><div><strong>免费体验任务</strong><p>使用网站剩余体验次数，提交后保留任务编号；完成后复核可领取的积分奖励。</p></div><div className="section-actions">{[1, 2, 3].map((index) => <button key={index} type="button" className="button outline" disabled={!!busy || !wanAccounts.length} onClick={() => claimWanPool([], { experience_ids: [`hidden_free_${index}`], label: `免费体验 ${index} 已处理` })}>体验任务 {index}</button>)}</div></div>
              </div></details>
              <div className="table-scroll"><table className="wan-account-table"><thead><tr><th>账号</th><th>状态</th><th>积分余额</th><th>积分任务 / 体验</th><th>最近核查</th><th className="right">操作</th></tr></thead><tbody>{!wanAccounts.length ? <tr><td colSpan={6} className="empty">暂无 Wan 账号，请批量导入账号文件。</td></tr> : wanAccounts.map((account) => {
                const reward = wanRewardState[account.id] || account.wan_state;
                const daily = reward?.tasks?.filter((item) => item.category === "daily" && item.status === "claimable").length || 0;
                const weekly = reward?.tasks?.filter((item) => item.category === "weekly" && item.status === "claimable").length || 0;
                const activities = reward?.activities?.filter((item) => !item.registered).length || 0;
                const error = reward?.error || reward?.errors?.join("；") || account.wan_state?.last_action?.error || account.auth_error || account.check_error;
                const invalid = account.status === "expired" || account.auth_state === "expired";
                return <React.Fragment key={account.id}><tr>
                  <td><strong className="table-primary">{account.name}</strong><small className="table-secondary">{shortId(account.id)}</small></td>
                  <td><span className={`badge ${invalid || account.status === "check_error" ? "amber" : account.enabled && account.status === "ready" ? "green" : "neutral"}`}>{!account.enabled ? "已停用" : invalid ? "登录失效" : account.status === "check_error" ? "核查失败" : account.status === "ready" ? account.balance != null && account.balance <= (overview?.wan_settings.min_balance || 0) ? "余额预警" : "正常" : "待核查"}</span>{error && <small className="table-secondary task-error" title={error}>{error}</small>}</td>
                  <td className="number">{account.balance == null ? "—" : account.balance.toLocaleString()}</td>
                  <td>{reward?.checked_at ? <><small className="table-secondary task-summary">可领：每日 {daily} · 每周 {weekly}</small><small className="table-secondary">活动 {activities} · 体验余量 {reward.trial?.remaining ?? "—"}</small></> : <span className="secondary">尚未读取任务</span>}</td>
                  <td className="secondary">{fmtDate(account.checked_at)}</td>
                  <td className="right actions"><button type="button" disabled={!!busy} onClick={() => checkAccounts([account.id], `${account.name} 余额已更新`)}>核查</button><button type="button" disabled={!!busy} onClick={() => action(`wan-rewards-${account.id}`, () => loadWanRewardState([account.id]), `${account.name} 任务已更新`)}>刷新任务</button>{invalid ? <button type="button" disabled={!!busy || !account.can_login || !loginPassword} title="在上方登录恢复中输入本次账号密码" onClick={() => loginWan([account.id])}>重新登录</button> : <><button type="button" disabled={!!busy || !account.enabled} onClick={() => claimWanAccount(account)}>领取积分</button><button type="button" disabled={!!busy} onClick={() => action("wan-browser", async () => { await post(`/admin/accounts/${account.id}/browser`); await post(`/admin/accounts/${account.id}/balance`); }, "已打开该账号成长中心，余额已刷新")}>打开账号</button></>}<button type="button" disabled={!!busy} onClick={() => action("toggle", () => post(`/admin/accounts/${account.id}/toggle`), "账号状态已更新")}>{account.enabled ? "停用" : "启用"}</button><button type="button" className="danger" disabled={!!busy} onClick={() => { if (window.confirm(`确认删除账号「${account.name}」？`)) action("delete", () => api(`/admin/accounts/${account.id}`, { method: "DELETE" }), "账号已删除"); }}>删除</button></td>
                </tr>{reward?.checked_at && <tr className="wan-detail-row"><td colSpan={6}><details><summary>任务与操作明细 · {account.name}</summary><div className="wan-detail-grid">
                  <div><strong>每日 / 每周任务</strong>{reward.tasks?.length ? reward.tasks.map((item) => <p className="task-detail-item" key={item.key}><span>{item.title}</span><span>{item.reward ?? "—"} 分 · {item.status === "claimed" ? "已领取" : item.status === "claimable" ? "可领取" : "进行中"}</span></p>) : <p className="secondary">暂无任务</p>}</div>
                  <div><strong>活动与体验</strong>{reward.activities?.map((item) => <p className="task-detail-item" key={item.id}><span>{item.title}</span><span>{item.registered ? "已报名" : "可报名"}</span></p>)}{reward.experiences?.map((item) => <p className="task-detail-item" key={item.id}><span>{item.title}</span><span>{STATUS[item.status] || (item.status === "available" ? "未提交" : item.status === "unknown" ? "待核实" : item.status)}{item.task_id && <small className="table-secondary" title={item.task_id}>任务 {shortId(item.task_id)}</small>}</span></p>)}</div>
                  <div className="operation-results"><strong>上次操作结果</strong>{account.wan_state?.last_action?.results?.length ? account.wan_state.last_action.results.map((item, index) => <p key={index} className={`task-detail-item ${["unknown", "failed", "rejected", "error"].includes(item.status) ? "task-error" : ""}`}><span>{item.operation || item.key || item.experience_id || (item.redemption ? "兑换码" : `活动 ${item.activity_id}`)}</span><span>{item.error || ({ claimed: "已领取", registered: "报名成功", already_registered: "已报名", skipped: "已跳过", unknown: "待核实", failed: "失败", rejected: "未兑换", not_eligible: "条件不符" } as Record<string, string>)[item.status] || STATUS[item.status] || item.status}</span></p>) : <p className="secondary">暂无领取记录</p>}</div>
                </div></details></td></tr>}</React.Fragment>;
              })}</tbody></table></div>
             </section>
          </>}
          {view === "tasks" && <><div className="page-heading"><div><span className="eyebrow">WORKSPACE / TASKS</span><h1>生成任务</h1><p>上游任务、下载和恢复状态。</p></div><button className="button outline" onClick={reload}><RefreshCw size={16} /> 刷新</button></div><TaskTable jobs={jobs} accounts={overview?.accounts || []} onResume={(id) => action("resume", () => post(`/admin/tasks/${id}/resume`), "任务查询已恢复")} onCancel={(id) => action("cancel", () => post(`/admin/tasks/${id}/cancel`), "已停止本地任务")} onDownload={downloadResult} /></>}
          {(view === "video" || view === "image") && (() => {
            const channel = view === "image" ? "image" : providerChannel;
            const channelSettings = channel === "image" ? overview?.image_settings : overview?.video_settings;
            const channelAccounts = overview?.accounts.filter((account) => account.provider === "vidu-cookie") || [];
            const eligibleAccounts = channelAccounts.filter((account) => account.credit_profile === "shared" || account.credit_profile === (channel === "image" ? "image" : "video"));
            const title = channel === "image" ? "图片生成" : channel === "q4-video" ? "Q4 视频" : "Claw 视频";
            return <><div className="page-heading"><div><span className="eyebrow">PROVIDERS / {channel.toUpperCase()}</span><h1>{title}</h1><p>{view === "video" ? "共用账号池，管理视频积分、免费次数与账号操作。" : "共用账号池，管理图片积分、试用次数与图片小号。"}</p></div><span className="badge green large"><span className="pulse good" /> 已接入</span></div>
              {view === "video" && <div className="subnav" role="group" aria-label="视频渠道"><button type="button" aria-pressed={channel === "q4-video"} className={channel === "q4-video" ? "active" : ""} onClick={() => setProviderChannel("q4-video")}>Q4</button><button type="button" aria-pressed={channel === "claw-video"} className={channel === "claw-video" ? "active" : ""} onClick={() => setProviderChannel("claw-video")}>Claw</button></div>}
              <section className="block">
                <div className="section-heading">
                  <div><h2>通用 Cookie 账号池</h2><p>Q4、Claw 和图片共用 Cookie；积分与试用额度读取上游实际结果。登录失效、访问受阻和部分核查失败分别显示。</p></div>
                </div>
                <div className="section-actions wan-pool-toolbar" aria-label="Cookie 账号池操作">
                  <button type="button" className="button outline" disabled={!!busy} onClick={() => action("reload-pool", async () => {}, "账号池已重新加载")}><RefreshCw size={15} /> 重新加载</button>
                  <button type="button" className="button outline" disabled={!!busy || !channelAccounts.length} onClick={() => checkAccounts(channelAccounts.map((account) => account.id), "全部积分与余量核查完成", channelSettings?.check_concurrency)}><RefreshCw size={15} /> {channel === "image" ? "核查积分" : "核查全部余量"}</button>
                  {channel === "claw-video" && <button type="button" className="button primary" disabled={!!busy || !eligibleAccounts.length} onClick={() => { if (window.confirm("确认向视频账号发送固定「你好」消息？将创建上游会话，只处理余额大于 180 分且未成功发送的账号。")) action("claw-batch", async () => { const result = await post<{ succeeded: number; failed: number; skipped: number }>("/admin/claw/test-messages", { account_ids: eligibleAccounts.map((account) => account.id) }); return `批量发送完成：成功 ${result.succeeded} 个，跳过 ${result.skipped} 个，失败 ${result.failed} 个；详情见账号消息状态`; }, "批量消息处理完成"); }}><Play size={15} /> 批量发送消息（&gt;180分）</button>}
                  {channel === "image" && <button type="button" className="button outline" disabled={!!busy || !channelAccounts.length} onClick={() => { if (window.confirm("确认清理已核查积分不足 50 分且没有剩余试用次数的图片小号？通用、视频及忙碌账号保留，清理记录加密归档。")) action("clean-image-exhausted", async () => { const result = await post<{ count: number; skipped: unknown[] }>("/admin/image-accounts/clean-exhausted"); return `已清理 ${result.count} 个耗尽图片账号，跳过 ${result.skipped.length} 个`; }, "耗尽图片账号清理完成"); }}><X size={15} /> 清理已耗尽</button>}
                  <button type="button" className="button outline" disabled={!!busy || !channelAccounts.length} onClick={() => { if (window.confirm(channel === "image" ? "确认清理 Cookie 池中凭证缺失或登录失效的账号？网络或接口核查失败账号保留，清理记录加密归档。" : "确认清理 Cookie 池中凭证缺失、登录失效及已核查为零分的视频小号？通用和图片账号的零分记录会保留，已清理记录保留加密归档。")) action("clean-invalid", async () => { const result = await post<{ count: number; skipped: unknown[] }>(`/admin/accounts/clean-invalid?provider=vidu-cookie${channel === "image" ? "" : "&include_exhausted=true"}`); return `已清理 ${result.count} 个账号，跳过 ${result.skipped.length} 个`; }, "失效账号清理完成"); }}><X size={15} /> {channel === "image" ? "清理失效账号" : "清理失效 / 耗尽账号"}</button>
                  {view === "video" && <button type="button" className="button outline" disabled={!!busy || !channelAccounts.length} onClick={() => { if (window.confirm("重置 Cookie 账号核查与 Q4 免费次数缓存？账号、凭据和已发送消息记录会保留，忙碌账号跳过，之后请重新核查。")) action("reset-cookie", async () => { const result = await post<{ count: number; skipped: string[] }>("/admin/cookie-accounts/reset"); return `已重置 ${result.count} 个账号，跳过 ${result.skipped.length} 个；请重新核查余量`; }, "账号核查状态已重置"); }}><RefreshCw size={15} /> 重置状态</button>}
                </div>
                {busy && <p className="pool-operation" role="status"><RefreshCw size={14} className="spin" /> {busy === "claw-batch" ? "正在核查积分并发送消息，账号状态会逐步更新…" : "正在处理账号池操作…"}</p>}
                <form className="inline-form account-import-form" onSubmit={importCookieAccounts}>
                  <div className="field grow"><label htmlFor="cookie-files">批量导入 {channel === "image" ? "图片积分账号" : "视频积分账号"}</label>
                    <input ref={cookieFileInput} id="cookie-files" name="cookie-files" className="file-picker-input" type="file" accept=".json,.vault" multiple tabIndex={-1} disabled={!!busy} onChange={(event) => { const files = Array.from(event.currentTarget.files || []); if (files.length) { setCookieFiles(files); setCookieImportFailures([]); } event.currentTarget.value = ""; }} />
                    <div className="file-picker"><button type="button" className="button outline" disabled={!!busy} onClick={() => cookieFileInput.current?.click()}><Upload size={15} /> 选择账号文件</button><span className="file-picker-status" role="status">{cookieFiles.length ? `已选择 ${cookieFiles.length} 个文件` : "尚未选择文件"}</span>{!!cookieFiles.length && <button type="button" className="text-button" disabled={!!busy} onClick={() => { setCookieFiles([]); setCookieImportFailures([]); }}>清空</button>}</div>
                    {!!cookieFiles.length && <ul className="import-file-list" aria-label="待导入文件">{cookieFiles.map((file, index) => <li key={`${file.name}-${index}`}>{file.name}</li>)}</ul>}
                    <small className="field-hint">支持多个 JSON / 加密账号包；相同 Cookie 不重复保存，积分用途不会被静默改写。</small>
                  </div>
                  <button className="button primary" disabled={!!busy || !cookieFiles.length}><Upload size={16} /> {busy === "cookie-import" ? "导入中…" : "批量导入"}</button>
                </form>
                {!!cookieImportFailures.length && <div className="import-errors" role="alert"><strong>以下文件导入失败</strong><ul>{cookieImportFailures.map((item, index) => <li key={`${item.file}-${index}`}>{item.file}：{item.error}</li>)}</ul></div>}
                <div className="settings-inline-actions"><span className="field-hint">运行参数集中在服务设置中，图片与视频分别保存。</span><button type="button" className="button outline" onClick={() => setView("settings")}><Settings2 size={15} /> 服务设置</button></div>
                <div className="table-scroll"><table className="cookie-account-table">
                  <thead><tr><th>账号</th><th>积分用途</th><th>状态</th><th>{channel === "image" ? "图片积分" : "视频积分"}</th>{channel === "q4-video" && <th>Q4 免费次数</th>}{channel === "claw-video" && <><th>工作区</th><th>消息状态</th></>}<th>最近核查</th><th className="right">操作</th></tr></thead>
                  <tbody>{channelAccounts.length ? channelAccounts.map((account) => {
                    const amount = channel === "image" ? account.image_balance : account.video_balance;
                    const checked = (channel === "image" ? account.image_checked_at : account.video_checked_at) || account.checked_at;
                    const invalid = account.status === "expired" || account.auth_state === "missing";
                    const trialMap = account.q4_trials.remaining_by_resolution;
                    const error = account.auth_error || account.check_error;
                    const messageStatus = String(account.claw_state.test_message_status || "");
                    const messageError = String(account.claw_state.test_message_error || "");
                    const accountBusy = jobs.some((job) => job.account_id === account.id && !["completed", "failed", "cancelled"].includes(job.status)) || ["checking", "sending"].includes(messageStatus);
                    const exhausted = account.status === "ready" && amount != null && (channel === "image" ? amount < 50 : amount === 0);
                    return <tr key={account.id}>
                      <td><strong className="table-primary">{account.name}</strong><small className="table-secondary">{shortId(account.id)}</small></td>
                      <td><select className="profile-select" value={account.credit_profile} disabled={!!busy} onChange={(event) => setProfile(account.id, event.target.value)}><option value="shared">通用</option><option value="video">视频小号</option><option value="image">图片小号</option></select></td>
                      <td><span className={`badge ${invalid || exhausted || accountBusy ? "amber" : account.enabled && account.status === "ready" ? "green" : "neutral"}`}>{accountBusy ? "忙碌" : !account.enabled ? "已停用" : invalid ? account.auth_state === "missing" ? "凭证缺失" : "登录失效" : account.status === "check_error" ? "核查失败" : exhausted ? channel === "image" ? "积分不足 50" : "积分耗尽" : account.status === "ready" ? account.check_error ? "部分核查成功" : "空闲" : "待核查"}</span>{error && <small className="table-secondary task-error" title={error}>{error}</small>}</td>
                      <td className="number">{amount == null ? "—" : amount.toLocaleString()}</td>
                      {channel === "q4-video" && <td>{account.q4_trials.checked_at && trialMap ? ["540p", "720p", "1080p"].map((resolution) => <small className="table-secondary" key={resolution}>{resolution} · {trialMap[resolution] ?? "—"}</small>) : <span className="secondary">尚未核查</span>}</td>}
                      {channel === "claw-video" && <><td><span className="badge neutral">{account.claw_state.status === "active" ? "已激活" : account.claw_state.status === "inactive" ? "未激活" : "尚未核查"}</span>{!!account.claw_state.id && <small className="table-secondary">{String(account.claw_state.id)}</small>}</td><td><span className={`badge ${messageStatus === "success" ? "green" : ["failed", "unknown"].includes(messageStatus) ? "amber" : "neutral"}`}>{({ success: "发送成功", failed: "发送失败", unknown: "待核实，勿重发", checking: "积分核查中", sending: "发送中" } as Record<string, string>)[messageStatus] || "未发送"}</span>{messageError && <small className="table-secondary task-error" title={messageError}>{messageError}</small>}</td></>}
                      <td className="secondary">{fmtDate(checked)}</td>
                      <td className="right actions"><button type="button" disabled={!!busy} onClick={() => checkAccounts([account.id])}>核查</button>{view === "video" && <button type="button" disabled={!!busy} onClick={() => action("cookie-browser", () => post(`/admin/cookie-accounts/${account.id}/browser?channel=${channel}`), "已在独立浏览器打开账号；手动更新登录后请重新导入 Cookie 并核查")}>登录</button>}{channel === "claw-video" && <button type="button" disabled={!!busy || accountBusy || !account.enabled || account.credit_profile === "image" || messageStatus === "success" || messageStatus === "unknown"} onClick={() => action("claw-message", () => post("/admin/claw/test-message", { account_id: account.id }), "固定消息已发送，积分与状态已更新")}>发送消息</button>}<button type="button" disabled={!!busy || accountBusy} onClick={() => action("toggle", () => post(`/admin/accounts/${account.id}/toggle`), "账号状态已更新")}>{account.enabled ? "停用" : "启用"}</button><button type="button" className="danger" disabled={!!busy || accountBusy} onClick={() => { if (window.confirm(`确认删除账号「${account.name}」？`)) action("delete-cookie", () => api(`/admin/accounts/${account.id}`, { method: "DELETE" }), "账号已删除"); }}>删除</button></td>
                    </tr>;
                  }) : <tr><td colSpan={channel === "q4-video" ? 7 : channel === "claw-video" ? 8 : 6} className="empty">暂无账号，请先批量导入。</td></tr>}</tbody>
                </table></div>
              </section>
            </>;
          })()}
          {view === "settings" && <ServiceSettings snapshot={overview} busy={!!busy} onNotice={setMessage}
            save={async (kind, value) => {
              const result = await api<RuntimeSettings>(`/admin/${kind}-settings`, { method: "PUT", body: JSON.stringify(value) });
              await reload();
              return result;
            }}
            restartBrowser={async () => {
              const result = await post("/admin/video-browser/restart");
              await reload();
              return result;
            }}
            common={<>
              <FileStorage onNotice={setMessage} />
              <section className="block">
                <div className="section-heading"><div><h2>激活状态</h2><p>总卡密用于激活本地生成服务，重启后自动验证。</p></div>
                  <span className={`badge ${overview?.activated ? "green" : "amber"}`}>{overview?.activated ? "已激活" : "未激活"}</span>
                </div>
                <form className="inline-form" onSubmit={(event) => {
                  event.preventDefault();
                  void action("activate", async () => {
                    const result = await post<ActivationResult>("/admin/activate", { license });
                    await window.zaowuProvider?.setAutomaticLogin(true);
                    acceptActivation(result);
                  }, "激活成功");
                }}>
                  <div className="field grow"><label>更换卡密</label><input type="password" placeholder="输入新的 zaowu 卡密" value={license} onChange={(event) => setLicense(event.target.value)} required /></div>
                  <button className="button primary" disabled={!!busy}><LockKeyhole size={16} /> 验证激活</button>
                </form>
              </section>
              <section className="block">
                <div className="section-heading"><div><h2>造物连接</h2><p>造物自动读取已加密连接。重新生成令牌后，请在造物中同步模型。</p></div></div>
                <div className="connect-row"><span className="badge green">本机 · 127.0.0.1:8787</span>
                  <button type="button" className="button outline" disabled={!!busy} onClick={() => action("token", async () => setGeneratedToken((await post<{ token: string }>("/admin/client-token")).token), "已更新连接令牌")}>重新生成连接令牌</button>
                </div>
                {generatedToken && <div className="secret-value"><code>{generatedToken}</code><button type="button" title="复制令牌" onClick={() => navigator.clipboard.writeText(generatedToken)}>复制</button></div>}
              </section>
            </>}
          />}
        </div>
      </main>
    </div>
  );

  async function downloadResult(taskId: string, saveAs = false) {
    setBusy(`download-${taskId}`);
    try {
      if (window.zaowuProvider?.revealResult) {
        if (saveAs) {
          const result = await window.zaowuProvider.saveResultAs(taskId, adminToken);
          if (result.saved) setMessage("已另存为");
        } else {
          await window.zaowuProvider.revealResult(taskId, adminToken);
        }
        return;
      }
      const response = await fetch(`/api/admin/tasks/${taskId}/result`, { headers: { Authorization: `Bearer ${adminToken}` } });
      if (!response.ok) throw new Error("生成结果暂不可下载");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `${taskId}.${jobs.find((job) => job.id === taskId)?.task_type === "image" ? "png" : "mp4"}`; link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "下载失败");
    } finally {
      setBusy("");
    }
  }
}

function TaskTable({ jobs, accounts, onResume, onCancel, onDownload }: { jobs: Job[]; accounts: Account[]; onResume: (id: string) => void; onCancel: (id: string) => void; onDownload: (id: string, saveAs?: boolean) => void }) {
  const providerName = (provider: string) => provider === "image" ? "图片" : provider === "q4-video" ? "Q4" : provider === "claw-video" ? "Claw" : "Wan";
  return <div className="table-scroll"><table><thead><tr><th>任务</th><th>渠道 / 模型 / 账号</th><th>状态</th><th>进度</th><th>创建时间</th><th className="right">操作</th></tr></thead><tbody>{jobs.length ? jobs.map((job) => <tr key={job.id}><td><strong className="table-primary">{job.task_type === "image" ? "图片生成" : "视频生成"}</strong><small className="table-secondary">{shortId(job.id)}</small></td><td><span className="table-primary">{providerName(job.provider)} · {job.model}</span><small className="table-secondary">{accounts.find((a) => a.id === job.account_id)?.name || "待分配"}</small></td><td><span className={`badge ${job.status === "completed" ? "green" : job.status.includes("failed") || job.status === "submission_unknown" ? "amber" : "neutral"}`}>{STATUS[job.status] || job.status}</span>{job.error && <small className="table-secondary task-error" title={job.error}>{job.error}</small>}</td><td><div className="progress-line"><span style={{ width: `${job.progress}%` }} /></div><small className="table-secondary">{job.progress}%</small></td><td className="secondary">{fmtDate(job.created_at)}</td><td className="right actions">{job.result_ready ? <>
    <button type="button" onClick={() => onDownload(job.id)}>{window.zaowuProvider?.revealResult ? <ExternalLink size={14} /> : <ArrowDownToLine size={14} />} {window.zaowuProvider?.revealResult ? "打开所在文件夹" : "下载"}</button>
    {window.zaowuProvider?.saveResultAs ? <button type="button" onClick={() => onDownload(job.id, true)}><ArrowDownToLine size={14} /> 另存为</button> : null}
  </> : ["poll_failed", "download_failed"].includes(job.status) ? <button type="button" onClick={() => onResume(job.id)}><RefreshCw size={14} /> 继续</button> : ["queued", "preparing", "submitted", "processing", "downloading"].includes(job.status) ? <button type="button" onClick={() => { if (window.confirm("确认停止这个本地任务？已提交到上游的任务不会被远程取消。")) onCancel(job.id); }}><X size={14} /> 停止</button> : "—"}</td></tr>) : <tr><td colSpan={6} className="empty">暂无生成任务。</td></tr>}</tbody></table></div>;
}

createRoot(document.getElementById("root")!).render(<App />);
