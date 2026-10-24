import { sessionScopeKey, workspaceRequest } from "./workspace";
import type { PreparedAssignment, StoreAssignment } from "./channel";
import type { RunResult, RunTask } from "./types";
import type { AgentArtifact, MarketingOpportunity } from "./agent";

export interface AssignmentDraft {
  status: "idle" | "preparing" | "queued" | "running" | "ready" | "failed" | "cancelled" | "needs_revision";
  job_id?: string | null;
  phase?: string;
  run_id?: string;
  result?: RunResult;
  task?: RunTask;
  opportunity?: PreparedAssignment["opportunity"];
  message?: string;
  error?: { message?: string };
}
export interface DraftState { value?: AssignmentDraft; loading: boolean; error?: string }
const states = new Map<string, DraftState>();
const requests = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();
export const draftKey = (scope: string, assignment: StoreAssignment) =>
  `${scope}:${assignment.id}:${assignment.campaign_version}`;
export const getDraftState = (key: string): DraftState | undefined => states.get(key);
export function subscribeDrafts(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function save(key: string, state: DraftState) {
  states.set(key, state);
  listeners.forEach((listener) => listener());
}

/** Generation belongs to the server; observing it survives view navigation. */
export function prepareAssignmentDraft(scope: string, assignment: StoreAssignment, refresh = false, resume = false) {
  const key = draftKey(scope, assignment);
  const pending = requests.get(key);
  if (pending) return pending;
  const previous = states.get(key);
  // Terminal errors require an explicit action. Re-rendering never spends again.
  if (!refresh && !resume && previous) {
    if (!previous.error && ["preparing", "queued", "running"].includes(previous.value?.status || "")) resume = true;
    else return Promise.resolve();
  }
  const path = `/api/channel/assignments/${assignment.id}/draft`;
  const isCurrent = () => sessionScopeKey() === scope;
  const operation = async () => {
    save(key, { value: refresh ? undefined : previous?.value, loading: true });
    try {
      if (!isCurrent()) return;
      let value = resume
        ? await workspaceRequest<AssignmentDraft>(path)
        : await workspaceRequest<AssignmentDraft>(path, { refresh });
      const started = Date.now();
      while (isCurrent()) {
        save(key, { value, loading: ["preparing", "queued", "running"].includes(value.status) });
        // A lost POST can precede server acceptance. An explicit recovery read
        // must stop here, rather than polling an unstarted task indefinitely.
        if (value.status === "idle") return;
        if (["ready", "needs_revision"].includes(value.status)) {
          if (!value.result) throw new Error("正文还没有取回，点一下继续查看。");
          return;
        }
        if (["failed", "cancelled"].includes(value.status)) {
          save(key, { value, loading: false, error: value.error?.message || value.message || "这篇暂时没有写完，可以重新准备。" });
          return;
        }
        if (Date.now() - started > 20 * 60 * 1000) throw new Error("准备还在继续，稍后点一下查看进展。");
        await new Promise((resolve) => setTimeout(resolve, 2500));
        if (!isCurrent()) return;
        value = await workspaceRequest<AssignmentDraft>(path);
      }
    } catch (reason) {
      save(key, { value: states.get(key)?.value, loading: false,
        error: reason instanceof Error ? reason.message : "暂时没有取回正文，可以继续查看。" });
    } finally {
      if (!isCurrent()) save(key, { ...states.get(key), loading: false });
      requests.delete(key);
    }
  };
  const request = operation();
  requests.set(key, request);
  return request;
}

export interface DailyStoreProposal {
  id?: string;
  day: string;
  status: "idle" | "preparing" | "ready" | "proposal" | "needs_input" | "failed" | "stale" | "dismissed";
  conversation_id?: string;
  turn_id?: string;
  title?: string;
  opportunity?: MarketingOpportunity | null;
  artifact?: AgentArtifact | null;
  in_progress?: boolean;
  adopted?: boolean;
  message?: string;
}
export interface DailyDraftState { value?: DailyStoreProposal; loading: boolean; error?: string }
const dailyStates = new Map<string, DailyDraftState>();
const dailyRequests = new Map<string, Promise<void>>();
export const getDailyDraftState = (scope: string) => dailyStates.get(scope);
function saveDaily(scope: string, state: DailyDraftState) {
  dailyStates.set(scope, state);
  listeners.forEach((listener) => listener());
}

/** A POST creates one durable agent turn; all subsequent recovery is read-only. */
export function prepareDailyStoreDraft(scope: string, refresh = false, resume = false) {
  const pending = dailyRequests.get(scope);
  if (pending) return pending;
  const previous = dailyStates.get(scope);
  const isCurrent = () => sessionScopeKey() === scope;
  const operation = async () => {
    saveDaily(scope, { value: previous?.value, loading: true });
    try {
      if (!isCurrent()) return;
      let value = await workspaceRequest<DailyStoreProposal>("/api/store/daily");
      if (!resume && (refresh || value.status === "idle" || (value.status === "stale" && !value.in_progress))) {
        value = await workspaceRequest<DailyStoreProposal>("/api/store/daily/prepare", { refresh });
      }
      const started = Date.now();
      while (isCurrent()) {
        const active = value.status === "preparing" || value.in_progress === true;
        saveDaily(scope, { value, loading: active });
        if (!active) return;
        if (Date.now() - started > 20 * 60 * 1000) throw new Error("今天的准备还在继续，稍后可以接着看。");
        await new Promise((resolve) => setTimeout(resolve, 2500));
        if (!isCurrent()) return;
        value = await workspaceRequest<DailyStoreProposal>("/api/store/daily");
      }
    } catch (reason) {
      saveDaily(scope, { value: dailyStates.get(scope)?.value, loading: false,
        error: reason instanceof Error ? reason.message : "今天的准备暂时没接回来，可以继续查看。" });
    } finally {
      if (!isCurrent()) saveDaily(scope, { ...dailyStates.get(scope), loading: false });
      dailyRequests.delete(scope);
    }
  };
  const request = operation();
  dailyRequests.set(scope, request);
  return request;
}

export async function actOnDailyStoreDraft(scope: string, action: "adopt" | "dismiss") {
  const current = dailyStates.get(scope)?.value;
  if (!current?.id || sessionScopeKey() !== scope) throw new Error("先打开这家门店的今日方向。");
  try {
    const value = await workspaceRequest<DailyStoreProposal>(`/api/store/daily/${current.id}/${action}`, {});
    if (sessionScopeKey() === scope) saveDaily(scope, { value, loading: false });
    return value;
  } catch (reason) {
    // A tab left open past expiry must switch to today's state, not remain a
    // permanently clickable stale card. This recovery never creates a task.
    try {
      const value = await workspaceRequest<DailyStoreProposal>("/api/store/daily");
      if (sessionScopeKey() === scope) saveDaily(scope, { value, loading: false });
    } catch { /* Keep the original, actionable session/network error. */ }
    throw reason;
  }
}
