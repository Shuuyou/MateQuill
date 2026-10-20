"use client";
import { useState } from "react";
import { confirmKnowledge, planEcosystem, previewKnowledge } from "@/lib/api";
import type { KnowledgeDraft } from "@/lib/api";
import type { CatalogItem, RunTask } from "@/lib/types";

const CATEGORY: Record<string, string> = {
  computer: "电脑",
  phone: "手机",
  tablet: "平板",
  accessory: "配件",
};
export default function ProductLibrary({
  items,
  onSelect,
  onGenerate,
  onRefresh,
}: {
  items: CatalogItem[];
  onSelect: (product: string) => void;
  onGenerate: (task: RunTask) => void;
  onRefresh: () => void;
}) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [draft, setDraft] = useState<KnowledgeDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scene, setScene] = useState("");
  const [audience, setAudience] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const visible = items.filter(
    (item) =>
      item.name.toLowerCase().includes(search.toLowerCase()) &&
      (category === "all" || (item.category || "computer") === category),
  );
  async function preview(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      setDraft(
        await previewKnowledge({
          name: String(form.get("name")),
          category: String(form.get("category")),
          source_url: String(form.get("source_url")),
          source_text: String(form.get("source_text")),
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "资料没有读成功，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      await confirmKnowledge(draft.draft_id);
      setDraft(null);
      setAdding(false);
      setConfirmed(false);
      onRefresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "资料没有保存成功，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function ecosystem(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await planEcosystem({
        products: selected,
        scene,
        audience,
        platform: "xiaohongshu",
      });
      if (response.task && response.status === "ready")
        onGenerate({ ...response.task, mode: "local" });
      else
        setError(
          response.questions?.join("；") || "请补充这组设备的具体使用场景。",
        );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "这次生态策划没有完成，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="mq-library mq-enter">
      <header className="mq-page-heading">
        <p className="mq-section-label">从一台设备，到一起工作的华为生态</p>
        <div className="mq-section-heading">
          <h1>好内容，从了解产品开始。</h1>
          <button
            className="mq-text-button"
            onClick={() => setAdding((value) => !value)}
          >
            {adding ? "收起新品资料" : "+ 录入新品"}
          </button>
        </div>
        <p>选择产品开始创作，或组合设备，讲一个真实的跨设备任务。</p>
      </header>
      {adding && (
        <section className="mq-knowledge-editor">
          <h2>{draft ? "核对这次新品资料" : "让刚发布的产品，也有据可讲。"}</h2>
          {draft ? (
            <div>
              <p>{draft.name}</p>
              <div className="mq-fact-preview">
                {draft.facts.map((fact) => (
                  <section key={fact.id}>
                    <h3>{fact.label}</h3>
                    <p>{fact.value}</p>
                    <blockquote>{fact.quote}</blockquote>
                    {fact.qualifiers?.length ? (
                      <p>{fact.qualifiers.join("；")}</p>
                    ) : null}
                  </section>
                ))}
              </div>
              <label className="mq-confirm-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                已核对产品名称、资料原文与适用条件
              </label>
              <button
                className="mq-primary"
                disabled={busy || !confirmed}
                onClick={() => void confirm()}
              >
                {busy ? "正在保存…" : "加入产品资料"}
              </button>
              <button
                className="mq-text-button"
                onClick={() => {
                  setDraft(null);
                  setConfirmed(false);
                }}
              >
                重新填写
              </button>
            </div>
          ) : (
            <form
              onSubmit={(event) => void preview(event)}
              className="mq-store-form"
            >
              <label>
                准确产品名称
                <input name="name" required maxLength={200} />
              </label>
              <label>
                产品类型
                <select name="category">
                  {Object.entries(CATEGORY).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                资料来源链接
                <input name="source_url" type="url" required />
              </label>
              <label>
                产品资料原文
                <textarea name="source_text" rows={5} />
              </label>
              <button className="mq-primary" disabled={busy} type="submit">
                {busy ? "正在整理产品资料…" : "读取并整理资料"}
              </button>
            </form>
          )}
        </section>
      )}
      <div className="mq-library-controls">
        <label>
          <span className="mq-sr-only">搜索产品</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="搜索产品"
            placeholder="搜索产品"
          />
        </label>
        <div className="mq-category-tabs" aria-label="产品类型">
          {[["all", "全部"], ...Object.entries(CATEGORY)].map(
            ([value, label]) => (
              <button
                key={value}
                aria-pressed={category === value}
                onClick={() => setCategory(value)}
              >
                {label}
              </button>
            ),
          )}
        </div>
      </div>
      <div className="mq-products">
        {visible.map((item) => (
          <article key={item.id}>
            <div className="mq-product-mark">
              {CATEGORY[item.category || "computer"] || "产品"}
            </div>
            <h2>{item.name}</h2>
            {item.system && <p>{item.system}</p>}
            <div>
              <button
                className="mq-text-button"
                onClick={() => onSelect(item.name)}
              >
                为它写一篇 ↗
              </button>
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(item.name)}
                  onChange={(event) =>
                    setSelected((previous) =>
                      event.target.checked
                        ? [...previous, item.name]
                        : previous.filter((name) => name !== item.name),
                    )
                  }
                />
                加入生态组合
              </label>
            </div>
          </article>
        ))}
      </div>
      {!visible.length && (
        <p className="mq-empty">
          这里还没有匹配的产品。可以换个名字搜索，或录入新品资料。
        </p>
      )}
      {selected.length > 0 && (
        <form
          className="mq-ecosystem-panel"
          onSubmit={(event) => void ecosystem(event)}
        >
          <div>
            <p className="mq-section-label">一起工作的设备</p>
            <h2>{selected.join(" ＋ ")}</h2>
          </div>
          <label>
            写给谁
            <input
              value={audience}
              onChange={(event) => setAudience(event.target.value)}
              required
            />
          </label>
          <label>
            一起完成什么事
            <textarea
              value={scene}
              onChange={(event) => setScene(event.target.value)}
              rows={2}
              required
            />
          </label>
          <button
            className="mq-primary"
            type="submit"
            disabled={
              busy || selected.length < 2 || !audience.trim() || !scene.trim()
            }
          >
            {busy ? "正在准备生态内容…" : "创作这组设备的故事"}
            <span>↗</span>
          </button>
        </form>
      )}
      {error && (
        <p className="mq-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
