"use client";

import { useEffect, useState } from "react";
import { draftKey, getDraftState, prepareAssignmentDraft, subscribeDrafts,
  prepareDailyStoreDraft, getDailyDraftState, actOnDailyStoreDraft } from "@/lib/assignmentDraft";
import type { StoreAssignment, StoreOpportunity } from "@/lib/channel";
import type { RunResult, RunTask } from "@/lib/types";
import { workspaceRequest } from "@/lib/workspace";
import { useWorkspace } from "./WorkspaceProvider";

export default function StoreDraftSuggestion({ visible, onOpen, onOpenConversation }: {
  visible: boolean;
  onOpen: (result: RunResult, task: RunTask, assignment: StoreAssignment) => void | Promise<void>;
  onOpenConversation?: (id: string) => void | Promise<void>;
}) {
  const { session, scopeKey } = useWorkspace();
  const [assignment, setAssignment] = useState<StoreAssignment | null>(null);
  const [shared, setShared] = useState<StoreOpportunity | null>(null);
  const [, redraw] = useState(0);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  const [dailyEnabled, setDailyEnabled] = useState(false);
  useEffect(() => subscribeDrafts(() => redraw((value) => value + 1)), []);
  useEffect(() => {
    let live = true;
    setAssignment(null);
    setShared(null);
    setDailyEnabled(false);
    if (session.is_guest || !session.workspace?.store_id) return;
    workspaceRequest<{ assignments: StoreAssignment[] }>("/api/channel/assignments").then(async (value) => {
      if (!live) return;
      const next = value.assignments.find((item) => item.available !== false &&
        ["assigned", "pending", "accepted", "creating", "in_progress", "changes_requested"].includes(item.status));
      if (!next) {
        setDailyEnabled(true);
        return;
      }
      setAssignment(next);
      const proposal = await workspaceRequest<StoreOpportunity>(`/api/channel/assignments/${next.id}/opportunity`);
      if (!live) return;
      setShared(proposal);
      if (!proposal.opportunity) void prepareAssignmentDraft(scopeKey, next);
      else if (proposal.status === "adopted") void prepareAssignmentDraft(scopeKey, next, false, true);
    }).catch(() => { /* The conversation remains available when no task is reachable. */ });
    return () => { live = false; };
  }, [scopeKey, session.is_guest, session.workspace?.store_id]);

  useEffect(() => {
    if (dailyEnabled && visible) void prepareDailyStoreDraft(scopeKey);
  }, [dailyEnabled, visible, scopeKey]);

  useEffect(() => {
    if (!dailyEnabled || !visible) return;
    const resume = () => { void prepareDailyStoreDraft(scopeKey, false, true); };
    window.addEventListener("focus", resume);
    return () => window.removeEventListener("focus", resume);
  }, [dailyEnabled, visible, scopeKey]);

  if (!visible) return null;
  if (dailyEnabled) {
    const daily = getDailyDraftState(scopeKey);
    const value = daily?.value;
    if (value?.status === "dismissed") return null;
    const canOpen = !!value?.conversation_id && ["ready", "proposal", "needs_input"].includes(value.status);
    const openDaily = async () => {
      if (opening) return;
      setOpening(true); setError("");
      try {
        if (canOpen) {
          const adopted = await actOnDailyStoreDraft(scopeKey, "adopt");
          if (adopted.conversation_id) {
            if (onOpenConversation) await onOpenConversation(adopted.conversation_id);
            else window.location.assign(`/?conversation=${encodeURIComponent(adopted.conversation_id)}`);
          }
        } else await prepareDailyStoreDraft(scopeKey, ["failed", "stale"].includes(value?.status || ""), !!daily?.error);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "暂时没有打开，可以再试一次。"); }
      finally { setOpening(false); }
    };
    return <div className="mq-store-suggestion" aria-live="polite">
      {daily?.loading && !canOpen ? <p className="mq-store-suggestion-working"><span aria-hidden="true" />
        我在为本店准备今天值得讲的故事。
      </p> : canOpen ? <>
        <p>{value?.status === "ready" ? "今天这篇，已经为本店准备好了" : value?.status === "needs_input" ? "今天的方向，只差你决定一件事" : value?.in_progress ? "这个本店方向，正在写成今天的分享" : "今天这件小事，值得让附近的人知道"}</p>
        <button className="mq-store-suggestion-open" onClick={() => void openDaily()} disabled={opening}>
          <strong>{value?.title || "看看本店今天的方向"}</strong>
          <span>{opening ? "正在打开…" : value?.status === "ready" ? "打开这篇 ↗" : "接着看 ↗"}</span>
        </button>
        <button className="mq-text-button" disabled={opening} onClick={() => {
          void actOnDailyStoreDraft(scopeKey, "dismiss").catch((reason) => setError(reason instanceof Error ? reason.message : "稍后再看。"));
        }}>今天先不写这个</button>
      </> : <button className="mq-text-button" disabled={opening} onClick={() => void openDaily()}>
        {daily?.error ? "接着准备今天这篇 ↗" : value?.status === "stale" ? "为今天准备一个新方向 ↗" : "看看今天有什么值得讲 ↗"}
      </button>}
      {error && <p role="alert">{error}</p>}
    </div>;
  }
  if (!assignment) return null;
  const state = getDraftState(draftKey(scopeKey, assignment));
  const result = state?.value?.result;
  async function act() {
    if (!assignment || opening) return;
    setOpening(true); setError("");
    try {
      if (result) await onOpen(result, state?.value?.task || assignment.task, assignment);
      else if (shared?.opportunity && shared.status !== "adopted") {
        await workspaceRequest(`/api/channel/assignments/${assignment.id}/opportunity/adopt`, {});
        setShared({ ...shared, status: "adopted" });
        await prepareAssignmentDraft(scopeKey, assignment, false, true);
      }
      else await prepareAssignmentDraft(scopeKey, assignment,
        ["failed", "cancelled", "idle"].includes(state?.value?.status || ""),
        !!state?.error && !["failed", "cancelled"].includes(state?.value?.status || ""));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "暂时没有打开，可以再试一次。"); }
    finally { setOpening(false); }
  }
  return <div className="mq-store-suggestion" aria-live="polite">
    {state?.loading ? <p className="mq-store-suggestion-working"><span aria-hidden="true" />
      {["running", "queued"].includes(state.value?.status || "") ? "本店今天的图文，我正在写。" : "我在为本店准备一个今天值得讲的故事。"}
    </p> : result ? <>
      <p>{result.status === "ready" ? "也为本店准备好了这一篇" : "本店的这篇初稿，可以接着打磨"}</p>
      <button className="mq-store-suggestion-open" onClick={() => void act()} disabled={opening}>
        <strong>{result.title || state?.value?.opportunity?.title || assignment.title}</strong>
        <span>{opening ? "正在打开…" : "打开这篇 ↗"}</span>
      </button>
    </> : shared?.opportunity ? <>
      <p>为本店发现了这个机会</p>
      <button className="mq-store-suggestion-open" disabled={opening} onClick={() => void act()}>
        <strong>{shared.opportunity.title}</strong><span>{opening ? "正在制作…" : shared.status === "adopted" ? "继续查看 ↗" : "就讲这个 ↗"}</span>
      </button>
    </> : <button className="mq-text-button" disabled={opening} onClick={() => void act()}>
      {state?.error ? "本店的图文暂时没写完，继续准备 ↗" : "也为本店准备一篇 ↗"}
    </button>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
