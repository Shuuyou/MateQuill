"use client";

import { useEffect, useState, useTransition } from "react";
import dynamic from "next/dynamic";
import IntakeComposer from "@/components/IntakeComposer";
import Clarification from "@/components/Clarification";
import { getDesign, type DesignBundle } from "@/lib/design";
import Character from "@/components/Character";
import { Icon } from "@/components/Icons";
import { catalog } from "@/lib/api";
import { useCreation } from "@/hooks/useCreation";
import type { CatalogItem, RunResult, RunTask, StoreProfile } from "@/lib/types";
import type { StoreAssignment } from "@/lib/channel";
import { workspaceRequest } from "@/lib/workspace";
import { useWorkspace } from "@/components/WorkspaceProvider";
import WorkspaceMenu from "@/components/WorkspaceMenu";
import CreationSubmission from "@/components/CreationSubmission";
import ChooseCreationStore from "@/components/ChooseCreationStore";
import ConversationWorkspace from "@/components/ConversationWorkspace";

const ResultWorkbench = dynamic(() => import("@/components/ResultWorkbench"));
const PosterWorkbench = dynamic(() => import("@/components/PosterWorkbench"));
const DesignWorkspace = dynamic(() => import("@/components/DesignWorkspace"));
const StoreCampaign = dynamic(() => import("@/components/StoreCampaign"));
const ProductLibrary = dynamic(() => import("@/components/ProductLibrary"));
const ChannelWorkspace = dynamic(() => import("@/components/ChannelWorkspace"));
const TeamWorkspace = dynamic(() => import("@/components/TeamWorkspace"));

type View = "agent" | "create" | "history" | "campaign" | "catalog" | "planning" | "team";
const NAV: {
  view: View;
  icon: "write" | "history" | "store" | "book";
  label: string;
}[] = [
  { view: "agent", icon: "write", label: "开始创作" },
  { view: "campaign", icon: "store", label: "团队活动" },
  { view: "planning", icon: "write", label: "门店策划" },
  { view: "catalog", icon: "book", label: "产品与生态" },
  { view: "history", icon: "history", label: "创作记录" },
];

export default function Page() {
  const { session, scopeKey, switchWorkspace, saveTrial } = useWorkspace();
  const creation = useCreation(scopeKey);
  const roles = session.workspace!.roles;
  const canManage = roles.some((role) =>
    ["admin", "operator", "regional_manager"].includes(role),
  );
  const canTeam = roles.some((role) => ["admin", "operator"].includes(role));
  const [view, setView] = useState<View>("agent");
  const [navigating, startNavigation] = useTransition();
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); }, [view]);
  const [agentSeed, setAgentSeed] = useState<{ id: string; text: string } | null>(null);
  const [viewReady, setViewReady] = useState(false);
  const [design, setDesign] = useState<DesignBundle | null>(null);
  useEffect(() => {
    let cancelled = false;
    setDesign(null);
    const identifier = sessionStorage.getItem(`matequill-open-design:${scopeKey}`);
    const savedView = sessionStorage.getItem(`matequill-view-v3:${scopeKey}`);
    if (identifier && savedView === "create") void getDesign(identifier).then((value) => {
      if (!cancelled) { setDesign(value); setView("create"); }
    }).catch(() => {
      if (!cancelled) { sessionStorage.removeItem(`matequill-open-design:${scopeKey}`); setView("history"); }
    });
    return () => { cancelled = true; };
  }, [scopeKey]);
  useEffect(() => {
    if (view !== "create" && design) {
      setDesign(null);
      sessionStorage.removeItem(`matequill-open-design:${scopeKey}`);
    }
  }, [view]);
  function rememberDesign(value: DesignBundle | null) {
    setDesign(value);
    if (value) sessionStorage.setItem(`matequill-open-design:${scopeKey}`, value.id);
    else sessionStorage.removeItem(`matequill-open-design:${scopeKey}`);
  }
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [product, setProduct] = useState("");
  const [pendingTask, setPendingTask] = useState<RunTask | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [historyItems, setHistoryItems] = useState<
    { id: string; kind: string; title: string; created_at: string | number }[]
  >([]);
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyReload, setHistoryReload] = useState(0);
  const busy = creation.phase !== "idle";
  async function refreshCatalog() {
    setCatalogLoading(true);
    try {
      const response = await catalog();
      setItems(response.items);
      setCatalogError("");
    } catch {
      setCatalogError("产品资料暂时没有载入。你的需求可以先记下来，稍后再试。");
    } finally {
      setCatalogLoading(false);
    }
  }
  useEffect(() => {
    void refreshCatalog();
    const params = new URLSearchParams(window.location.search);
    const product = params.get("product");
    if (product) setProduct(product);
    if (params.get("view") === "catalog") setView("catalog");
    try {
      const savedView = sessionStorage.getItem(
        `matequill-view-v3:${scopeKey}`,
      ) as View | null;
      if (
        savedView &&
        [
          "agent",
          "create",
          "history",
          "campaign",
          "catalog",
          "planning",
          "team",
        ].includes(savedView)
      )
        setView(savedView);
      if (params.get("conversation")) setView("agent");
      const pending = sessionStorage.getItem(`matequill-pending:${scopeKey}`);
      if (pending) {
        const value = JSON.parse(pending) as {
          task?: RunTask | null;
          product?: string;
          assignmentId: string;
          generate?: boolean;
          result?: RunResult;
        };
        if (value.task && value.result)
          creation.openDraft(value.result, value.task, value.assignmentId);
        else if (value.task && value.generate)
          void creation.direct(value.task, value.assignmentId);
        else if (value.task)
          creation.startAssignment(value.task, value.assignmentId);
        setProduct(value.task?.product || value.product || "");
        setView("create");
        sessionStorage.removeItem(`matequill-pending:${scopeKey}`);
      }
    } catch {
      /* An interrupted switch can start a fresh task. */
    }
    setViewReady(true);
  }, []);
  useEffect(() => {
    if (viewReady)
      sessionStorage.setItem(`matequill-view-v3:${scopeKey}`, view);
  }, [view, viewReady, scopeKey]);
  useEffect(() => {
    if (view !== "history") return;
    let active = true;
    setHistoryLoading(true);
    setHistoryError("");
    workspaceRequest<{ items: typeof historyItems }>("/api/creations")
      .then((value) => {
        if (!active) return;
        setHistoryItems(value.items);
        setHistoryError("");
      })
      .catch((reason: Error) => { if (active) setHistoryError(reason.message); })
      .finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [view, scopeKey, historyReload]);
  useEffect(() => {
    const body = document.body;
    body.classList.add("mq-next");
    return () => body.classList.remove("mq-next");
  }, []);
  function generate(task: RunTask) {
    if (!session.workspace!.store_id && !task.store_id) {
      setPendingTask(task);
      setView("create");
      return;
    }
    if (task.store_id && task.store_id !== session.workspace!.store_id) {
      const key = [
        session.user!.id,
        session.workspace!.tenant_id,
        task.store_id,
      ].join(":");
      sessionStorage.setItem(
        `matequill-pending:${key}`,
        JSON.stringify({ task, assignmentId: "", generate: true }),
      );
      void switchWorkspace(session.workspace!.tenant_id, task.store_id).catch(
        (reason: Error) => {
          sessionStorage.removeItem(`matequill-pending:${key}`);
          setCatalogError(reason.message);
        },
      );
      return;
    }
    setView("create");
    void creation.direct(task);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  async function chooseStore(store: StoreProfile) {
    const key = [session.user!.id, session.workspace!.tenant_id, store.id].join(
      ":",
    );
    sessionStorage.setItem(
      `matequill-pending:${key}`,
      JSON.stringify({
        product,
        task: pendingTask ? { ...pendingTask, store_id: store.id } : null,
        assignmentId: "",
        generate: !!pendingTask,
      }),
    );
    try {
      await switchWorkspace(session.workspace!.tenant_id, store.id);
    } catch (reason) {
      sessionStorage.removeItem(`matequill-pending:${key}`);
      throw reason;
    }
  }
  function selectProduct(name: string) {
    setProduct(name);
    creation.update({
      result: null,
      poster: null,
      answers: {},
      assignmentId: "",
    });
    creation.clearQuestions();
    setAgentSeed({ id: crypto.randomUUID(), text: `帮我为${name}准备一篇适合我们店的内容。` });
    setView("agent");
  }
  async function startAssignment(
    task: RunTask,
    assignment: StoreAssignment,
    immediately = false,
  ) {
    if (session.workspace!.store_id !== assignment.store_id) {
      const key = [
        session.user!.id,
        session.workspace!.tenant_id,
        assignment.store_id,
      ].join(":");
      sessionStorage.setItem(
        `matequill-pending:${key}`,
        JSON.stringify({ task, assignmentId: assignment.id, generate: immediately }),
      );
      try {
        await switchWorkspace(
          session.workspace!.tenant_id,
          assignment.store_id,
        );
      } catch (reason) {
        sessionStorage.removeItem(`matequill-pending:${key}`);
        throw reason;
      }
      return;
    }
    if (immediately) void creation.direct(task, assignment.id);
    else creation.startAssignment(task, assignment.id);
    setProduct(task.product);
    setView("create");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  async function openAssignmentDraft(result: RunResult, task: RunTask, assignment: StoreAssignment) {
    if (session.workspace!.store_id !== assignment.store_id) {
      const key = [session.user!.id, session.workspace!.tenant_id, assignment.store_id].join(":");
      sessionStorage.setItem(`matequill-pending:${key}`, JSON.stringify({ task, result, assignmentId: assignment.id }));
      try { await switchWorkspace(session.workspace!.tenant_id, assignment.store_id); }
      catch (reason) { sessionStorage.removeItem(`matequill-pending:${key}`); throw reason; }
      return;
    }
    creation.openDraft(result, task, assignment.id);
    setProduct(task.product);
    setView("create");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  return (
    <div className={`mq-app ${expanded ? "mq-nav-expanded" : ""}`}>
      <aside className="mq-sidebar" aria-label="工作空间导航">
        <div className="mq-sidebar-header">
          <div className="mq-brand" aria-label="MateQuill">
            <span className="mq-brand-symbol" aria-hidden="true">
              <Character />
            </span>
            <span className="mq-brand-wordmark">
              MateQuill<small>场景有据</small>
            </span>
          </div>
          <button
            type="button"
            className="mq-sidebar-toggle"
            aria-label={expanded ? "收起导航" : "展开导航"}
            title={expanded ? "收起导航" : "展开导航"}
            aria-expanded={expanded}
            aria-controls="mq-primary-navigation"
            onClick={() => setExpanded((value) => !value)}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="3" />
              <path d="M9 4v16" />
              <path className="mq-sidebar-toggle-arrow" d="m13 9 3 3-3 3" />
            </svg>
          </button>
        </div>
        <nav id="mq-primary-navigation">
          {NAV.filter((item) => (item.view !== "planning" || canManage) && (!session.is_guest || item.view !== "campaign")).map(
            (item) => (
              <button
                key={item.view}
                className={view === item.view || (view === "create" && item.view === "agent") ? "is-selected" : ""}
                aria-current={view === item.view || (view === "create" && item.view === "agent") ? "page" : undefined}
                aria-label={item.label}
                title={item.label}
                disabled={busy}
                onClick={() => {
                  if (item.view === "create") {
                    creation.reset();
                    setProduct("");
                  }
                  startNavigation(() => setView(item.view));
                }}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
              </button>
            ),
          )}
        </nav>
        {canTeam && (
          <button
            type="button"
            className="mq-settings-link"
            aria-label="团队成员"
            title="团队成员"
            disabled={busy}
            onClick={() => setView("team")}
          >
            <Icon name="settings" />
            <span>团队成员</span>
          </button>
        )}
      </aside>
      <div className="mq-topline">
        <span>
          {view === "agent" ? "创作" : view === "create" ? "作品" : NAV.find((item) => item.view === view)?.label || "工作空间"}
        </span>
        <WorkspaceMenu disabled={busy} onTeam={canTeam ? () => startNavigation(() => setView("team")) : undefined} />
      </div>
      <main
        aria-busy={navigating}
        className={`mq-main ${view === "create" && !design && !creation.result && !creation.poster && !creation.questions.length ? "mq-main-intake" : ""}`}
      >
        {view === "agent" && <ConversationWorkspace seed={agentSeed} onSeedConsumed={() => setAgentSeed(null)} onOpenDraft={openAssignmentDraft} />}
        {view === "create" && design && <DesignWorkspace initial={design} onChange={rememberDesign} onBack={() => { rememberDesign(null); setView("history"); }} />}
        {view === "create" && !design && (
          <>
            {creation.assignmentId &&
              !busy &&
              !creation.result &&
              !creation.poster && (
                <div className="mq-active-assignment">
                  <span>团队活动 · {product || creation.task?.product}</span>
                  <button
                    className="mq-text-button"
                    onClick={() => setView("campaign")}
                  >
                    查看活动
                  </button>
                </div>
              )}
            {!session.workspace!.store_id &&
            !creation.result &&
            !creation.poster ? (
              <ChooseCreationStore
                onSelect={chooseStore}
                onManage={() => setView("planning")}
              />
            ) : busy && !creation.result && !creation.poster ? (
              <section
                className="mq-generating"
                role="status"
                aria-live="polite"
              >
                <Character state="thinking" />
                <h1>
                  {creation.phase === "understanding"
                    ? "让我想想，这次怎么讲。"
                    : "正在把这个故事，好好写出来。"}
                </h1>
                <p>{creation.query}</p>
                <span>
                  {creation.phase === "understanding"
                    ? "理解顾客的需要"
                    : "整理产品资料，准备正文与配图"}
                </span>
              </section>
            ) : creation.poster ? (
              <PosterWorkbench
                poster={creation.poster}
                onChange={(poster) => creation.update({ poster })}
                onBack={() => creation.update({ poster: null })}
              />
            ) : creation.result ? (
              <ResultWorkbench
                key={creation.result.run_id}
                result={creation.result}
                task={creation.task}
                busy={busy}
                onRevise={(instruction) => void creation.revise(instruction)}
                onBack={() => creation.update({ result: null })}
              />
            ) : creation.questions.length ? (
              <Clarification
                key={creation.questions
                  .map((question) => question.field)
                  .join("-")}
                query={creation.query}
                questions={creation.questions}
                busy={busy}
                onAnswer={(answers) => void creation.resolve(answers)}
                onBack={() => {
                  creation.clearQuestions();
                  creation.update({ answers: {} });
                }}
              />
            ) : (
              <IntakeComposer
                query={creation.query}
                onQuery={(query) =>
                  creation.update({
                    query,
                    answers: {},
                    ...(creation.assignmentId ? {} : { task: null }),
                  })
                }
                media={creation.media}
                onMedia={(media, replaceId) =>
                  creation.update((previous) => ({
                    media: [
                      ...previous.media.filter(
                        (item) => item.id !== replaceId && item.id !== media.id,
                      ),
                      media,
                    ],
                  }))
                }
                onRemove={(id) =>
                  creation.update({
                    media: creation.media.filter((item) => item.id !== id),
                  })
                }
                onSubmit={(answers) => void creation.resolve(answers)}
                product={product}
                onProduct={setProduct}
                items={items}
                busy={busy}
              />
            )}
            {creation.assignmentId && creation.result?.status === "ready" && (
              <CreationSubmission
                key={`submission:${creation.result.run_id}`}
                assignmentId={creation.assignmentId}
                runId={creation.result.run_id}
                onOpenTeam={() => setView("campaign")}
              />
            )}
            {creation.error && (
              <div className="mq-error" role="alert">
                <p>{creation.error}</p>
                {session.is_guest && <button className="mq-primary" onClick={saveTrial}>保存到我的门店</button>}
                <button
                  className="mq-text-button"
                  onClick={() => {
                    if (creation.pendingJob) void creation.resume();
                    else if (creation.task)
                      void creation.direct(
                        creation.task,
                        creation.assignmentId,
                      );
                    else void creation.resolve();
                  }}
                >
                  {creation.pendingJob ? "继续查看这次创作" : "重新尝试"}
                </button>
              </div>
            )}
            {catalogError && !creation.result && (
              <p className="mq-catalog-error">
                {catalogError}
                <button
                  className="mq-text-button"
                  onClick={() => void refreshCatalog()}
                >
                  重新载入
                </button>
              </p>
            )}
          </>
        )}
        {view === "campaign" && (
          <ChannelWorkspace products={items} onStart={startAssignment} onOpenDraft={openAssignmentDraft} />
        )}
        {view === "planning" && canManage && (
          <StoreCampaign products={items} onGenerate={generate} />
        )}
        {view === "team" && canTeam && <TeamWorkspace />}
        {view === "catalog" && catalogLoading && !items.length ? (
          <p className="mq-catalog-error" role="status">
            正在取回产品资料…
          </p>
        ) : view === "catalog" && catalogError && !items.length ? (
          <div className="mq-error" role="alert">
            <p>{catalogError}</p>
            <button
              className="mq-text-button"
              onClick={() => void refreshCatalog()}
            >
              重新载入
            </button>
          </div>
        ) : (
          view === "catalog" && (
            <ProductLibrary
              items={items}
              onSelect={selectProduct}
              onGenerate={generate}
              onRefresh={() => void refreshCatalog()}
            />
          )
        )}
        {view === "history" && (
          <section className="mq-history mq-enter">
            <header className="mq-page-heading">
              <h1>创作记录</h1>
            </header>
            {historyLoading && <p role="status">正在读取创作记录…</p>}
            {!historyLoading && historyError && (
              <div role="alert" className="mq-error">
                <p>{historyError}</p>
                <button className="mq-text-button" onClick={() => setHistoryReload((value) => value + 1)}>重新读取</button>
              </div>
            )}
            {!historyLoading && !historyError && (historyItems.length ? (
              <ol>
                {historyItems.map((item) => (
                  <li key={item.id}>
                    <button
                      onClick={async () => {
                        if (item.kind === "design") {
                          try { rememberDesign(await getDesign(item.id)); setView("create"); }
                          catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "这组图片暂时没有打开，请重试。"); }
                          return;
                        }
                        setDesign(null);
                        void creation.reopen(
                          item.kind === "poster"
                            ? `poster:${item.id}`
                            : item.id,
                        );
                        setView("create");
                      }}
                    >
                      <span>{item.title}</span>
                      <time
                        dateTime={new Date(
                          typeof item.created_at === "number"
                            ? item.created_at * 1000
                            : item.created_at,
                        ).toISOString()}
                      >
                        {new Date(
                          typeof item.created_at === "number"
                            ? item.created_at * 1000
                            : item.created_at,
                        ).toLocaleString("zh-CN", {
                          month: "long",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </time>
                      <Icon name="arrow" />
                    </button>
                  </li>
                ))}
              </ol>
            ) : (
              <div className="mq-empty">
                <Character />
                <h2>第一篇好内容，就从今天开始。</h2>
                <button
                  className="mq-primary"
                  onClick={() => setView("agent")}
                >
                  开始创作<span>↗</span>
                </button>
              </div>
            ))}
          </section>
        )}
      </main>
    </div>
  );
}
