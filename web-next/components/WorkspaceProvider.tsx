"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { Session } from "@/lib/workspace";
import { rememberWorkspace, workspaceRequest } from "@/lib/workspace";
import Character from "./Character";

interface WorkspaceContextValue {
  session: Session;
  scopeKey: string;
  refresh: () => Promise<void>;
  switchWorkspace: (tenant: string, store?: string | null) => Promise<void>;
  logout: () => Promise<void>;
}
const Context = createContext<WorkspaceContextValue | null>(null);
export function useWorkspace() {
  const context = useContext(Context);
  if (!context) throw new Error("WorkspaceProvider is required");
  return context;
}
const anonymous: Session = {
  authenticated: false,
  user: null,
  workspace: null,
  memberships: [],
};

export default function WorkspaceProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [session, setSession] = useState<Session>(anonymous);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState("");
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [workspaceNotice, setWorkspaceNotice] = useState(false);
  const previousWorkspace = useRef("");
  const refresh = useCallback(async () => {
    try {
      const value = await workspaceRequest<Session>("/api/auth/me");
      const key = value.workspace
        ? `${value.user?.id}:${value.workspace.tenant_id}:${value.workspace.store_id || ""}`
        : "";
      if (previousWorkspace.current && key && previousWorkspace.current !== key)
        setWorkspaceNotice(true);
      previousWorkspace.current = key;
      rememberWorkspace(value);
      setSession(value);
      setConnectionError("");
    } catch (reason) {
      setConnectionError(
        reason instanceof Error ? reason.message : "暂时没有连接到工作空间。",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    setInvite(
      new URLSearchParams(window.location.search).get("invite") ||
        new URLSearchParams(window.location.search).get("token") ||
        "",
    );
    void refresh();
    const expired = () => {
      rememberWorkspace(anonymous);
      setSession(anonymous);
    };
    const changed = () => {
      void refresh();
    };
    window.addEventListener("matequill-session-expired", expired);
    window.addEventListener("matequill-workspace-changed", changed);
    window.addEventListener("focus", changed);
    return () => {
      window.removeEventListener("matequill-session-expired", expired);
      window.removeEventListener("matequill-workspace-changed", changed);
      window.removeEventListener("focus", changed);
    };
  }, [refresh]);
  const workspace = session.workspace;
  const scopeKey = [
    session.user?.id,
    workspace?.tenant_id,
    workspace?.store_id || "enterprise",
  ].join(":");
  async function authenticate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const value = await workspaceRequest<Session>(
        invite ? "/api/auth/accept-invitation" : "/api/auth/login",
        invite
          ? {
              token: invite,
              name: String(data.get("name")),
              password: String(data.get("password")),
            }
          : {
              email: String(data.get("email")),
              password: String(data.get("password")),
            },
      );
      rememberWorkspace(value);
      previousWorkspace.current = `${value.user?.id}:${value.workspace?.tenant_id}:${value.workspace?.store_id || ""}`;
      setSession(value);
      setInvite("");
      setConnectionError("");
      setWorkspaceNotice(false);
      window.history.replaceState({}, "", "/");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "暂时未能登录，请再试一次。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    await workspaceRequest("/api/auth/logout", {});
    // Personal drafts must not remain on a shared shop device after sign-out.
    Object.keys(sessionStorage)
      .filter((key) => key.startsWith("matequill-"))
      .forEach((key) => sessionStorage.removeItem(key));
    rememberWorkspace(anonymous);
    previousWorkspace.current = "";
    setSession(anonymous);
  }
  async function switchWorkspace(tenant: string, store?: string | null) {
    const value = await workspaceRequest<Session>("/api/auth/workspace", {
      tenant_id: tenant,
      store_id: store || null,
    });
    rememberWorkspace(value);
    previousWorkspace.current = `${value.user?.id}:${value.workspace?.tenant_id}:${value.workspace?.store_id || ""}`;
    setSession(value);
    setWorkspaceNotice(false);
  }
  if (loading)
    return (
      <main className="mq-auth-shell">
        <Character state="thinking" />
        <p>正在打开你的工作空间。</p>
      </main>
    );
  if (!session.authenticated || !workspace || invite)
    return (
      <main className="mq-auth-shell mq-enter">
        <div className="mq-auth-brand">
          <img src="/youju.svg" alt="" />
          <span>
            MateQuill <em>场景有据</em>
          </span>
        </div>
        <div className="mq-auth-layout">
          <section className="mq-auth-story">
            <Character />
            <h1>
              门店里的好故事，
              <br />
              从这里开始。
            </h1>
            <p>
              拍一张，说一句。
              <br />
              把真实的日常，讲给对的人。
            </p>
          </section>
          <form
            className="mq-auth-form"
            onSubmit={(event) => void authenticate(event)}
          >
            <h2>{invite ? "加入你的团队" : "回到工作空间"}</h2>
            <p>
              {invite
                ? "留下你的名字，和门店一起创作。"
                : "使用团队邀请时填写的邮箱登录。"}
            </p>
            {invite ? (
              <label>
                你的名字
                <input
                  name="name"
                  required
                  maxLength={100}
                  autoComplete="name"
                />
              </label>
            ) : (
              <label>
                工作邮箱
                <input
                  name="email"
                  type="email"
                  required
                  autoComplete="username"
                />
              </label>
            )}
            <label>
              {invite ? "设置密码" : "密码"}
              <input
                name="password"
                type="password"
                required
                minLength={invite ? 12 : 1}
                autoComplete={invite ? "new-password" : "current-password"}
              />
            </label>
            {(error || connectionError) && (
              <p className="mq-form-error" role="alert">
                {error || connectionError}
              </p>
            )}
            <button type="submit" className="mq-primary" disabled={busy}>
              {busy ? "正在进入…" : invite ? "加入并开始创作" : "进入工作空间"}
              <span>↗</span>
            </button>
            {connectionError && (
              <button
                type="button"
                className="mq-text-button"
                onClick={() => void refresh()}
              >
                重新连接
              </button>
            )}
            {invite && (
              <button
                type="button"
                className="mq-text-button"
                onClick={() => {
                  setInvite("");
                  window.history.replaceState({}, "", "/");
                }}
              >
                已有账号，直接登录
              </button>
            )}
          </form>
        </div>
      </main>
    );
  return (
    <Context.Provider
      value={{ session, scopeKey, refresh, switchWorkspace, logout }}
    >
      {workspaceNotice && (
        <aside className="mq-context-notice" role="status">
          <p>工作范围已与其他窗口同步，请在当前门店继续。</p>
          <button
            className="mq-text-button"
            onClick={() => setWorkspaceNotice(false)}
          >
            知道了
          </button>
        </aside>
      )}
      <div key={scopeKey}>{children}</div>
    </Context.Provider>
  );
}
