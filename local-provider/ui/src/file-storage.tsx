import { useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";

export function FileStorage({ onNotice }: { onNotice(message: string): void }) {
  const [value, setValue] = useState<{ root: string; activeResults: string; pending: boolean } | null>(null);
  useEffect(() => {
    if (!window.zaowuProvider?.getFileStorage) return;
    let cancelled = false;
    const reload = () => window.zaowuProvider!.getFileStorage().then((result) => {
      if (!cancelled) setValue(result);
    }).catch((cause) => { if (!cancelled) onNotice(cause instanceof Error ? cause.message : "读取文件目录失败"); });
    void reload();
    const timer = window.setInterval(() => void reload(), 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [onNotice]);
  if (!value) return null;
  return <section className="block">
    <div className="section-heading"><div><h2>文件存储</h2>
      <p>保存位置统一在造物「设置 → 文件存储」中管理。</p></div>
      <span className={`badge ${value.pending ? "amber" : "green"}`}>{value.pending ? "等待重启应用" : "已同步"}</span>
    </div>
    <div className="field"><label htmlFor="shared-file-root">统一保存位置</label>
      <input id="shared-file-root" readOnly value={value.root} /></div>
    <div className="field"><label htmlFor="provider-result-root">当前生成结果目录</label>
      <input id="provider-result-root" readOnly value={value.activeResults} /></div>
    <div className="settings-inline-actions">
      <button type="button" className="button outline" onClick={() => {
        void window.zaowuProvider?.openResultDirectory().catch((cause) => onNotice(cause instanceof Error ? cause.message : "打开目录失败"));
      }}><FolderOpen size={15} /> 打开结果目录</button>
      <span className="field-hint">账号与卡密继续保存在应用数据目录。</span>
    </div>
  </section>;
}
