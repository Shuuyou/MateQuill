"use client";
import { useEffect, useState } from "react";
import { stores } from "@/lib/api";
import { ROLE_NAMES } from "@/lib/workspace";
import type { StoreProfile } from "@/lib/types";
import { useWorkspace } from "./WorkspaceProvider";

export default function WorkspaceMenu({
  disabled = false,
}: {
  disabled?: boolean;
}) {
  const { session, switchWorkspace, logout } = useWorkspace();
  const workspace = session.workspace!;
  const [open, setOpen] = useState(false);
  const [profiles, setProfiles] = useState<StoreProfile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const load = () => {
      stores()
        .then((value) => setProfiles(value.stores))
        .catch(() => {});
    };
    load();
    window.addEventListener("matequill-stores-updated", load);
    return () => window.removeEventListener("matequill-stores-updated", load);
  }, []);
  const enterprises = [
    ...new Map(
      session.memberships.map((item) => [item.tenant_id, item.tenant_name]),
    ).entries(),
  ];
  const currentStore = profiles.find((item) => item.id === workspace.store_id);
  async function select(tenant: string, store: string | null) {
    setBusy(true);
    setError("");
    try {
      await switchWorkspace(tenant, store);
      setOpen(false);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "暂时无法切换工作空间。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mq-workspace-menu">
      <button
        type="button"
        className="mq-workspace-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        disabled={disabled || busy}
      >
        <span className="mq-workspace-avatar">
          {session.user?.name.slice(0, 1) || "M"}
        </span>
        <span>
          <strong>{currentStore?.name || workspace.tenant_name}</strong>
          <span>
            {session.user?.name} ·{" "}
            {ROLE_NAMES[workspace.roles[0]] || "团队成员"}
          </span>
        </span>
        <span aria-hidden="true">⌄</span>
      </button>
      {open && (
        <>
          <button
            className="mq-menu-scrim"
            aria-label="关闭工作空间菜单"
            onClick={() => setOpen(false)}
          />
          <section className="mq-workspace-popover" aria-label="工作空间与账号">
            <h2>{workspace.tenant_name}</h2>
            {enterprises.length > 1 && (
              <label>
                当前企业
                <select
                  value={workspace.tenant_id}
                  disabled={busy}
                  onChange={(event) => void select(event.target.value, null)}
                >
                  {enterprises.map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {(profiles.length > 1 || workspace.all_stores) && (
              <label>
                工作范围
                <select
                  value={workspace.store_id || ""}
                  disabled={busy}
                  onChange={(event) =>
                    void select(workspace.tenant_id, event.target.value || null)
                  }
                >
                  {workspace.all_stores && (
                    <option value="">企业全部门店</option>
                  )}
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {error && (
              <p role="alert" className="mq-form-error">
                {error}
              </p>
            )}
            <button
              className="mq-text-button"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void logout().catch((reason: Error) => {
                  setError(reason.message);
                  setBusy(false);
                });
              }}
            >
              退出登录
            </button>
          </section>
        </>
      )}
    </div>
  );
}
