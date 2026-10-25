"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  attachArtifact,
  createConversation,
  getAgentTurn,
  getConversation,
  listConversations,
  sendAgentTurn,
  type AgentArtifact,
  type AgentConversation,
  type AgentProcess,
  type AgentQuestion,
  type AgentTurnInput,
} from "@/lib/agent";
import { getPoster, getRun } from "@/lib/api";
import type {
  MediaIntake,
  PosterResponse,
  RunResult,
  RunTask,
} from "@/lib/types";
import Character from "./Character";
import MediaCapture from "./MediaCapture";
const ResultWorkbench = dynamic(() => import("./ResultWorkbench"));
const PosterWorkbench = dynamic(() => import("./PosterWorkbench"));
const DesignWorkspace = dynamic(() => import("./DesignWorkspace"));
import { getDesign, type DesignBundle } from "@/lib/design";
import { useWorkspace } from "./WorkspaceProvider";

import StoreDraftSuggestion from "./StoreDraftSuggestion";
import OpportunityPlan from "./OpportunityPlan";
import QuotationCard from "./QuotationCard";
import type { StoreAssignment } from "@/lib/channel";

interface PendingTurn {
  conversation_id: string;
  input: AgentTurnInput;
  turn_id?: string;
}
type Editor = {
  artifact: AgentArtifact;
  run?: RunResult;
  poster?: PosterResponse;
  design?: DesignBundle;
};
const terminal = new Set(["completed", "needs_input", "failed"]);

function ReadyCopy({ run }: { run: RunResult }) {
  const [copied, setCopied] = useState("");
  const paragraphs = [
    run.title,
    run.intro,
    ...(run.blocks || []).map((block) => [block.text, block.disclosures?.join("；")].filter(Boolean).join("\n")),
    run.cta,
    run.tags?.map((tag) => `#${tag.replace(/^#/, "")}`).join(" "),
  ].filter((value): value is string => !!value);
  async function copy() {
    try {
      await navigator.clipboard.writeText(paragraphs.join("\n\n"));
      setCopied("已复制文案");
    } catch {
      setCopied("可以展开文案，选中文字复制");
    }
  }
  return (
    <section aria-label="已完成的文案">
      <p>文案已准备好，配图继续制作。</p>
      <details>
        <summary className="mq-text-button">先读文案</summary>
        <div className="mq-conversation-text">
          {paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
        </div>
      </details>
      <button type="button" className="mq-text-button" onClick={() => void copy()}>
        {copied || "复制文案"}
      </button>
    </section>
  );
}

function ArtifactCard({
  artifact,
  onOpen,
}: {
  artifact: AgentArtifact;
  onOpen: () => void;
}) {
  const [preview, setPreview] = useState<{ image?: string; text?: string }>({});
  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        if (artifact.kind === "design") {
          const design = await getDesign(artifact.id);
          if (live) setPreview({ image: design.artboards[0]?.preview_url });
        } else if (artifact.kind === "poster") {
          const poster = await getPoster(artifact.id);
          if (live) setPreview({ image: poster.preview_url });
        } else {
          const run = await getRun(artifact.id);
          const first = run.images?.[0];
          const image = run.assets?.find((item) => item.id === first?.asset_id);
          if (live)
            setPreview({
              image: image?.url,
              text: run.intro || run.blocks?.[0]?.text,
            });
        }
      } catch {
        /* The open action reports an actionable permission or availability error. */
      }
    };
    void load();
    return () => {
      live = false;
    };
  }, [artifact.id, artifact.kind, artifact.version]);
  return (
    <button type="button" className="mq-conversation-artifact" onClick={onOpen}>
      {preview.image && (
        <img
          src={preview.image}
          alt={artifact.kind === "poster" ? "这次创作的海报" : "这篇内容的配图"}
        />
      )}
      <span>
        <strong>
          {artifact.title ||
            (artifact.kind === "poster" ? "这次的海报" : "这次的作品")}
        </strong>
        {preview.text && <span>{preview.text.slice(0, 150)}</span>}
        <em>打开作品 ↗</em>
      </span>
    </button>
  );
}

export default function ConversationWorkspace({
  seed,
  onSeedConsumed,
  onOpenDraft,
}: {
  seed?: { id: string; text: string } | null;
  onSeedConsumed?: () => void;
  onOpenDraft: (
    result: RunResult,
    task: RunTask,
    assignment: StoreAssignment,
  ) => void | Promise<void>;
}) {
  const { scopeKey, session, saveTrial } = useWorkspace();
  const storage = `matequill-agent:${scopeKey}`;
  const [conversation, setConversation] = useState<AgentConversation | null>(
    null,
  );
  const [recent, setRecent] = useState<AgentConversation[]>([]);
  const [text, setText] = useState("");
  const [media, setMedia] = useState<MediaIntake[]>([]);
  const [mediaBusy, setMediaBusy] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [workingMessage, setWorkingMessage] = useState("正在准备这次创作。");
  const [process, setProcess] = useState<AgentProcess | null>(null);
  // null = automatic (open while running, collapsed afterwards); a manual
  // summary click pins the user's choice for the rest of this turn.
  const [processOpen, setProcessOpen] = useState<boolean | null>(null);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [readyCopy, setReadyCopy] = useState<{
    conversationId: string; turnId: string; run: RunResult;
  } | null>(null);
  const readyCopyRequest = useRef<string | null>(null);
  const [questions, setQuestions] = useState<AgentQuestion[]>([]);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const [opening, setOpening] = useState(false);
  const [submission, setSubmission] = useState(false);
  const [notice, setNotice] = useState("");
  const [replyTo, setReplyTo] = useState<AgentArtifact | null>(null);
  const live = useRef(true);
  const epoch = useRef(0);
  const sending = useRef(false);
  const conversationRef = useRef<AgentConversation | null>(null);
  const transcript = useRef<HTMLOListElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const editorRoot = useRef<HTMLDivElement>(null);
  const consumedSeed = useRef<string | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const editing = !!editor;
  useEffect(() => {
    if (editing) {
      editorRoot.current?.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "instant" });
    } else if (focusRequest && !loading) composer.current?.focus();
  }, [editing, focusRequest, loading]);
  useEffect(() => {
    const field = composer.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 220)}px`;
  }, [text, loading, editor]);

  function updateConversation(value: AgentConversation) {
    conversationRef.current = value;
    setConversation(value);
    setQuestions(value.pending_questions || []);
    sessionStorage.setItem(`${storage}:active`, value.id);
    const address = new URL(window.location.href);
    address.searchParams.set("conversation", value.id);
    window.history.replaceState({}, "", address.pathname + address.search);
    setRecent((items) => [
      value,
      ...items.filter((item) => item.id !== value.id),
    ]);
  }
  function rememberPending(value: PendingTurn | null) {
    readyCopyRequest.current = null;
    setReadyCopy(null);
    setPending(value);
    setWorkingMessage("正在准备这次创作。");
    if (value) {
      setProcess(null);
      setProcessOpen(null);
      sessionStorage.setItem(`${storage}:pending`, JSON.stringify(value));
    } else sessionStorage.removeItem(`${storage}:pending`);
  }
  async function poll(value: PendingTurn) {
    const token = ++epoch.current;
    setBusy(true);
    setReconnecting(false);
    // Only GETs enter this recovery loop. The creative POST above remains a
    // single request, and its accepted turn ID stays in session storage.
    async function readProgress<T>(read: () => Promise<T>): Promise<T> {
      const started = Date.now();
      let attempts = 0;
      while (live.current && token === epoch.current) {
        try {
          const result = await read();
          if (live.current && token === epoch.current) setReconnecting(false);
          return result;
        } catch (reason) {
          const status = reason instanceof Error
            ? (reason as Error & { status?: number }).status
            : undefined;
          const transient = status !== undefined
            ? [500, 502, 503, 504].includes(status)
            : reason instanceof TypeError;
          if (!transient || !live.current || token !== epoch.current) throw reason;
          const pause = Math.min(1000 * 2 ** attempts++, 6000);
          if (Date.now() - started + pause > 35_000)
            throw new Error("这次工作还在继续，点一下接回最新进展。");
          setReconnecting(true);
          await new Promise((resolve) => setTimeout(resolve, pause));
        }
      }
      throw new Error("这次查看已结束。");
    }
    try {
      let turnId = value.turn_id;
      if (!turnId) {
        const turn = await sendAgentTurn(value.conversation_id, value.input);
        turnId = turn.id;
        if (!live.current || token !== epoch.current) return;
        value = { ...value, turn_id: turnId };
        rememberPending(value);
        const started = await readProgress(() => getConversation(value.conversation_id));
        if (!live.current || token !== epoch.current) return;
        updateConversation(started);
      }
      while (live.current && token === epoch.current) {
        const turn = await readProgress(() => getAgentTurn(value.conversation_id, turnId!));
        if (!live.current || token !== epoch.current) return;
        setWorkingMessage(turn.progress?.message || "正在准备这次创作。");
        if (turn.process) setProcess(turn.process);
        if (terminal.has(turn.status)) {
          readyCopyRequest.current = null;
          setReadyCopy(null);
          const current = await readProgress(() => getConversation(value.conversation_id));
          if (!live.current || token !== epoch.current) return;
          updateConversation(current);
          setQuestions(turn.questions || []);
          rememberPending(null);
          if (turn.status === "failed")
            setError(
              turn.message || "这次工作还没完成，可以接着告诉我怎么调整。",
            );
          return;
        }
        // The server checkpoints only reviewed, ready runs. Fetch that exact
        // authorized record as a second gate; never expose a writer draft or
        // delay image progress while loading it. Epoch + request identity
        // prevent a late response from another turn/workspace resurfacing.
        if (turn.artifact?.kind === "run") {
          const artifact = turn.artifact;
          const key = `${token}:${value.conversation_id}:${turnId}:${artifact.id}:${artifact.version}`;
          if (readyCopyRequest.current !== key) {
            readyCopyRequest.current = key;
            setReadyCopy(null);
            void getRun(artifact.id).then((run) => {
              if (!live.current || token !== epoch.current || readyCopyRequest.current !== key) return;
              if (run.run_id === artifact.id && run.status === "ready") {
                setReadyCopy({ conversationId: value.conversation_id, turnId: turnId!, run });
              } else setReadyCopy(null);
            }).catch(() => {
              if (live.current && token === epoch.current && readyCopyRequest.current === key) {
                readyCopyRequest.current = null;
                setReadyCopy(null);
              }
            });
          }
        } else if (turn.artifact) {
          readyCopyRequest.current = null;
          setReadyCopy(null);
        }
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    } catch (reason) {
      if (live.current && token === epoch.current) {
        const message =
          reason instanceof Error
            ? reason.message
            : "暂时没有取到最新进度，可以继续查看这次工作。";
        if (!value.turn_id && /对话刚刚有更新/.test(message)) {
          // A concurrent IM/web turn won the conversation version before this request.
          // The rejected input has no effects; keep it editable against the latest context.
          rememberPending(null);
          setText(value.input.text);
          try {
            updateConversation(await getConversation(value.conversation_id));
          } catch {
            /* Keep the draft available. */
          }
          setError("对话里有了新进展，已经接上。你可以接着发送这段话。");
        } else setError(message);
      }
    } finally {
      if (live.current && token === epoch.current) {
        setBusy(false);
        setReconnecting(false);
        sending.current = false;
      }
    }
  }

  useEffect(() => {
    live.current = true;
    readyCopyRequest.current = null;
    setReadyCopy(null);
    let active = true;
    async function load() {
      try {
        const response = await listConversations();
        if (!active) return;
        setRecent(response.items);
        const savedId = sessionStorage.getItem(`${storage}:active`);
        const rawPending = sessionStorage.getItem(`${storage}:pending`);
        const savedPending = rawPending
          ? (JSON.parse(rawPending) as PendingTurn)
          : null;
        const requested = new URLSearchParams(window.location.search).get(
          "conversation",
        );
        const id = requested || savedPending?.conversation_id || savedId;
        if (id) {
          const current = await getConversation(id);
          if (!active) return;
          updateConversation(current);
          if (savedPending && savedPending.conversation_id === id) {
            rememberPending(savedPending);
            void poll(savedPending);
          } else if (current.active_turn_id) {
            const resumed = {
              conversation_id: id,
              turn_id: current.active_turn_id,
              input: { text: "", media_ids: [], client_turn_id: "" },
            };
            rememberPending(resumed);
            void poll(resumed);
          }
        }
        const draft = sessionStorage.getItem(`${storage}:draft`);
        if (draft && active) setText(draft);
        const savedMedia = sessionStorage.getItem(`${storage}:media`);
        if (
          savedMedia &&
          active &&
          !savedPending &&
          !conversationRef.current?.active_turn_id
        ) {
          try {
            setMedia(JSON.parse(savedMedia) as MediaIntake[]);
          } catch {
            sessionStorage.removeItem(`${storage}:media`);
          }
        }
      } catch (reason) {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "这次对话暂时没有打开，请重试。",
          );
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      live.current = false;
      epoch.current++;
    };
  }, [storage]);
  useEffect(() => {
    if (!loading) sessionStorage.setItem(`${storage}:draft`, text);
  }, [text, loading, storage]);
  useEffect(() => {
    if (!loading)
      sessionStorage.setItem(`${storage}:media`, JSON.stringify(media));
  }, [media, loading, storage]);
  useEffect(() => {
    // Restore the existing draft first; an explicit product choice wins once.
    if (!seed || loading || consumedSeed.current === seed.id) return;
    consumedSeed.current = seed.id;
    setText(seed.text);
    setFocusRequest((value) => value + 1);
    onSeedConsumed?.();
  }, [seed, loading, onSeedConsumed]);
  useEffect(() => {
    transcript.current?.lastElementChild?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }, [conversation?.messages.length, busy]);

  async function send(override?: string, referenced?: AgentArtifact) {
    if (sending.current || busy || mediaBusy) return;
    if (pending) {
      sending.current = true;
      setError("");
      void poll(pending);
      return;
    }
    const words = (override ?? text).trim();
    if (!words && !media.length) return;
    sending.current = true;
    setSubmission(true);
    setError("");
    setQuestions([]);
    setNotice("");
    try {
      const current =
        conversationRef.current ||
        (await createConversation(words.slice(0, 45)));
      if (!live.current) return;
      updateConversation(current);
      const reference = referenced || replyTo || current.current_artifact;
      const input: AgentTurnInput = {
        text: words,
        media_ids: media.map((item) => item.id),
        client_turn_id: crypto.randomUUID(),
        expected_version: current.version,
        ...(reference ? { reply_to_artifact: reference } : {}),
      };
      const value = { conversation_id: current.id, input };
      // Save before the first network mutation, so a lost response reuses the turn ID.
      rememberPending(value);
      setText("");
      setMedia([]);
      setReplyTo(null);
      await poll(value);
    } catch (reason) {
      if (live.current)
        setError(
          reason instanceof Error
            ? reason.message
            : "这段话还没有送达，可以再试一次。",
        );
    } finally {
      if (live.current) {
        sending.current = false;
        setSubmission(false);
      }
    }
  }
  async function choose(id: string) {
    if (busy || submission) return;
    setError("");
    setQuestions([]);
    setEditor(null);
    setReplyTo(null);
    setLoading(true);
    try {
      const current = await getConversation(id);
      updateConversation(current);
      if (current.active_turn_id) {
        const resumed = {
          conversation_id: id,
          turn_id: current.active_turn_id,
          input: { text: "", media_ids: [], client_turn_id: "" },
        };
        rememberPending(resumed);
        void poll(resumed);
      } else rememberPending(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "这次对话没有打开。");
    } finally {
      setLoading(false);
      setFocusRequest((value) => value + 1);
    }
  }
  function fresh() {
    if (busy || submission) return;
    epoch.current++;
    setConversation(null);
    conversationRef.current = null;
    setQuestions([]);
    setError("");
    setText("");
    setMedia([]);
    setEditor(null);
    setReplyTo(null);
    setNotice("");
    rememberPending(null);
    sessionStorage.removeItem(`${storage}:active`);
    const address = new URL(window.location.href);
    address.searchParams.delete("conversation");
    window.history.replaceState({}, "", address.pathname + address.search);
    setFocusRequest((value) => value + 1);
  }
  async function openArtifact(artifact: AgentArtifact) {
    setOpening(true);
    setError("");
    try {
      if (artifact.kind === "design") {
        const design = await getDesign(artifact.id);
        setEditor({ artifact: { ...artifact, version: design.version }, design });
      } else if (artifact.kind === "poster") {
        const poster = await getPoster(artifact.id);
        setEditor({
          artifact: { ...artifact, version: poster.document.version },
          poster,
        });
      } else setEditor({ artifact, run: await getRun(artifact.id) });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "这份作品暂时没有打开。",
      );
    } finally {
      setOpening(false);
    }
  }
  async function returnToConversation() {
    const current = conversationRef.current;
    if (!current) {
      setEditor(null);
      setFocusRequest((value) => value + 1);
      return;
    }
    setOpening(true);
    try {
      const refreshed = await getConversation(current.id);
      updateConversation(refreshed);
      setReplyTo(editor?.artifact || null);
      setEditor(null);
      setFocusRequest((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "暂时没有取到最新对话。",
      );
    } finally {
      setOpening(false);
    }
  }
  async function savePoster(poster: PosterResponse) {
    setEditor((value) =>
      value
        ? {
            ...value,
            poster,
            artifact: { ...value.artifact, version: poster.document.version },
          }
        : null,
    );
    const current = conversationRef.current;
    if (!current) return;
    try {
      const artifact: AgentArtifact = {
        kind: "poster",
        id: poster.id,
        version: poster.document.version,
        title: current.current_artifact?.title || "这次的海报",
      };
      const updated = await attachArtifact(
        current.id,
        artifact,
        current.version,
      );
      updateConversation(updated);
    } catch {
      setNotice("画布修改已保存。回到对话时会取回最新进度。");
    }
  }

  if (editor)
    return (
      <div ref={editorRoot} tabIndex={-1} role="region" aria-label="作品编辑" className="mq-conversation-editor">
        <button
          type="button"
          className="mq-text-button mq-return-conversation"
          disabled={opening || busy}
          onClick={() => void returnToConversation()}
        >
          ← 回到对话，接着说
        </button>
        {editor.run && (
          <ResultWorkbench
            result={editor.run}
            task={editor.run._poster_task || null}
            busy={busy}
            onBack={() => void returnToConversation()}
            onRevise={(instruction) => {
              setEditor(null);
              void send(instruction, editor.artifact);
            }}
          />
        )}
        {editor.design && (
          <DesignWorkspace initial={editor.design} onBack={() => void returnToConversation()} onChange={(design) => {
            const artifact: AgentArtifact = { kind: "design", id: design.id, version: design.version, title: design.title };
            setEditor({ artifact, design });
            const current = conversationRef.current;
            if (current) void attachArtifact(current.id, artifact, current.version).then(updateConversation).catch(() => setNotice("图片已保存，回到对话后可继续创作。"));
          }} />
        )}
        {editor.poster && (
          <PosterWorkbench
            poster={editor.poster}
            onChange={(poster) => void savePoster(poster)}
            onBack={() => void returnToConversation()}
          />
        )}
        {notice && (
          <p className="mq-inline-notice" role="status">
            {notice}
          </p>
        )}
      </div>
    );

  const messages =
    conversation?.messages.filter(
      (item) => item.role === "user" || item.role === "assistant",
    ) || [];
  const hasMessages = messages.length > 0;
  const errorInTranscript = messages.at(-1)?.role === "assistant" && messages.at(-1)?.text === error;
  const showSaveTrial = session.is_guest && /额度|保存到你的门店|体验次数/.test(error);
  const lastRequest = [...messages].reverse().find((message) => message.role === "user")?.text;
  return (
    <section
      className={`mq-conversation ${hasMessages ? "has-messages" : "is-new"}`}
      aria-label="和 MateQuill 一起创作"
    >
      <header className="mq-conversation-header">
        {hasMessages ? (
          <h1>{conversation?.current_artifact?.title || "我们接着说。"}</h1>
        ) : (
          <div className="mq-conversation-welcome mq-enter">
            <Character state={focused ? "focus" : "idle"} />
            <h1>
              今天，有什么值得分享<span>？</span>
            </h1>
            <p>拍一张，说一句。一起把眼前的故事讲好。</p>
          </div>
        )}
        <div className="mq-conversation-navigation">
          {recent.length > 0 && (
            <details>
              <summary>最近的对话</summary>
              <div>
                {recent.slice(0, 12).map((item) => (
                  <button
                    key={item.id}
                    className="mq-text-button"
                    disabled={busy || submission}
                    onClick={(event) => {
                      (
                        event.currentTarget.closest(
                          "details",
                        ) as HTMLDetailsElement | null
                      )?.removeAttribute("open");
                      void choose(item.id);
                    }}
                  >
                    {item.title || "一起创作"}
                  </button>
                ))}
              </div>
            </details>
          )}
          {hasMessages && (
            <button
              className="mq-text-button"
              disabled={busy || submission}
              onClick={fresh}
            >
              新对话 ＋
            </button>
          )}
        </div>
      </header>
      {hasMessages && (
        <ol
          ref={transcript}
          className="mq-conversation-messages"
          aria-label="对话记录"
        >
          {messages.map((message) => (
            <li
              key={message.id}
              className={`mq-conversation-message is-${message.role}`}
            >
              <span className="mq-sr-only">
                {message.role === "user" ? "你" : "MateQuill"}
              </span>
              {message.text && (
                <div className="mq-conversation-text">{message.text}</div>
              )}
              {!!message.media_ids?.length && (
                <p className="mq-conversation-materials">
                  已附上 {message.media_ids.length} 份现场素材
                </p>
              )}
              {message.artifact && (
                <ArtifactCard
                  artifact={message.artifact}
                  onOpen={() => void openArtifact(message.artifact!)}
                />
              )}
              {message.quotation && <QuotationCard quoteId={message.quotation.id} />}
            </li>
          ))}
          {readyCopy && readyCopy.conversationId === conversation?.id && readyCopy.turnId === pending?.turn_id && (
            <li className="mq-conversation-message is-assistant" key={`ready-copy-${readyCopy.turnId}`}>
              <ReadyCopy key={readyCopy.run.run_id} run={readyCopy.run} />
            </li>
          )}
          {(busy || process) && (
            <li className="mq-conversation-working" role={busy ? "status" : undefined}>
              <Character state={busy ? "thinking" : "done"} />
              <details
                className="mq-conversation-process"
                open={processOpen ?? busy}
              >
                <summary
                  onClick={(event) => {
                    event.preventDefault();
                    setProcessOpen(!(processOpen ?? busy));
                  }}
                >
                  {busy
                    ? reconnecting
                      ? "正在接回最新进展。"
                      : workingMessage
                    : `创作过程${
                        process?.elapsed_ms
                          ? ` · ${Math.max(1, Math.round(process.elapsed_ms / 1000))} 秒`
                          : ""
                      }`}
                </summary>
                {!!process?.steps.length && (
                  <ol className="mq-process-steps">
                    {process.steps.map((step, index) => (
                      <li key={`${step.key}-${index}`} className={`is-${step.status}`}>
                        <span className="mq-process-step-title">{step.title}</span>
                        {typeof step.duration_ms === "number" && (
                          <em>{` ${Math.max(1, Math.round(step.duration_ms / 1000))}s`}</em>
                        )}
                        {step.status === "active" && typeof step.elapsed_ms === "number" && (
                          <em className="is-live">{` ${Math.max(1, Math.round(step.elapsed_ms / 1000))}s`}</em>
                        )}
                        {!!step.details?.length && (
                          <ul>
                            {step.details.map((detail, detailIndex) => (
                              <li key={detailIndex}>
                                {detail.stage}
                                {detail.text ? ` · ${detail.text}` : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </details>
            </li>
          )}
        </ol>
      )}
      {!busy && questions.some((question) => question.options.length > 0) && (
        <section
          className="mq-conversation-questions"
          aria-label="这次需要你决定的事"
        >
          {questions
            .filter((question) => question.options.length > 0)
            .map((question) => (
              <div key={question.field}>
                <p>{question.label}</p>
                <div>
                  {question.options.map((option) => (
                    <button
                      key={option.value}
                      className="mq-conversation-choice"
                      onClick={() => void send(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
        </section>
      )}
      {conversation?.current_opportunity && !loading && (
        <details className="mq-conversation-plan" open={!conversation.current_opportunity.artifacts?.length}>
        <summary>这次的创作方案</summary>
        <OpportunityPlan
          plan={conversation.current_opportunity}
          disabled={busy || submission || mediaBusy || !!pending}
          onContinue={(instruction) => void send(instruction)}
          onOpen={(artifact) => void openArtifact(artifact)}
        />
        </details>
      )}
      {error && (
        <div className={errorInTranscript ? "mq-conversation-retry" : "mq-error"} role="alert">
          {!errorInTranscript && <p>{error}</p>}
          {pending && (
            <button
              className="mq-text-button"
              disabled={busy || submission}
              onClick={() => void send()}
            >
              继续查看这次工作
            </button>
          )}
          {!pending && lastRequest && (
            <button className="mq-text-button" disabled={busy || submission} onClick={() => void send(lastRequest)}>
              重新尝试这条
            </button>
          )}
          {showSaveTrial && (
            <button className="mq-text-button" onClick={saveTrial}>
              保存到我的门店
            </button>
          )}
        </div>
      )}
      {loading ? (
        <div className="mq-conversation-loading" role="status">
          <Character state="thinking" />
          <p>接上刚才的思路。</p>
        </div>
      ) : (
        <form
          className="mq-conversation-composer"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          {replyTo && (
            <div className="mq-conversation-reply">
              <span>接着修改：{replyTo.title || "这份作品"}</span>
              <button
                type="button"
                className="mq-text-button"
                aria-label="取消指定作品"
                onClick={() => setReplyTo(null)}
              >
                取消
              </button>
            </div>
          )}
          <label className="mq-sr-only" htmlFor="conversation-message">
            说说这次的目标，或接着修改刚才的作品
          </label>
          <textarea
            ref={composer}
            id="conversation-message"
            value={text}
            rows={2}
            maxLength={6000}
            onChange={(event) => setText(event.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            disabled={busy || submission || !!pending}
          />
          <div className="mq-composer-toolbar">
          <MediaCapture
            showUpload={uploadOpen}
            disabled={busy || submission || !!pending}
            media={media}
            onMedia={(item, replaceId) =>
              setMedia((items) => [
                ...items.filter(
                  (previous) =>
                    previous.id !== replaceId && previous.id !== item.id,
                ),
                item,
              ])
            }
            onRemove={(id) =>
              setMedia((items) => items.filter((item) => item.id !== id))
            }
            onBusy={setMediaBusy}
            onTranscript={(words) =>
              setText((previous) =>
                [previous.trim(), words]
                  .filter(Boolean)
                  .join("\n")
                  .slice(0, 6000),
              )
            }
          />
          <footer>
            <button
              type="button"
              className="mq-text-button mq-composer-attachment"
              aria-label={uploadOpen ? "收起录音文件选项" : "上传录音文件"}
              title={uploadOpen ? "收起录音文件选项" : "上传录音文件"}
              aria-expanded={uploadOpen}
              onClick={() => setUploadOpen(!uploadOpen)}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                {uploadOpen ? (
                  <path d="m6 6 12 12M18 6 6 18" />
                ) : (
                  <path d="m20 11-8.5 8.5a5 5 0 0 1-7.1-7.1L13 3.8a3.3 3.3 0 0 1 4.7 4.7L9.2 17a1.7 1.7 0 0 1-2.4-2.4l8-8" />
                )}
              </svg>
            </button>
            <button
              className="mq-primary"
              type="submit"
              aria-label={submission ? "正在发送" : hasMessages ? "发送回复" : "开始创作"}
              title={hasMessages ? "发送回复" : "开始创作"}
              disabled={
                busy ||
                submission ||
                mediaBusy ||
                !!pending ||
                (!text.trim() && !media.length)
              }
            >
              <svg aria-hidden="true" className={submission ? "mq-composer-send-progress" : undefined} viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                {submission ? <path d="M20 12a8 8 0 1 1-8-8" /> : <path d="M12 19V5m-6 6 6-6 6 6" />}
              </svg>
            </button>
          </footer>
          </div>
        </form>
      )}
      <StoreDraftSuggestion visible={!hasMessages && !loading && !busy && !submission} onOpen={onOpenDraft}
        onOpenConversation={async (id) => {
          await choose(id);
          const current = conversationRef.current;
          if (current?.id === id && !current.active_turn_id && current.current_artifact)
            await openArtifact(current.current_artifact);
        }} />
      {opening && (
        <p className="mq-conversation-opening" role="status">
          正在打开作品。
        </p>
      )}
    </section>
  );
}
