"use client";
import { useEffect, useRef, useState } from "react";
import { stores } from "@/lib/api";
import {
  CHANNEL_STATUS,
  type ChannelCampaign,
  type ContentSubmission,
  type StoreAssignment,
  type PreparedAssignment,
  type PrepareAssignmentResponse,
  type StoreOpportunity,
} from "@/lib/channel";
import type { CatalogItem, RunResult, RunTask, StoreProfile } from "@/lib/types";
import { workspaceRequest } from "@/lib/workspace";
import { useWorkspace } from "./WorkspaceProvider";
import Character from "./Character";
import OpportunityPlan from "./OpportunityPlan";
import CampaignOpportunities from "./CampaignOpportunities";
import styles from "./ChannelWorkspace.module.css";

import { draftKey, getDraftState, prepareAssignmentDraft, subscribeDrafts } from "@/lib/assignmentDraft";

const PATH = "/api/channel";
interface PartnerGrant {
  id: string;
  title: string;
  sender_tenant_id: string;
  recipient_tenant_id: string;
  expires_at: number;
  accepted_at: number | null;
  revoked_at: number | null;
}
interface CampaignProgress {
  campaign_id: string;
  stores: number;
  counts: Record<string, number>;
}
function OpportunityNote({ opportunity }: { opportunity: PreparedAssignment["opportunity"] }) {
  const text = opportunity.summary || opportunity.reason || opportunity.reasons?.join(" ") || "";
  if (!text) return null;
  return (
    <details className="mq-preparation-explanation">
      <summary>选题思路</summary>
      <p className="mq-preparation-reason">{text}</p>
    </details>
  );
}
export default function ChannelWorkspace({
  products,
  onStart,
  onOpenDraft,
}: {
  products: CatalogItem[];
  onOpenDraft: (result: RunResult, task: RunTask, assignment: StoreAssignment) => void | Promise<void>;
  onStart: (task: RunTask, assignment: StoreAssignment, immediately?: boolean) => void | Promise<void>;
}) {
  const { session, scopeKey } = useWorkspace();
  const roles = session.workspace!.roles;
  const canManage = roles.some((role) =>
    ["admin", "operator", "regional_manager"].includes(role),
  );
  const canCreate = roles.some((role) => ["admin", "operator"].includes(role));
  const canReview = roles.some((role) =>
    [
      "admin",
      "operator",
      "regional_manager",
      "store_manager",
      "reviewer",
    ].includes(role),
  );
  const [tab, setTab] = useState<
    "assignments" | "campaigns" | "reviews" | "partners"
  >("assignments");
  const [campaigns, setCampaigns] = useState<ChannelCampaign[]>([]);
  const [assignments, setAssignments] = useState<StoreAssignment[]>([]);
  const [submissions, setSubmissions] = useState<ContentSubmission[]>([]);
  const [profiles, setProfiles] = useState<StoreProfile[]>([]);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<ChannelCampaign | null>(null);
  const [progress, setProgress] = useState<CampaignProgress[]>([]);
  const [grants, setGrants] = useState<PartnerGrant[]>([]);
  const [grantCode, setGrantCode] = useState("");
  const [partnerStores, setPartnerStores] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [granting, setGranting] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showPast, setShowPast] = useState(false);
  const [sharedOpportunities, setSharedOpportunities] = useState<Record<string, StoreOpportunity>>({});
  const actionRunning = useRef(false);
  const [, updateDrafts] = useState(0);
  useEffect(() => subscribeDrafts(() => updateDrafts((value) => value + 1)), []);
  function draft(assignment: StoreAssignment) { return getDraftState(draftKey(scopeKey, assignment)); }
  function requestDraft(assignment: StoreAssignment, refresh = false, resume = false) {
    return prepareAssignmentDraft(scopeKey, assignment, refresh, resume);
  }
  async function openOrPrepare(assignment: StoreAssignment) {
    const state = draft(assignment);
    if (state?.value?.status === "idle") {
      await requestDraft(assignment, true);
      return;
    }
    if (state?.value?.result) {
      await onOpenDraft(state.value.result, state.value.task || assignment.task, assignment);
      return;
    }
    await requestDraft(assignment, state?.value?.status === "failed" || state?.value?.status === "cancelled", !!state?.error && !["failed", "cancelled"].includes(state?.value?.status || ""));
  }
  const preparationRequests = useRef(new Map<string, Promise<PreparedAssignment>>());
  const [preparations, setPreparations] = useState<Record<string, PreparedAssignment>>({});
  const [preparing, setPreparing] = useState<Record<string, boolean>>({});
  const [preparationErrors, setPreparationErrors] = useState<Record<string, string>>({});
  function preparationKey(assignment: StoreAssignment) {
    return `${scopeKey}:${assignment.id}:${assignment.campaign_version}`;
  }
  function prepare(assignment: StoreAssignment, refresh = false): Promise<PreparedAssignment> {
    const key = preparationKey(assignment);
    const existing = preparationRequests.current.get(key);
    if (existing) return existing;
    if (!refresh) {
      if (preparations[key]) return Promise.resolve(preparations[key]);
      try {
        const saved = JSON.parse(sessionStorage.getItem(`matequill-prepared-v3:${key}`) || "null");
        if (saved?.value?.status === "ready" && saved.value.task && saved.value.opportunity?.title &&
          Date.now() - saved.at < 30 * 60 * 1000) {
          setPreparations((previous) => ({ ...previous, [key]: saved.value }));
          return Promise.resolve(saved.value);
        }
      } catch { /* A preparation can always be requested again. */ }
    } else {
      setPreparations((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      try { sessionStorage.removeItem(`matequill-prepared-v3:${key}`); } catch { /* Memory state remains authoritative. */ }
    }
    setPreparing((previous) => ({ ...previous, [key]: true }));
    setPreparationErrors((previous) => ({ ...previous, [key]: "" }));
    const request = workspaceRequest<PrepareAssignmentResponse>(
      `${PATH}/assignments/${assignment.id}/prepare`,
      refresh ? { refresh: true, instruction: "请换一个不同的创作角度，保持同一门店和活动事实。" } : {},
    ).then((value) => {
      if (value.status !== "ready")
        throw new Error(value.message || value.reasons?.join("；") || "这次选题还没有准备完成，请再试一次。");
      if (!value.task || !value.opportunity?.title)
        throw new Error("这次选题还没有准备完成，请再试一次。");
      setPreparations((previous) => ({ ...previous, [key]: value }));
      try {
        sessionStorage.setItem(`matequill-prepared-v3:${key}`, JSON.stringify({ at: Date.now(), value }));
      } catch { /* The current recommendation remains available in memory. */ }
      return value;
    }).catch((reason: unknown) => {
      setPreparationErrors((previous) => ({
        ...previous, [key]: reason instanceof Error ? reason.message : "选题暂时没有准备好，可以再试一次。",
      }));
      throw reason;
    }).finally(() => {
      preparationRequests.current.delete(key);
      setPreparing((previous) => ({ ...previous, [key]: false }));
    });
    preparationRequests.current.set(key, request);
    return request;
  }
  async function load() {
    try {
      const [a, s, locations] = await Promise.all([
        workspaceRequest<{ assignments: StoreAssignment[] }>(
          `${PATH}/assignments`,
        ),
        workspaceRequest<{ submissions: ContentSubmission[] }>(
          `${PATH}/submissions`,
        ),
        stores(),
      ]);
      setAssignments(a.assignments);
      setSubmissions(s.submissions);
      setProfiles(locations.stores);
      if (canManage) {
        const result = await workspaceRequest<{
          campaigns: ChannelCampaign[];
          progress: CampaignProgress[];
        }>(`${PATH}/campaigns`);
        setCampaigns(result.campaigns);
        setProgress(result.progress);
      }
      if (canCreate)
        setGrants(
          (await workspaceRequest<{ grants: PartnerGrant[] }>(`${PATH}/grants`))
            .grants,
        );
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "活动暂未载入，请再试一次。",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function action(
    key: string,
    operation: () => Promise<unknown>,
    success = "",
  ) {
    if (actionRunning.current) return;
    actionRunning.current = true;
    setBusy(key);
    setError("");
    setNotice("");
    try {
      await operation();
      await load();
      setNotice(success);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "操作未完成，请重试。",
      );
    } finally {
      actionRunning.current = false;
      setBusy("");
    }
  }
  async function createCampaign(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await action(
      "create",
      async () => {
        if (data.getAll("ecosystem_products").length > 4)
          throw new Error("这次活动请最多选择 4 款生态设备。");
        await workspaceRequest(
          editing ? `${PATH}/campaigns/${editing.id}` : `${PATH}/campaigns`,
          {
            title: String(data.get("title")),
            brief: String(data.get("brief")),
            product: String(data.get("product")),
            platform: String(data.get("platform")),
            store_ids: selected,
            ecosystem_products: data.getAll("ecosystem_products").map(String),
            required_copy: String(data.get("required_copy") || ""),
            ends_at: data.get("ends_at")
              ? new Date(String(data.get("ends_at"))).toISOString()
              : null,
          },
          editing ? "PUT" : "POST",
        );
        setAdding(false);
        setEditing(null);
        setSelected([]);
      },
      "活动草稿已保存，可以确认后下发门店。",
    );
  }
  async function start(assignment: StoreAssignment, immediately = true) {
    await action(assignment.id, async () => {
      const prepared = await prepare(assignment);
      if (["assigned", "pending"].includes(assignment.status))
        await workspaceRequest(
          `${PATH}/assignments/${assignment.id}/accept`,
          {},
        );
      await onStart(prepared.task, assignment, immediately);
    });
  }
  const waiting = submissions.filter(
    (item) => item.status === "submitted",
  ).length;
  const recommended = assignments.find(
    (item) => item.available !== false &&
      ["assigned", "pending", "accepted", "changes_requested"].includes(item.status),
  );
  const orderedAssignments = recommended
    ? [recommended, ...assignments.filter((item) => item.id !== recommended.id)]
    : assignments;
  const pastAssignmentCount = orderedAssignments.filter((item) => item.available === false || item.status === "published").length;
  const visibleAssignments = orderedAssignments.filter((item) => showPast || (item.available !== false && item.status !== "published"));
  const assignmentScope = assignments.map((item) => `${item.id}:${item.campaign_version}`).join("|");
  useEffect(() => {
    let live = true;
    setSharedOpportunities({});
    if (!session.workspace?.store_id) return;
    const currentAssignments = assignments.filter((item) => item.store_id === session.workspace?.store_id && item.available !== false);
    void Promise.all(currentAssignments.map(async (item) => {
      try {
        const shared = await workspaceRequest<StoreOpportunity>(`${PATH}/assignments/${item.id}/opportunity`);
        if (live) setSharedOpportunities((previous) => ({ ...previous, [item.id]: shared }));
        // A published regional proposal is adopted explicitly; it must not race
        // an unrelated automatic draft when the store first opens this page.
        if (live && item.id === recommended?.id && !shared.opportunity) void requestDraft(item);
      } catch { /* Leave the existing explicit preparation action available. */ }
    }));
    return () => { live = false; };
  }, [assignmentScope, scopeKey]);
  async function adoptShared(assignment: StoreAssignment) {
    await workspaceRequest(`${PATH}/assignments/${assignment.id}/opportunity/adopt`, {});
    const shared = await workspaceRequest<StoreOpportunity>(`${PATH}/assignments/${assignment.id}/opportunity`);
    setSharedOpportunities((previous) => ({ ...previous, [assignment.id]: shared }));
    await requestDraft(assignment, false, true);
    const result = draft(assignment)?.value?.result;
    if (result) await onOpenDraft(result, draft(assignment)?.value?.task || assignment.task, assignment);
  }
  return (
    <section className={`mq-channel mq-enter ${styles.workspace}`}>
      <header className={`mq-page-heading ${styles.heading}`}>
        <h1>团队活动</h1>
      </header>
      <nav className={`mq-channel-tabs ${styles.tabs}`} aria-label="活动工作台">
        <button
          aria-current={tab === "assignments" ? "page" : undefined}
          onClick={() => setTab("assignments")}
        >
          门店任务
        </button>
        {canManage && (
          <button
            aria-current={tab === "campaigns" ? "page" : undefined}
            onClick={() => setTab("campaigns")}
          >
            总部活动
          </button>
        )}
        <button
          aria-current={tab === "reviews" ? "page" : undefined}
          onClick={() => setTab("reviews")}
        >
          {canReview ? "内容审核" : "我的送审"}
          {waiting > 0 && <span>{waiting}</span>}
        </button>
        {canCreate && (
          <button
            aria-current={tab === "partners" ? "page" : undefined}
            onClick={() => setTab("partners")}
          >
            合作活动
          </button>
        )}
        <button
          className="mq-channel-refresh"
          onClick={() => void load()}
          disabled={!!busy}
        >
          刷新
        </button>
      </nav>
      {error && (
        <p role="alert" className="mq-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mq-inline-notice">
          {notice}
        </p>
      )}
      {loading ? (
        <p role="status">正在取回团队的活动。</p>
      ) : (
        <>
          {tab === "assignments" && (
            <div className={`mq-assignment-list ${styles.assignments}`}>
              {visibleAssignments.length ? (
                visibleAssignments.map((assignment) => (
                  <article key={assignment.id} className={assignment.id === recommended?.id ? "mq-assignment-featured" : undefined}>
                    <div>
                      <div className={styles.metadata}>
                        {(!session.workspace?.store_id || assignment.store_id !== session.workspace.store_id) && <span>{assignment.store_name}</span>}
                        {assignment.publisher_label && <span>
                          {assignment.publisher_label}
                          {assignment.is_simulation && !assignment.publisher_label.includes("模拟") ? "（模拟）" : ""}
                        </span>}
                        <span className={styles.status}>
                          {assignment.available === false
                            ? assignment.unavailable_reason || "活动已结束"
                            : CHANNEL_STATUS[assignment.status] || assignment.status}
                        </span>
                      </div>
                      <h2>{draft(assignment)?.value?.result?.title || draft(assignment)?.value?.opportunity?.title || preparations[preparationKey(assignment)]?.opportunity.title || assignment.title}</h2>
                      <p className="mq-assignment-product">{assignment.task?.product}</p>
                      {draft(assignment)?.loading ? (
                        <p className="mq-preparation-thinking" role="status" aria-live="polite">
                          {["queued", "running"].includes(draft(assignment)?.value?.status || "")
                            ? "正在写正文、安排配图…"
                            : "正在为本店准备选题…"}
                        </p>
                      ) : draft(assignment)?.error ? (
                        <div className="mq-preparation-error" role="alert"><p>{draft(assignment)?.error}</p></div>
                      ) : null}
                      {draft(assignment)?.value?.opportunity && <OpportunityNote opportunity={draft(assignment)!.value!.opportunity!} />}
                      {sharedOpportunities[assignment.id]?.opportunity && <OpportunityPlan
                        plan={sharedOpportunities[assignment.id].opportunity!}
                        disabled={!!busy || !!draft(assignment)?.loading || assignment.available === false || assignment.status === "published"}
                        primaryAction={{ label: draft(assignment)?.value?.result ? "打开本店图文" : draft(assignment)?.loading ? "正在制作本店图文…" : "采用这个机会，制作本店图文", run: () => void action(assignment.id, () => draft(assignment)?.value?.result ? openOrPrepare(assignment) : adoptShared(assignment)) }}
                        onContinue={() => {}}
                        onOpen={() => {}}
                      />}
                      {!!assignment.source_refs?.length && (
                        <details className="mq-assignment-sources">
                          <summary>活动资料 · {assignment.source_refs.length}</summary>
                          <ul>{assignment.source_refs.map((source) => (
                            <li key={source.url}>
                              <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}<span aria-hidden="true"> ↗</span></a>
                              {source.published_at && <time>{source.published_at.slice(0, 10)}</time>}
                            </li>
                          ))}</ul>
                        </details>
                      )}
                    </div>
                    <div className="mq-card-actions">
                      {!sharedOpportunities[assignment.id]?.opportunity && <button
                        className="mq-primary"
                        disabled={!!busy || !!draft(assignment)?.loading || assignment.status === "published" || assignment.available === false}
                        onClick={() => void action(assignment.id, () => openOrPrepare(assignment))}
                      >
                        {draft(assignment)?.loading ? "正在为本店准备…" : busy === assignment.id
                          ? "正在打开…" : assignment.available === false ? "活动已结束"
                          : assignment.status === "published" ? "已确认发布"
                          : draft(assignment)?.value?.result ? "打开这篇"
                          : draft(assignment)?.error ? (["failed", "cancelled"].includes(draft(assignment)?.value?.status || "") ? "重新准备这篇" : "继续查看") : "准备本店图文"}
                        <span>↗</span>
                      </button>}
                      {assignment.available !== false && !["published", "submitted", "approved"].includes(assignment.status) && (
                        <button className="mq-text-button" disabled={!!busy || !!preparing[preparationKey(assignment)]} onClick={() => void start(assignment, false)}>
                          {preparing[preparationKey(assignment)] ? "正在打开创作…" : "添张照片，或说一句"}
                        </button>
                      )}
                      {draft(assignment)?.value?.result && assignment.available !== false && (
                        <button className="mq-text-button" disabled={!!busy || !!draft(assignment)?.loading} onClick={() => void requestDraft(assignment, true)}>
                          换个角度 ↻
                        </button>
                      )}
                    </div>
                  </article>
                ))
              ) : (
                <div className="mq-channel-empty">
                  <Character />
                  <h2>{pastAssignmentCount ? "当前没有待处理活动。" : "还没有团队活动。"}</h2>
                  <p>
                    {canCreate
                      ? "创建一次活动，把共同任务交给门店。"
                      : "团队下发活动后，会出现在这里。你也可以随时开始自己的创作。"}
                  </p>
                  {canCreate && (
                    <button
                      className="mq-primary"
                      onClick={() => {
                        setTab("campaigns");
                        setAdding(true);
                      }}
                    >
                      创建活动<span>↗</span>
                    </button>
                  )}
                </div>
              )}
              {pastAssignmentCount > 0 && (
                <button className="mq-text-button mq-assignment-history-toggle" onClick={() => setShowPast((value) => !value)} aria-expanded={showPast}>
                  {showPast ? "收起过往活动" : `查看已结束与已发布的活动（${pastAssignmentCount}）`}
                </button>
              )}
            </div>
          )}
          {tab === "campaigns" && canManage && (
            <>
              <div className="mq-section-heading">
                <h2>{editing ? "更新活动版本" : "团队活动"}</h2>
                {canCreate && (
                  <button
                    className="mq-primary"
                    onClick={() => {
                      setAdding(!adding);
                      setEditing(null);
                      setSelected([]);
                    }}
                  >
                    {adding ? "收起" : "新建活动"}
                    <span>＋</span>
                  </button>
                )}
              </div>
              {adding && (
                <form
                  key={editing?.id || "new"}
                  className="mq-workspace-form mq-campaign-create"
                  onSubmit={(event) => void createCampaign(event)}
                >
                  <label>
                    活动主题
                    <input
                      name="title"
                      required
                      maxLength={160}
                      defaultValue={editing?.title || ""}
                    />
                  </label>
                  <label>
                    主推产品
                    <select
                      name="product"
                      required
                      defaultValue={editing?.product || ""}
                    >
                      <option value="">选择主推产品</option>
                      {products.map((product) => (
                        <option key={product.id} value={product.name}>
                          {product.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="mq-field-wide">
                    这次想带给顾客什么
                    <textarea
                      name="brief"
                      required
                      rows={3}
                      maxLength={3000}
                      defaultValue={editing?.brief || ""}
                    />
                  </label>
                  <label>
                    内容平台
                    <select
                      name="platform"
                      defaultValue={editing?.platform || "xiaohongshu"}
                    >
                      <option value="xiaohongshu">小红书</option>
                      <option value="wechat">公众号</option>
                      <option value="moments">朋友圈</option>
                    </select>
                  </label>
                  <label>
                    完成时间
                    <input
                      name="ends_at"
                      type="datetime-local"
                      defaultValue={
                        editing?.ends_at
                          ? new Date(
                              new Date(editing.ends_at).getTime() -
                                new Date().getTimezoneOffset() * 60000,
                            )
                              .toISOString()
                              .slice(0, 16)
                          : ""
                      }
                    />
                  </label>
                  <details className="mq-field-wide mq-campaign-extras">
                    <summary>补充生态设备与品牌要求</summary>
                    <label>
                      一起讲的设备（最多 4 款）
                      <select
                        name="ecosystem_products"
                        multiple
                        defaultValue={editing?.ecosystem_products || []}
                      >
                        {products.map((product) => (
                          <option key={product.id} value={product.name}>
                            {product.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      这次需要保留的内容
                      <textarea
                        name="required_copy"
                        rows={2}
                        maxLength={2000}
                        defaultValue={editing?.required_copy || ""}
                      />
                    </label>
                  </details>
                  <fieldset className="mq-field-wide">
                    <legend>参与门店</legend>
                    <div className="mq-store-choices">
                      {profiles.map((profile) => (
                        <label key={profile.id}>
                          <input
                            type="checkbox"
                            checked={selected.includes(profile.id)}
                            onChange={(event) =>
                              setSelected(
                                event.target.checked
                                  ? [...selected, profile.id]
                                  : selected.filter((id) => id !== profile.id),
                              )
                            }
                          />
                          <span>{profile.name}</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <button
                    className="mq-primary"
                    type="submit"
                    disabled={!!busy || !selected.length}
                  >
                    {busy === "create"
                      ? "正在保存…"
                      : editing
                        ? "保存新版本"
                        : "保存活动草稿"}
                    <span>↗</span>
                  </button>
                </form>
              )}
              <div className="mq-campaign-cards">
                {campaigns.map((campaign) => (
                  <article key={campaign.id}>
                    <div className="mq-section-heading">
                      <span className="mq-content-status">
                        {CHANNEL_STATUS[campaign.status] || campaign.status}
                      </span>
                      <span>版本 {campaign.version}</span>
                    </div>
                    <h2>{campaign.title}</h2>
                    <p>{campaign.brief}</p>
                    <dl>
                      <div>
                        <dt>产品</dt>
                        <dd>{campaign.product}</dd>
                      </div>
                      <div>
                        <dt>参与任务</dt>
                        <dd>{campaign.assignments_count} 份</dd>
                      </div>
                      {campaign.ends_at && (
                        <div>
                          <dt>完成时间</dt>
                          <dd>
                            {new Date(campaign.ends_at).toLocaleString("zh-CN")}
                          </dd>
                        </div>
                      )}
                    </dl>
                    {progress.find((item) => item.campaign_id === campaign.id)
                      ?.stores ? (
                      <p className="mq-campaign-progress">
                        {Object.entries(
                          progress.find(
                            (item) => item.campaign_id === campaign.id,
                          )!.counts,
                        )
                          .filter(([, count]) => count > 0)
                          .map(
                            ([status, count]) =>
                              `${CHANNEL_STATUS[status] || status} ${count}`,
                          )
                          .join(" · ")}
                      </p>
                    ) : null}
                    {campaign.status === "active" && <CampaignOpportunities key={`${campaign.id}:${campaign.version}`} campaignId={campaign.id} />}
                    {canCreate && (
                      <div className="mq-card-actions">
                        {campaign.status === "draft" && (
                          <button
                            className="mq-primary"
                            disabled={!!busy}
                            onClick={() =>
                              void action(
                                campaign.id,
                                () =>
                                  workspaceRequest(
                                    `${PATH}/campaigns/${campaign.id}/publish`,
                                    {},
                                  ),
                                "活动已下发，门店可以领取。",
                              )
                            }
                          >
                            下发门店<span>↗</span>
                          </button>
                        )}
                        {!campaign.source_grant_id && (
                          <button
                            className="mq-text-button"
                            onClick={() => {
                              setEditing(campaign);
                              setSelected(campaign.store_ids || []);
                              setAdding(true);
                              window.scrollTo({ top: 0, behavior: "smooth" });
                            }}
                          >
                            编辑新版本
                          </button>
                        )}
                        {campaign.status === "active" &&
                          !campaign.source_grant_id && (
                            <button
                              className="mq-text-button"
                              onClick={() =>
                                setGranting(
                                  granting === campaign.id ? null : campaign.id,
                                )
                              }
                            >
                              邀请合作企业参与
                            </button>
                          )}
                        {campaign.status === "active" && (
                          <button
                            className="mq-text-button"
                            disabled={!!busy}
                            onClick={() => {
                              if (
                                window.confirm(
                                  "撤回这次活动？门店将停止使用当前任务。",
                                )
                              )
                                void action(
                                  campaign.id,
                                  () =>
                                    workspaceRequest(
                                      `${PATH}/campaigns/${campaign.id}/withdraw`,
                                      {},
                                    ),
                                  "这次活动已撤回。",
                                );
                            }}
                          >
                            撤回活动
                          </button>
                        )}
                      </div>
                    )}
                    {granting === campaign.id && (
                      <form
                        className="mq-partner-form"
                        onSubmit={(event) => {
                          event.preventDefault();
                          const data = new FormData(event.currentTarget);
                          void action(
                            `grant:${campaign.id}`,
                            async () => {
                              const value = await workspaceRequest<{
                                token: string;
                              }>(`${PATH}/campaigns/${campaign.id}/grants`, {
                                recipient_tenant_id: String(
                                  data.get("tenant_id"),
                                ),
                              });
                              setGrantCode(value.token);
                              setGranting(null);
                              setTab("partners");
                            },
                            "请将活动邀请码发给指定合作企业。",
                          );
                        }}
                      >
                        <label>
                          合作企业编号
                          <input name="tenant_id" required />
                        </label>
                        <button className="mq-primary" disabled={!!busy}>
                          生成活动邀请
                        </button>
                      </form>
                    )}
                  </article>
                ))}
              </div>
            </>
          )}
          {tab === "partners" && canCreate && (
            <section className="mq-partner-workspace">
              {grantCode && (
                <div className="mq-invitation-card">
                  <h3>把这次活动，交给合作伙伴。</h3>
                  <p>邀请码只在这次创建后显示，请发送给指定合作企业。</p>
                  <div>
                    <input
                      value={grantCode}
                      readOnly
                      aria-label="活动邀请码"
                      onFocus={(event) => event.currentTarget.select()}
                    />
                    <button
                      className="mq-primary"
                      onClick={() =>
                        void navigator.clipboard
                          .writeText(grantCode)
                          .then(() => setNotice("活动邀请码已复制。"))
                          .catch(() => setNotice("请选中邀请码复制。"))
                      }
                    >
                      复制邀请码
                    </button>
                  </div>
                </div>
              )}
              <form
                className="mq-workspace-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  const data = new FormData(event.currentTarget);
                  void action(
                    "accept-grant",
                    async () => {
                      await workspaceRequest(`${PATH}/grants/accept`, {
                        token: String(data.get("token")),
                        store_ids: partnerStores,
                      });
                      setPartnerStores([]);
                    },
                    "合作活动已接收并下发到所选门店。",
                  );
                }}
              >
                <h2 className="mq-field-wide">接收伙伴的好主题。</h2>
                <label className="mq-field-wide">
                  活动邀请码
                  <input name="token" required minLength={20} />
                </label>
                <fieldset className="mq-field-wide">
                  <legend>交给哪些门店</legend>
                  <div className="mq-store-choices">
                    {profiles.map((profile) => (
                      <label key={profile.id}>
                        <input
                          type="checkbox"
                          checked={partnerStores.includes(profile.id)}
                          onChange={(event) =>
                            setPartnerStores(
                              event.target.checked
                                ? [...partnerStores, profile.id]
                                : partnerStores.filter(
                                    (id) => id !== profile.id,
                                  ),
                            )
                          }
                        />
                        <span>{profile.name}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <button
                  className="mq-primary"
                  disabled={!!busy || !partnerStores.length}
                >
                  接收并下发<span>↗</span>
                </button>
              </form>
              <div className="mq-partner-list">
                {grants.map((grant) => (
                  <article key={grant.id}>
                    <div>
                      <p className="mq-section-label">
                        {grant.sender_tenant_id === session.workspace?.tenant_id
                          ? "发出的活动"
                          : "收到的活动"}
                      </p>
                      <h2>{grant.title}</h2>
                      <p>
                        {grant.revoked_at
                          ? "已撤回授权"
                          : grant.accepted_at
                            ? "伙伴已接收"
                            : "等待接收"}{" "}
                        · 有效至{" "}
                        {new Date(grant.expires_at * 1000).toLocaleDateString(
                          "zh-CN",
                        )}
                      </p>
                    </div>
                    {grant.sender_tenant_id === session.workspace?.tenant_id &&
                      !grant.revoked_at && (
                        <button
                          className="mq-text-button"
                          disabled={!!busy}
                          onClick={() => {
                            if (window.confirm("结束这份合作活动授权？"))
                              void action(
                                grant.id,
                                () =>
                                  workspaceRequest(
                                    `${PATH}/grants/${grant.id}/revoke`,
                                    {},
                                  ),
                                "这份合作授权已结束。",
                              );
                          }}
                        >
                          结束授权
                        </button>
                      )}
                  </article>
                ))}
              </div>
            </section>
          )}
          {tab === "reviews" && (
            <div className="mq-submission-list">
              {submissions.length ? (
                submissions.map((submission) => (
                  <article key={submission.id}>
                    <header>
                      <div>
                        <p className="mq-section-label">
                          {submission.store_name} · 第 {submission.revision} 稿
                        </p>
                        <h2>{submission.title}</h2>
                      </div>
                      <span className="mq-content-status">
                        {CHANNEL_STATUS[submission.status] || submission.status}
                      </span>
                    </header>
                    {submission.review_note && (
                      <blockquote>{submission.review_note}</blockquote>
                    )}
                    <button
                      className="mq-text-button"
                      onClick={() =>
                        setReviewing(
                          reviewing === submission.id ? null : submission.id,
                        )
                      }
                    >
                      {reviewing === submission.id ? "收起内容" : "查看内容"}
                    </button>
                    {submission.note && (
                      <p className="mq-creator-note">
                        创作者的话：{submission.note}
                      </p>
                    )}
                    {reviewing === submission.id && (
                      <div className="mq-review-body">
                        <h3>{submission.content_snapshot?.title}</h3>
                        {submission.content_snapshot?.intro && (
                          <p>{submission.content_snapshot.intro}</p>
                        )}
                        {submission.image_previews?.map((image) => (
                          <figure key={image.asset_id}>
                            <img
                              src={image.url}
                              alt={image.caption || "内容配图"}
                            />
                            {image.caption && (
                              <figcaption>{image.caption}</figcaption>
                            )}
                          </figure>
                        ))}
                        {submission.content_snapshot?.blocks?.map(
                          (block, index) => (
                            <div key={index}>
                              <p>{block.text}</p>
                              {block.disclosures?.length ? (
                                <p className="mq-review-conditions">
                                  {block.disclosures.join("；")}
                                </p>
                              ) : null}
                            </div>
                          ),
                        )}
                        {submission.content_snapshot?.cta && (
                          <p>{submission.content_snapshot.cta}</p>
                        )}
                      </div>
                    )}
                    {canReview &&
                      submission.status === "submitted" &&
                      (submission.submitted_by !== session.user?.id ||
                        roles.includes("admin")) && (
                        <div className="mq-review-actions">
                          <label>
                            给创作者的建议
                            <textarea
                              rows={2}
                              value={notes[submission.id] || ""}
                              onChange={(event) =>
                                setNotes({
                                  ...notes,
                                  [submission.id]: event.target.value,
                                })
                              }
                            />
                          </label>
                          <div>
                            <button
                              className="mq-primary"
                              disabled={!!busy}
                              onClick={() =>
                                void action(
                                  submission.id,
                                  () =>
                                    workspaceRequest(
                                      `${PATH}/submissions/${submission.id}/review`,
                                      {
                                        decision: "approved",
                                        note: notes[submission.id] || "",
                                      },
                                    ),
                                  "这一稿已通过审核。",
                                )
                              }
                            >
                              通过审核
                            </button>
                            <button
                              className="mq-text-button"
                              disabled={!!busy || !notes[submission.id]?.trim()}
                              onClick={() =>
                                void action(
                                  submission.id,
                                  () =>
                                    workspaceRequest(
                                      `${PATH}/submissions/${submission.id}/review`,
                                      {
                                        decision: "changes_requested",
                                        note: notes[submission.id],
                                      },
                                    ),
                                  "修改建议已送达创作者。",
                                )
                              }
                            >
                              请修改后再看
                            </button>
                          </div>
                        </div>
                      )}
                    {submission.status === "approved" &&
                      (submission.submitted_by === session.user?.id ||
                        roles.some((role) =>
                          [
                            "admin",
                            "operator",
                            "regional_manager",
                            "store_manager",
                          ].includes(role),
                        )) && (
                        <form
                          className="mq-publication-form"
                          onSubmit={(event) => {
                            event.preventDefault();
                            const data = new FormData(event.currentTarget);
                            void action(
                              submission.id,
                              () =>
                                workspaceRequest(
                                  `${PATH}/submissions/${submission.id}/publish`,
                                  {
                                    url: String(data.get("url")),
                                    note: String(data.get("note")),
                                  },
                                ),
                              "已记录这次人工发布确认。",
                            );
                          }}
                        >
                          <h3>这篇内容发出去了吗？</h3>
                          <label>
                            发布链接
                            <input name="url" type="url" />
                          </label>
                          <label>
                            发布说明
                            <input name="note" />
                          </label>
                          <button className="mq-primary" disabled={!!busy}>
                            确认已发布
                          </button>
                        </form>
                      )}
                    {submission.publication && (
                      <div className="mq-publication-record">
                        <h3>人工确认的发布记录</h3>
                        <p>{submission.publication.note}</p>
                        {submission.publication.url && (
                          <a
                            href={submission.publication.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            查看发布链接 ↗
                          </a>
                        )}
                        <p>
                          {new Date(
                            submission.publication.created_at,
                          ).toLocaleString("zh-CN")}
                        </p>
                      </div>
                    )}
                  </article>
                ))
              ) : (
                <div className="mq-channel-empty">
                  <Character />
                  <h2>每一篇好内容，都会在这里留下记录。</h2>
                  <p>创作完成后送审，即可和团队一起打磨。</p>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
