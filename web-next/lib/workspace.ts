export type WorkspaceRole =
  | "admin"
  | "operator"
  | "regional_manager"
  | "store_manager"
  | "salesperson"
  | "reviewer";
export interface Membership {
  id: string;
  tenant_id: string;
  tenant_name: string;
  store_id: string | null;
  role: WorkspaceRole;
}
export interface Session {
  authenticated: boolean;
  is_guest?: boolean;
  trial_expires_at?: number | null;
  user: { id: string; email: string; name: string } | null;
  workspace: {
    tenant_id: string;
    tenant_name: string;
    store_id: string | null;
    roles: WorkspaceRole[];
    store_ids: string[];
    all_stores: boolean;
  } | null;
  memberships: Membership[];
}
export const ROLE_NAMES: Record<WorkspaceRole, string> = {
  admin: "企业管理员",
  operator: "总部运营",
  regional_manager: "区域负责人",
  store_manager: "店长",
  salesperson: "销售顾问",
  reviewer: "内容审核员",
};
let activeWorkspace = "";
let activeSessionScope = "";
export function sessionScopeKey() {
  return activeSessionScope;
}
export function rememberWorkspace(session: Session) {
  activeSessionScope = session.workspace
    ? `${session.user?.id}:${session.workspace.tenant_id}:${session.workspace.store_id || "enterprise"}`
    : "";
  activeWorkspace = session.workspace
    ? `${session.workspace.tenant_id}:${session.workspace.store_id || ""}`
    : "";
}
/** A consistency precondition only: the server derives authorization from the session. */
export async function workspaceFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (activeWorkspace && !path.startsWith("/api/auth/"))
    headers.set("X-MateQuill-Workspace", activeWorkspace);
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 409) {
    const value = await response
      .clone()
      .json()
      .catch(() => ({}));
    if (value.code === "workspace_changed")
      window.dispatchEvent(new Event("matequill-workspace-changed"));
  }
  return response;
}
export async function workspaceRequest<T>(
  path: string,
  body?: unknown,
  method = body === undefined ? "GET" : "POST",
): Promise<T> {
  const response = await workspaceFetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    ...(body !== undefined
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("matequill-session-expired"));
    throw Object.assign(new Error(
      typeof value.detail === "string"
        ? value.detail
        : "这次操作没有完成，请重试。",
    ), { status: response.status });
  }
  return value as T;
}
