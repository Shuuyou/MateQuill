"use client";
import { useEffect, useRef, useState } from "react";
import { workspaceRequest } from "@/lib/workspace";

export default function TrialSaveDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); }, []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await workspaceRequest("/api/auth/claim-trial", {
        store_name: String(data.get("store_name")), email: String(data.get("email")), password: String(data.get("password")),
      });
      await onSaved();
      window.dispatchEvent(new Event("matequill-stores-updated"));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "这次没有保存成功，请再试一次。");
    } finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="mq-trial-dialog" aria-labelledby="trial-save-title" onCancel={(event) => { if (busy) event.preventDefault(); else onClose(); }}>
    <form className="mq-auth-form" onSubmit={(event) => void submit(event)}>
      <h2 id="trial-save-title">把今天的作品，留在店里。</h2>
      <p>照片、文稿和创作记录会一起保存。</p>
      <label>门店名称<input name="store_name" required maxLength={100} autoComplete="organization" /></label>
      <label>邮箱<input name="email" type="email" required autoComplete="username" /></label>
      <label>设置密码<input name="password" type="password" required minLength={12} maxLength={128} autoComplete="new-password" aria-describedby="trial-password-help" /></label>
      <p id="trial-password-help">密码至少 12 个字符，可使用一句容易记住的话。</p>
      {error && <p className="mq-form-error" role="alert">{error}</p>}
      <button type="submit" className="mq-primary" disabled={busy}>{busy ? "正在保存…" : "保存并继续创作"}<span>↗</span></button>
      <button type="button" className="mq-text-button" disabled={busy} onClick={onClose}>继续体验，稍后再说</button>
    </form>
  </dialog>;
}
