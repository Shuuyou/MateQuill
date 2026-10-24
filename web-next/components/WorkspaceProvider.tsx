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
import TrialSaveDialog from "./TrialSaveDialog";

interface WorkspaceContextValue {
  session: Session;
  scopeKey: string;
  refresh: () => Promise<void>;
  switchWorkspace: (tenant: string, store?: string | null) => Promise<void>;
  logout: () => Promise<void>;
  saveTrial: () => void;
  showLogin: () => void;
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
  const [loginVisible, setLoginVisible] = useState(false);
  const [saveVisible, setSaveVisible] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState<{ id: string; label: string; description: string }[]>([]);
  const [handoff, setHandoff] = useState<{
    user_name: string;
    workspace_name: string;
    conversation_id: string;
    expires_at: number;
    requires_workspace_switch?: boolean;
  } | null>(null);
  const [handoffError, setHandoffError] = useState("");
  const [handoffBusy, setHandoffBusy] = useState(false);
  const handoffCode = useRef("");
  const initialLoad = useRef<Promise<void> | null>(null);
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
    async function initialize() {
      void workspaceRequest<{ accounts: { id: string; label: string; description: string }[] }>("/api/auth/demo")
        .then((value) => setDemoAccounts(value.accounts)).catch(() => {});
      const fragment = new URLSearchParams(window.location.hash.slice(1));
      const code = fragment.get("handoff");
      if (code) {
        handoffCode.current = code;
        window.history.replaceState(
          {},
          "",
          window.location.pathname + window.location.search,
        );
        try {
          const value = await workspaceRequest<{
            requires_confirmation: boolean;
            user_name: string;
            workspace_name: string;
            conversation_id: string;
            expires_at: number;
            requires_workspace_switch?: boolean;
          }>("/api/messaging/handoff/exchange", { code, confirm: false });
          setHandoff(value);
        } catch (reason) {
          setHandoffError(
            reason instanceof Error
              ? reason.message
              : "这次作品链接没有打开，请回到聊天里重新打开。",
          );
        }
      }
      await refresh();
    }
    if (!initialLoad.current) initialLoad.current = initialize();
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
  async function confirmHandoff() {
    setHandoffBusy(true);
    setHandoffError("");
    try {
      const value = await workspaceRequest<
        Session & { conversation_id: string }
      >("/api/messaging/handoff/exchange", {
        code: handoffCode.current,
        confirm: true,
        switch_workspace: handoff?.requires_workspace_switch === true,
      });
      const key = `${value.user?.id}:${value.workspace?.tenant_id}:${value.workspace?.store_id || ""}`;
      rememberWorkspace(value);
      previousWorkspace.current = key;
      setSession(value);
      setConnectionError("");
      setWorkspaceNotice(false);
      const scope = `${value.user?.id}:${value.workspace?.tenant_id}:${value.workspace?.store_id || "enterprise"}`;
      sessionStorage.setItem(
        `matequill-agent:${scope}:active`,
        value.conversation_id,
      );
      const destination = new URL(window.location.href);
      destination.searchParams.set("conversation", value.conversation_id);
      destination.hash = "";
      window.history.replaceState(
        {},
        "",
        destination.pathname + destination.search,
      );
      handoffCode.current = "";
      setHandoff(null);
      setInvite("");
      setLoginVisible(false);
    } catch (reason) {
      setHandoffError(
        reason instanceof Error
          ? reason.message
          : "这次没有接上作品，请回到聊天里重新打开。",
      );
    } finally {
      setHandoffBusy(false);
    }
  }
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
      setLoginVisible(false);
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
    setLoginVisible(false);
  }
  async function startTrial(persona?: string) {
    setBusy(true);
    setError("");
    try {
      const value = await workspaceRequest<Session>(persona ? "/api/auth/demo" : "/api/auth/guest", persona ? { persona } : {});
      rememberWorkspace(value);
      setSession(value);
      setConnectionError("");
      setLoginVisible(false);
      previousWorkspace.current = `${value.user?.id}:${value.workspace?.tenant_id}:${value.workspace?.store_id || ""}`;
      window.history.replaceState({}, "", "/");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "这次未能打开，请再试一次。",
      );
    } finally {
      setBusy(false);
    }
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
      <main className="mq-auth-shell mq-workspace-loading" role="status">
        <Character state="thinking" />
        <p>正在打开你的工作空间。</p>
      </main>
    );
  if (handoff || handoffError)
    return (
      <main className="mq-auth-shell mq-enter">
        <div className="mq-handoff-confirm">
          <Character state={handoffBusy ? "thinking" : "idle"} />
          <h1>在网页里，接着做好这件事。</h1>
          {handoff && (
            <p>
              {handoff.user_name} · {handoff.workspace_name}
            </p>
          )}
          {handoff?.requires_workspace_switch && (
            <p>继续后，这个网页会打开你在企微中正在创作的会话。</p>
          )}
          {handoffError && (
            <p className="mq-form-error" role="alert">
              {handoffError}
            </p>
          )}
          {handoff && !handoffError && (
            <button
              className="mq-primary"
              disabled={handoffBusy}
              onClick={() => void confirmHandoff()}
            >
              {handoffBusy
                ? "正在打开…"
                : handoff.requires_workspace_switch
                  ? "切换到企微会话并继续"
                  : "继续这次创作"}
              <span>↗</span>
            </button>
          )}
          <button
            className="mq-text-button"
            disabled={handoffBusy}
            onClick={() => {
              handoffCode.current = "";
              setHandoff(null);
              setHandoffError("");
            }}
          >
            {session.authenticated ? "回到我的工作台" : "先看看网页工作台"}
          </button>
        </div>
      </main>
    );
  if (!session.authenticated || !workspace || invite || loginVisible)
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
          {!invite && !loginVisible ? (
            <section className="mq-trial-welcome">
              <h2>先写一篇，看看效果。</h2>
              <p>从你眼前的顾客和一张照片开始。</p>
              <button
                className="mq-primary"
                disabled={busy}
                onClick={() => void startTrial()}
              >
                {busy ? "正在准备…" : "直接试用"}
                <span>↗</span>
              </button>
              <button
                className="mq-text-button"
                disabled={busy}
                onClick={() => setLoginVisible(true)}
              >
                已有账号，登录继续
              </button>
              {(error || connectionError) && (
                <p className="mq-form-error" role="alert">
                  {error || connectionError}
                </p>
              )}
              {demoAccounts.length > 0 && <div className="mq-demo-accounts">
                <h3>选择一家店，开始演示</h3>
                {demoAccounts.map((account) => <button key={account.id} type="button" disabled={busy} onClick={() => void startTrial(account.id)}>
                  <span><strong>{account.label}</strong><small>{account.description}</small></span><span aria-hidden="true">↗</span>
                </button>)}
              </div>}
            </section>
          ) : (
            <form
              className="mq-auth-form"
              onSubmit={(event) => void authenticate(event)}
            >
              <h2>{invite ? "加入你的团队" : "回到工作空间"}</h2>
              <p>
                {invite
                  ? "留下你的名字，和门店一起创作。"
                  : "接着写上次的故事。"}
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
                {busy
                  ? "正在进入…"
                  : invite
                    ? "加入并开始创作"
                    : "进入工作空间"}
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
                    setLoginVisible(true);
                    window.history.replaceState({}, "", "/");
                  }}
                >
                  已有账号，直接登录
                </button>
              )}
              {!invite && (
                <button
                  className="mq-text-button"
                  type="button"
                  disabled={busy}
                  onClick={() => setLoginVisible(false)}
                >
                  {session.is_guest ? "继续刚才的体验" : "先试用，再决定"}
                </button>
              )}
            </form>
          )}
        </div>
      </main>
    );
  return (
    <Context.Provider
      value={{
        session,
        scopeKey,
        refresh,
        switchWorkspace,
        logout,
        saveTrial: () => setSaveVisible(true),
        showLogin: () => setLoginVisible(true),
      }}
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
      {saveVisible && session.is_guest && (
        <TrialSaveDialog
          onClose={() => setSaveVisible(false)}
          onSaved={refresh}
        />
      )}
    </Context.Provider>
  );
}
