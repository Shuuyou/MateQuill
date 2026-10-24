"use client";

import type { AgentArtifact, MarketingOpportunity } from "@/lib/agent";

function safeSource(url?: string) {
  if (!url) return undefined;
  try {
    const value = new URL(url);
    return ["https:", "http:"].includes(value.protocol) ? value.href : undefined;
  } catch { return undefined; }
}

export default function OpportunityPlan({ plan, disabled, onContinue, onOpen, primaryAction }: {
  plan: MarketingOpportunity;
  disabled: boolean;
  onContinue: (instruction: string) => void;
  onOpen: (artifact: AgentArtifact) => void;
  primaryAction?: { label: string; run: () => void };
}) {
  const latest = plan.artifacts?.at(-1);
  const design = plan.artifacts?.findLast((artifact) => artifact.kind === "design");
  const run = plan.artifacts?.findLast((artifact) => artifact.kind === "run");
  return <section className="mq-opportunity" aria-label="这家店的营销方案">
    <header>
      <p>从这家店出发</p>
      <h2>{plan.title}</h2>
      {plan.customer_task && <div className="mq-opportunity-rationale">{plan.customer_task}</div>}
    </header>
    <details className="mq-opportunity-preparation">
      <summary>看看怎么拍、怎么体验 <span aria-hidden="true">＋</span></summary>
      {plan.capture_steps?.length > 0 && <div>
        <h3>随手拍下这几个瞬间</h3>
        {plan.visual_direction && <p>{plan.visual_direction}</p>}
        <ol>{plan.capture_steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>
      </div>}
      {plan.experience_steps?.length > 0 && <div>
        <h3>带顾客试一试</h3>
        <ol className="mq-opportunity-experience">{plan.experience_steps.map((step, index) => <li key={`${index}-${step.action}`}>
          <strong>{step.action}</strong>
          {step.customer_value && <p>{step.customer_value}</p>}
          {step.seconds > 0 && <span>{step.seconds} 秒</span>}
        </li>)}</ol>
      </div>}
      {plan.materials?.length > 0 && <div><h3>带上这些就好</h3><p>{plan.materials.join("、")}</p></div>}
      {plan.capabilities_to_confirm?.length > 0 && <div><h3>按本店实际安排</h3><ul>{plan.capabilities_to_confirm.map((item) => <li key={item}>{item}</li>)}</ul></div>}
      {plan.next_action && <p className="mq-opportunity-next">{plan.next_action}</p>}
    </details>
    {plan.rationale && <details className="mq-opportunity-sources">
      <summary>为什么适合这家店</summary>
      <p>{plan.rationale}</p>
      {plan.audience_hypothesis && <p>{plan.audience_hypothesis}</p>}
    </details>}
    {plan.content_angle && <details className="mq-opportunity-sources">
      <summary>图文思路</summary>
      <p>{plan.content_angle}</p>
    </details>}
    {plan.sources?.length > 0 && <details className="mq-opportunity-sources">
      <summary>这个想法从哪里来</summary>
      <ul>{plan.sources.map((source) => {
        const url = safeSource(source.url);
        return <li key={source.source_id}>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a> : source.title}</li>;
      })}</ul>
    </details>}
    <footer>
      {primaryAction ? <button className="mq-primary" type="button" disabled={disabled} onClick={primaryAction.run}>{primaryAction.label}<span aria-hidden="true">↗</span></button>
        : design && run ? <>
          <button className="mq-primary" type="button" disabled={disabled} onClick={() => onOpen(design)}>看看配图 <span aria-hidden="true">↗</span></button>
          <button className="mq-text-button" type="button" disabled={disabled} onClick={() => onOpen(run)}>读读文案</button>
        </>
        : run ? <button className="mq-primary" type="button" disabled={disabled} onClick={() => onOpen(run)}>打开文案 <span aria-hidden="true">↗</span></button>
        : latest ? <button className="mq-primary" type="button" disabled={disabled} onClick={() => onOpen(latest)}>打开这组图文 <span aria-hidden="true">↗</span></button>
        : <button className="mq-primary" type="button" disabled={disabled} onClick={() => onContinue("就按这个本店营销机会，制作可发布的图文，沿用刚才的选题、产品事实与现场素材。")}>制作这组图文 <span aria-hidden="true">↗</span></button>}
      {!primaryAction && <button className="mq-text-button" type="button" disabled={disabled} onClick={() => onContinue("保留这家店的研究依据，换一个不同的本地营销切角，重新准备图文与体验方案。")}>换个切角</button>}
    </footer>
  </section>;
}
