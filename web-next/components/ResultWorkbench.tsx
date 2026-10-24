"use client";
import { useState } from "react";
import type { RunResult, RunTask } from "@/lib/types";
import Character from "./Character";
import MomentsDraft from "./MomentsDraft";
import PublicationActions from "./PublicationActions";
import { DesignLauncher } from "./DesignWorkspace";

const LABELS: Record<string, string> = {
  audience: "这次写给谁",
  angle: "内容切角",
  hook: "开篇思路",
  scene: "使用场景",
  tone: "表达语气",
  rationale: "这样写的理由",
  platform: "发布平台",
  target_audience: "目标顾客",
  key_message: "核心信息",
  emotional_design: "情感设计",
  scene_slice: "场景切片",
  strategy: "内容策略",
  insight: "顾客洞察",
  content_goal: "沟通目标",
  narrative_device: "叙事方式",
  jobs: "顾客想完成的事",
  structure: "阅读顺序",
  goal: "这次沟通的目标",
  cta: "邀请顾客的下一步",
};
const STRATEGY_FIELDS = new Set([
  "audience",
  "target_audience",
  "scene",
  "scene_slice",
  "goal",
  "content_goal",
  "angle",
  "jobs",
  "tone",
  "structure",
  "narrative_device",
  "emotional_design",
  "key_message",
  "hook",
  "rationale",
  "cta",
]);
function display(value: unknown): string {
  return typeof value === "string"
    ? value
    : Array.isArray(value)
      ? value.map(display).join("；")
      : value && typeof value === "object"
        ? Object.entries(value)
            .map(([key, item]) => `${LABELS[key] || key}：${display(item)}`)
            .join("\n")
        : String(value ?? "");
}
export function documentText(result: RunResult) {
  return [
    result.title,
    result.intro,
    ...(result.blocks || []).map(
      (block) =>
        block.text +
        (block.disclosures?.length ? `\n${block.disclosures.join("；")}` : ""),
    ),
    result.cta,
    result.tags?.map((tag) => `#${tag.replace(/^#/, "")}`).join(" "),
  ]
    .filter(Boolean)
    .join("\n\n");
}

export default function ResultWorkbench({
  result,
  task,
  busy,
  onRevise,
  onBack,
}: {
  result: RunResult;
  task: RunTask | null;
  busy: boolean;
  onRevise: (instruction: string) => void;
  onBack: () => void;
}) {
  const [tab, setTab] = useState("article");
  const [sources, setSources] = useState(false);
  const [revision, setRevision] = useState("");
  const [copy, setCopy] = useState("");
  const cards = result.creative_plan?.cards || [];
  const images = (result.images || [])
    .map((placement) => ({
      ...placement,
      asset: result.assets?.find((asset) => asset.id === placement.asset_id),
    }))
    .filter((image) => image.asset);
  const publishable = result.status === "ready" || result.status === "draft";
  if (task?.platform === "moments" && publishable) {
    return <><MomentsDraft result={result} task={task} busy={busy} onRevise={onRevise} onBack={onBack} /><DesignLauncher sourceRunId={result.run_id} platform={task?.platform} /></>;
  }
  async function copyText() {
    try {
      await navigator.clipboard.writeText(documentText(result));
      setCopy("已复制全文");
    } catch {
      setCopy("请选中文字复制");
    }
  }
  return (
    <section className="mq-workbench mq-enter" aria-label="本次创作结果">
      <header className="mq-workbench-heading">
        <div>
          <p className="mq-section-label">
            {task?.product || "这次创作"} · {task?.audience}
          </p>
          <h1>{result.title || "这次内容还需要调整"}</h1>
        </div>
        <div className="mq-workbench-actions">
        <button type="button" className="mq-text-button" onClick={onBack}>
          修改需求
        </button>
        {publishable && <DesignLauncher sourceRunId={result.run_id} platform={task?.platform} />}
        </div>
      </header>
      <div className="mq-document-grid">
        <article className="mq-document">
          <nav className="mq-document-tabs" aria-label="内容视图">
            <button
              aria-pressed={tab === "article"}
              onClick={() => setTab("article")}
            >
              正文
            </button>
            {cards.length > 0 && (
              <button
                aria-pressed={tab === "cards"}
                onClick={() => setTab("cards")}
              >
                图文分镜
              </button>
            )}
            {result.strategy && Object.keys(result.strategy).length > 0 && (
              <button
                aria-pressed={tab === "thinking"}
                onClick={() => setTab("thinking")}
              >
                创作思路
              </button>
            )}
            {task?.platform !== "wechat" && <button
              className="mq-copy"
              disabled={!publishable}
              onClick={() => void copyText()}
            >
              {copy || "复制全文"}
            </button>}
          </nav>
          {tab === "article" && task?.platform === "wechat" && result.status === "ready" && (
            <PublicationActions runId={result.run_id} ready platform="wechat" preview disabled={busy} />
          )}
          {tab === "article" && !(task?.platform === "wechat" && result.status === "ready") && (
            <div className="mq-article">
              {result.intro && <p className="mq-lead">{result.intro}</p>}
              {result.blocks?.map((block, index) => (
                <div key={index}>
                  <p>{block.text}</p>
                  {block.disclosures?.length ? (
                    <p className="mq-caption">{block.disclosures.join("；")}</p>
                  ) : null}
                  {images
                    .filter((image) => image.block_index === index)
                    .map((image) => (
                      <figure key={image.asset_id}>
                        <img
                          src={image.asset!.url}
                          alt={
                            image.caption || image.asset!.title || "产品配图"
                          }
                        />
                        <figcaption>{image.caption}</figcaption>
                      </figure>
                    ))}
                </div>
              ))}
              {images
                .filter(
                  (image) =>
                    image.block_index === null ||
                    image.block_index === undefined,
                )
                .map((image) => (
                  <figure key={image.asset_id}>
                    <img
                      src={image.asset!.url}
                      alt={image.caption || image.asset!.title || "产品配图"}
                    />
                    <figcaption>{image.caption}</figcaption>
                  </figure>
                ))}
              {result.cta && <p className="mq-cta">{result.cta}</p>}
              {result.tags?.length ? (
                <p className="mq-tags">
                  {result.tags
                    .map((tag) => `#${tag.replace(/^#/, "")}`)
                    .join("  ")}
                </p>
              ) : null}
              {!publishable && (
                <div className="mq-result-recovery">
                  <Character state="waiting" />
                  <h2>换个表达，再来一次。</h2>
                  <p>
                    {result.audit
                      ?.filter((item) => item.severity === "error")
                      .map((item) => item.message)
                      .join("；") ||
                      "这次内容没有完成。需求已经保留，可以直接重新生成。"}
                  </p>
                  <button
                    className="mq-primary"
                    disabled={busy}
                    onClick={() => onRevise("重新生成这次内容")}
                  >
                    重新生成
                  </button>
                </div>
              )}
            </div>
          )}
          {tab === "cards" && (
            <div className="mq-storyboard">
              {cards.map((card, index) => {
                const asset = result.assets?.find((item) =>
                  (card.asset_ids || [card.asset_id]).includes(item.id),
                );
                return (
                  <section key={index}>
                    <div className="mq-card-number">
                      {String(index + 1).padStart(2, "0")}
                    </div>
                    <div>
                      {asset && (
                        <img
                          src={asset.url}
                          alt={asset.title || card.headline || "图文配图"}
                        />
                      )}
                      <h2>{card.headline || card.overlay_text}</h2>
                      <p>{card.body || card.caption}</p>
                      {card.visual_direction || card.shot ? (
                        <p className="mq-caption">
                          画面：{card.visual_direction || card.shot}
                        </p>
                      ) : null}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
          {tab === "thinking" && (
            <dl className="mq-thinking">
              {Object.entries(result.strategy || {})
                .filter(([key]) => STRATEGY_FIELDS.has(key))
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{LABELS[key] || key.replace(/_/g, " ")}</dt>
                    <dd>{display(value)}</dd>
                  </div>
                ))}
            </dl>
          )}
          <footer className="mq-document-footer">
            {task?.platform !== "wechat" && <><a
              aria-disabled={!publishable}
              href={
                publishable
                  ? `/api/runs/${result.run_id}/export?format=md`
                  : undefined
              }
            >
              导出文稿 ↗
            </a>
            <a
              aria-disabled={!publishable}
              href={
                publishable
                  ? `/api/runs/${result.run_id}/export?format=html`
                  : undefined
              }
            >
              导出图文网页 ↗
            </a></>}
            <button
              onClick={() => setSources((value) => !value)}
              aria-expanded={sources}
            >
              产品依据
            </button>
          </footer>
          {sources && (
            <aside className="mq-sources">
              <h2>这篇内容的产品依据</h2>
              {result.facts?.map((fact) => (
                <p key={fact.id}>
                  <strong>{fact.label}</strong> {fact.value}
                </p>
              ))}
              {result.sources?.map((source) => (
                <p key={source.id}>
                  {/^https?:\/\//.test(source.url || "") ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {source.title} ↗
                    </a>
                  ) : (
                    source.title
                  )}
                </p>
              ))}
            </aside>
          )}
        </article>
        <aside className="mq-editor-note">
          <Character state={busy ? "thinking" : "done"} />
          <h2>再改一点。</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (revision.trim()) onRevise(revision.trim());
            }}
          >
            <label className="mq-sr-only" htmlFor="revision">
              修改意见
            </label>
            <textarea
              id="revision"
              value={revision}
              onChange={(event) => setRevision(event.target.value)}
              rows={4}
            />
            <button
              type="submit"
              className="mq-primary"
              disabled={busy || !revision.trim()}
            >
              {busy ? "正在修改…" : "按这个改"}
              <span>↗</span>
            </button>
          </form>
        </aside>
      </div>
    </section>
  );
}
