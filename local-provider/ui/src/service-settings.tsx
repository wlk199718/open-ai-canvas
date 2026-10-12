import { useEffect, useState, type ReactNode } from "react";
import { Check, RefreshCw, Settings2 } from "lucide-react";

export type SettingsKind = "wan" | "video" | "image";
export type RuntimeSettings = {
  check_concurrency: number; request_timeout: number; upload_timeout: number;
  submit_timeout: number; download_timeout: number; poll_interval: number;
  task_timeout: number; min_balance: number; proxy_subscription_timeout: number;
  max_proxy_nodes: number; proxy_configured: boolean;
  retry_count?: number; compress_images?: boolean; login_concurrency?: number;
  claim_concurrency?: number; generation_timeout?: number; claw_message_workers?: number;
  browser_assist_enabled?: boolean; browser_window_count?: number;
};
export type SettingsSnapshot = {
  wan_settings: RuntimeSettings; video_settings: RuntimeSettings; image_settings: RuntimeSettings;
  browser_helper: { windows: number; browser_available: boolean };
};

const TITLES = { wan: "Wan", video: "Q4 / Claw", image: "图片" };
type NumericField = readonly [keyof RuntimeSettings, string, number, number, string?];
const NETWORK_FIELDS: NumericField[] = [
  ["request_timeout", "接口查询超时 / 秒", 10, 600],
  ["upload_timeout", "上传超时 / 秒", 30, 3600],
  ["submit_timeout", "提交超时 / 秒", 30, 600],
  ["download_timeout", "下载超时 / 秒", 30, 3600],
];

export function ServiceSettings({ snapshot, common, busy, save, restartBrowser, onNotice }: {
  snapshot: SettingsSnapshot | null;
  common: ReactNode;
  busy: boolean;
  save(kind: SettingsKind, value: Record<string, unknown>): Promise<RuntimeSettings>;
  restartBrowser(): Promise<unknown>;
  onNotice(message: string): void;
}) {
  const [tab, setTab] = useState<"common" | SettingsKind>("common");
  const [drafts, setDrafts] = useState<Partial<Record<SettingsKind, RuntimeSettings>>>({});
  const [dirty, setDirty] = useState<Partial<Record<SettingsKind, boolean>>>({});
  const [proxies, setProxies] = useState<Partial<Record<SettingsKind, string>>>({});
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!snapshot) return;
    setDrafts((current) => {
      const next = { ...current };
      for (const kind of ["wan", "video", "image"] as const) {
        if (!dirty[kind]) next[kind] = snapshot[`${kind}_settings`];
      }
      return next;
    });
  }, [snapshot, dirty]);

  const change = (kind: SettingsKind, key: keyof RuntimeSettings, value: number | boolean) => {
    setDrafts((current) => ({ ...current, [kind]: { ...current[kind]!, [key]: value } }));
    setDirty((current) => ({ ...current, [kind]: true }));
    setError("");
  };
  const renderNumber = (kind: SettingsKind, field: NumericField) => {
    const [key, label, min, max, hint] = field;
    return <div className="field" key={key}>
      <label htmlFor={`settings-${kind}-${key}`}>{label}</label>
      <input id={`settings-${kind}-${key}`} type="number" min={min} max={max} step="1" required
        value={Number(drafts[kind]?.[key] ?? min)} onChange={(event) => change(kind, key, Number(event.target.value))} />
      {hint && <small className="field-hint">{hint}</small>}
    </div>;
  };
  const saveCurrent = async (kind: SettingsKind) => {
    if (!drafts[kind]) return;
    setSaving(kind); setError("");
    const { proxy_configured: _configured, ...payload } = drafts[kind];
    try {
      const result = await save(kind, { ...payload, ...(proxies[kind] !== undefined ? { proxy_server: proxies[kind] } : {}) });
      setDrafts((current) => ({ ...current, [kind]: result }));
      setDirty((current) => ({ ...current, [kind]: false }));
      setProxies((current) => ({ ...current, [kind]: undefined }));
      onNotice(`${TITLES[kind]} 设置已保存`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "设置保存失败，请重试");
    } finally { setSaving(""); }
  };
  const restart = async () => {
    setSaving("browser"); setError("");
    try {
      await restartBrowser();
      onNotice("浏览器辅助已重置，下次 Q4 任务会按新设置启动");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "浏览器辅助重启失败");
    } finally { setSaving(""); }
  };

  return <>
    <div className="page-heading"><div><span className="eyebrow">SYSTEM / SETTINGS</span><h1>服务设置</h1><p>按生成渠道独立保存运行参数，账号池与模型选择继续在各自页面管理。</p></div></div>
    <div className="subnav settings-tabs" role="tablist" aria-label="服务设置分类">
      {(["common", "wan", "video", "image"] as const).map((kind) => <button key={kind} id={`settings-tab-${kind}`}
        role="tab" type="button" aria-selected={tab === kind} aria-controls={`settings-panel-${kind}`}
        className={tab === kind ? "active" : ""} onClick={() => { setTab(kind); setError(""); }}>
        {kind === "common" ? "通用" : TITLES[kind]}
      </button>)}
    </div>
    {tab === "common" ? <div id="settings-panel-common" role="tabpanel" aria-labelledby="settings-tab-common">{common}</div> :
      drafts[tab] ? <form id={`settings-panel-${tab}`} role="tabpanel" aria-labelledby={`settings-tab-${tab}`}
        className="provider-settings-form" onSubmit={(event) => { event.preventDefault(); void saveCurrent(tab); }}>
        <div className="provider-settings-intro"><Settings2 size={19} /><div><strong>{TITLES[tab]} 独立配置</strong>
          <p>{tab === "wan" ? "Wan 视频、积分领取和登录使用这套配置。压缩仅处理本机上传图片，已发布的 HTTPS 素材保持原样。" :
            tab === "video" ? "Q4 与 Claw 共用视频运行参数；浏览器辅助只用于 Q4，Claw 继续通过 API 提交。" :
              "图片使用独立超时、代理与积分阈值，修改此处不会影响视频。模型、比例和生成数量由造物请求传入。"}</p></div>
          <span className={`badge ${dirty[tab] ? "amber" : "neutral"}`}>{dirty[tab] ? "有未保存修改" : "已保存配置"}</span>
        </div>
        <section className="settings-card"><div className="settings-card-heading"><h2>账号与额度</h2><p>设置核查、账号筛选与批量操作。</p></div>
          <div className="settings-fields">
            {renderNumber(tab, ["check_concurrency", "核查并发数", 1, 50])}
            {renderNumber(tab, ["min_balance", "最低可用积分", 0, 1000000, "已核查余额不高于此值的账号跳过；各渠道独立生效。"])}
            {tab === "wan" && <>
              {renderNumber(tab, ["login_concurrency", "批量登录并发数", 1, 20])}
              {renderNumber(tab, ["claim_concurrency", "积分领取并发数", 1, 20])}
            </>}
            {tab === "video" && renderNumber(tab, ["claw_message_workers", "Claw 消息发送并发数", 0, 500, "0 表示本批账号全部并发；仅用于固定消息操作。"])}
          </div>
        </section>
        <section className="settings-card"><div className="settings-card-heading"><h2>任务与超时</h2><p>提交不会因为网络异常自动重发，避免重复生成。</p></div>
          <div className="settings-fields">
            {renderNumber(tab, ["task_timeout", "总任务超时 / 秒", 60, tab === "wan" ? 7200 : 14400])}
            {tab === "video" && renderNumber(tab, ["generation_timeout", "生成等待超时 / 秒", 60, 14400])}
            {renderNumber(tab, ["poll_interval", "轮询间隔 / 秒", tab === "image" ? 3 : 2, 60])}
            {NETWORK_FIELDS.map((field) => renderNumber(tab, field))}
            {tab === "wan" && renderNumber(tab, ["retry_count", "安全请求重试次数", 0, 10, "只重试读取和上传预签名请求，生成和领取操作不重试。"])}
          </div>
          {tab === "wan" && <label className="settings-toggle"><input type="checkbox" checked={Boolean(drafts.wan?.compress_images)}
            onChange={(event) => change("wan", "compress_images", event.target.checked)} /><span><strong>上传前压缩图片</strong><small>最长边限制为 2048 像素，保留透明通道，不修改原文件。</small></span></label>}
        </section>
        {tab === "video" && <section className="settings-card"><div className="settings-card-heading"><h2>Q4 浏览器辅助</h2>
          <span className={`badge ${snapshot?.browser_helper.browser_available ? "green" : "amber"}`}>{snapshot?.browser_helper.browser_available ? `可用 · ${snapshot.browser_helper.windows} 个窗口` : "未检测到 Chrome / Edge"}</span></div>
          <label className="settings-toggle"><input type="checkbox" checked={Boolean(drafts.video?.browser_assist_enabled)}
            onChange={(event) => change("video", "browser_assist_enabled", event.target.checked)} /><span><strong>启用浏览器辅助</strong><small>Q4 任务通过独立 Chrome / Edge 页面提交和查询，账号 Cookie 不进入命令行。</small></span></label>
          <div className="settings-fields">{renderNumber("video", ["browser_window_count", "最多保留窗口数", 1, 50, "按账号隔离窗口，达到上限后替换空闲窗口。"])}</div>
          <div className="settings-inline-actions"><button className="button outline" type="button" disabled={busy || !!saving} onClick={() => void restart()}><RefreshCw size={15} /> 重启浏览器辅助</button><span className="field-hint">Q4 任务运行期间禁止重启。</span></div>
        </section>}
        <section className="settings-card"><div className="settings-card-heading"><h2>代理与订阅</h2><p>支持 HTTP / HTTPS / SOCKS5 代理及包含这些节点的 HTTPS 订阅。账号凭据与订阅地址分别加密保存。</p></div>
          <div className="field"><label htmlFor={`settings-${tab}-proxy`}>代理地址 / 订阅链接</label>
            <input id={`settings-${tab}-proxy`} type="password" autoComplete="off" value={proxies[tab] ?? ""}
              placeholder={drafts[tab]?.proxy_configured ? "已保存代理；留空保持，输入新地址可替换" : "未配置，使用直连"}
              onChange={(event) => { setProxies((current) => ({ ...current, [tab]: event.target.value })); setDirty((current) => ({ ...current, [tab]: true })); }} />
            <small className="field-hint">订阅最多读取 1 MB；SS / VMess / VLESS 节点暂不支持。带认证的 SOCKS5 代理可用于 API，浏览器辅助需使用 HTTP 代理。</small>
          </div>
          <div className="settings-fields settings-proxy-options">
            {renderNumber(tab, ["proxy_subscription_timeout", "订阅请求超时 / 秒", 15, 600])}
            {renderNumber(tab, ["max_proxy_nodes", "最多使用节点数", 1, 50])}
          </div>
          <button className="button outline" type="button" disabled={busy || !!saving} onClick={() => {
            setProxies((current) => ({ ...current, [tab]: "" })); setDirty((current) => ({ ...current, [tab]: true }));
          }}>清除代理并改为直连</button>
        </section>
        {error && <p className="settings-error" role="alert">{error}</p>}
        <div className="settings-save-bar"><span>{dirty[tab] ? "保存后用于后续任务；正在执行的任务沿用当前参数。" : "这套配置只影响对应生成渠道。"}</span>
          <button className="button primary" disabled={busy || !!saving}><Check size={16} />{saving === tab ? "保存中…" : `保存 ${TITLES[tab]} 设置`}</button></div>
      </form> : <p role="status">正在加载设置…</p>}
  </>;
}
