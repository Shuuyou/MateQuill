"use client";

import { useState } from "react";
import { workspaceRequest } from "@/lib/workspace";
import type { CampaignOpportunities as Opportunities } from "@/lib/channel";
import styles from "./CampaignOpportunities.module.css";

export default function CampaignOpportunities({ campaignId }: { campaignId: string }) {
  const [value, setValue] = useState<Opportunities | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const path = `/api/channel/campaigns/${encodeURIComponent(campaignId)}/opportunities`;
  async function load() {
    if (busy) return;
    setBusy(true); setError("");
    try { setValue(await workspaceRequest<Opportunities>(path)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "暂时没有取回门店方案，请再试一次。"); }
    finally { setBusy(false); }
  }
  const pending = value?.stores.filter((store) => store.available !== false && !store.opportunity).slice(0, 10) || [];
  async function prepare() {
    if (busy || !pending.length) return;
    setBusy(true); setError("");
    try {
      // Keep each HTTP request within the proxy window while the backend works
      // on at most three stores concurrently. The user still starts one action.
      for (let offset = 0; offset < pending.length; offset += 3) {
        setValue(await workspaceRequest<Opportunities>(`${path}/prepare`, {
          store_ids: pending.slice(offset, offset + 3).map((store) => store.store_id),
          refresh: false,
        }));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "这次研究还没有完成，可以再试一次。"); }
    finally { setBusy(false); }
  }
  async function select(assignmentId: string, candidateId: string) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      setValue(await workspaceRequest<Opportunities>(`/api/channel/assignments/${encodeURIComponent(assignmentId)}/opportunity/select`, { candidate_id: candidateId }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "暂时没有切换这个观察角度，请再试一次。"); }
    finally { setBusy(false); }
  }
  return <details className="mq-channel-opportunities" onToggle={(event) => {
    if (event.currentTarget.open && !value && !busy) void load();
  }}>
    <summary>让每家店有自己的讲法</summary>
    {busy && <p role="status">正在研究门店周边，准备各自的选题和体验方案。</p>}
    {value?.stores.map((store) => <article key={store.assignment_id}>
      <h3>{store.store_name}</h3>
      {store.opportunity ? <>
        <h4>{store.opportunity.title}</h4>
        <p>{store.opportunity.content_angle || store.opportunity.rationale}</p>
        {store.diversity?.selection_reason && <p className={styles.selection}>{store.diversity.selection_reason}</p>}
        <details><summary>看看本店的依据与体验安排</summary>
          <p>{store.opportunity.rationale}</p>
          <p>{store.opportunity.audience_hypothesis}</p>
          {store.opportunity.experience_steps?.length > 0 && <ol>{store.opportunity.experience_steps.map((step, index) => <li key={index}>{step.action}</li>)}</ol>}
        </details>
        {store.status === "prepared" && (store.alternatives?.length || 0) > 1 && <details className={styles.alternatives}>
          <summary>看看其他观察角度</summary>
          <div>{store.alternatives?.filter((candidate) => !candidate.selected).map((candidate) => <section key={candidate.candidate_id}>
            <h4>{candidate.title}</h4>
            <p>{candidate.customer_task}</p>
            <button className="mq-text-button" type="button" disabled={busy} onClick={() => void select(store.assignment_id, candidate.candidate_id)}>采用这个切角 <span aria-hidden="true">↗</span></button>
          </section>)}</div>
        </details>}
        {store.status === "adopted" && <p>门店已采用，图文制作已启动。</p>}
      </> : <p>{store.available === false ? "活动已结束" : "可以为这家店自主研究一个选题。"}</p>}
    </article>)}
    {error && <p role="alert">{error}</p>}
    {value && pending.length > 0 && <button className="mq-primary" type="button" disabled={busy} onClick={() => void prepare()}>
      {busy ? "正在准备…" : `为这 ${pending.length} 家店准备机会`}<span aria-hidden="true">↗</span>
    </button>}
    {!value && error && <button className="mq-text-button" type="button" onClick={() => void load()}>重新查看</button>}
  </details>;
}
