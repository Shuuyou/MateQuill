"use client";
import { useEffect, useState } from "react";
import { planCampaign, saveStore, stores } from "@/lib/api";
import type {
  CampaignPlan,
  CatalogItem,
  RunTask,
  StoreProfile,
} from "@/lib/types";
import Character from "./Character";

export default function StoreCampaign({
  products,
  onGenerate,
}: {
  products: CatalogItem[];
  onGenerate: (task: RunTask) => void;
}) {
  const [profiles, setProfiles] = useState<StoreProfile[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [product, setProduct] = useState("");
  const [brief, setBrief] = useState("");
  const [platform, setPlatform] = useState("xiaohongshu");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<CampaignPlan | null>(null);
  const [restored, setRestored] = useState(false);
  useEffect(() => {
    stores()
      .then((data) => setProfiles(data.stores))
      .catch((reason) => setError(reason.message));
    try {
      const saved = JSON.parse(
        sessionStorage.getItem("matequill-campaign-v1") || "null",
      );
      if (saved) {
        setProduct(saved.product || "");
        setBrief(saved.brief || "");
        setPlatform(saved.platform || "xiaohongshu");
        setSelected(Array.isArray(saved.selected) ? saved.selected : []);
        setPlan(saved.plan || null);
      }
    } catch {
      /* Start with an empty brief when the previous session is unavailable. */
    }
    setRestored(true);
  }, []);
  useEffect(() => {
    if (restored) {
      try {
        sessionStorage.setItem(
          "matequill-campaign-v1",
          JSON.stringify({ product, brief, platform, selected, plan }),
        );
      } catch {
        /* Keep the active plan in component state. */
      }
    }
  }, [restored, product, brief, platform, selected, plan]);
  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const response = await saveStore({
        name: String(form.get("name")),
        audience: String(form.get("audience")),
        location: String(form.get("location")),
        voice: String(form.get("voice")),
        core_tasks: String(form.get("tasks"))
          .split(/[，,、\n]/)
          .map((value) => value.trim())
          .filter(Boolean),
      });
      setProfiles((previous) => [...previous, response]);
      setSelected((previous) => [...previous, response.id]);
      setAdding(false);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "门店没有保存成功，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function createPlan(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await planCampaign({
        brief,
        product,
        platform,
        store_ids: selected,
      });
      setPlan(response);
      if (!response.variants.length)
        setError(
          response.questions?.join("；") || "请补充这次主推任务，再试一次。",
        );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "这次策划没有完成，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="mq-campaign mq-enter">
      <header className="mq-page-heading">
        <p className="mq-section-label">同一款新品，每家店有自己的说法</p>
        <h1>千店，千种好内容。</h1>
        <p>用真实门店画像，找到各自值得讲的那一个角度。</p>
      </header>
      <div className="mq-campaign-layout">
        <div>
          <section className="mq-store-section">
            <div className="mq-section-heading">
              <h2>这次由哪些门店来讲？</h2>
              <button
                className="mq-text-button"
                onClick={() => setAdding((value) => !value)}
              >
                {adding ? "收起" : "+ 添加门店"}
              </button>
            </div>
            {!profiles.length && !adding && (
              <div className="mq-empty-store">
                <p>先记下门店常来的顾客和熟悉的表达方式。</p>
                <button className="mq-primary" onClick={() => setAdding(true)}>
                  建立第一家门店
                </button>
              </div>
            )}
            <div className="mq-store-list">
              {profiles.map((profile) => (
                <label
                  key={profile.id}
                  className={selected.includes(profile.id) ? "is-selected" : ""}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(profile.id)}
                    onChange={(event) =>
                      setSelected((previous) =>
                        event.target.checked
                          ? [...previous, profile.id]
                          : previous.filter((id) => id !== profile.id),
                      )
                    }
                  />
                  <span>
                    <strong>{profile.name}</strong>
                    <span>{profile.audience}</span>
                    {profile.location && <span>{profile.location}</span>}
                  </span>
                </label>
              ))}
            </div>
            {adding && (
              <form
                className="mq-store-form"
                onSubmit={(event) => void add(event)}
              >
                <label>
                  门店名称
                  <input name="name" required maxLength={120} />
                </label>
                <label>
                  常来的顾客
                  <input name="audience" required maxLength={500} />
                </label>
                <label>
                  门店位置
                  <input name="location" maxLength={200} />
                </label>
                <label>
                  常聊的使用需求
                  <input name="tasks" maxLength={500} />
                </label>
                <label>
                  我们说话的风格
                  <input name="voice" maxLength={300} />
                </label>
                <button className="mq-primary" disabled={busy} type="submit">
                  保存门店
                </button>
              </form>
            )}
          </section>
          <form
            className="mq-campaign-brief"
            onSubmit={(event) => void createPlan(event)}
          >
            <h2>给这些门店一个共同任务。</h2>
            <label>
              主推产品
              <select
                value={product}
                onChange={(event) => setProduct(event.target.value)}
                required
              >
                <option value="">选择主推产品</option>
                {products.map((item) => (
                  <option key={item.id} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              这次想讲什么
              <textarea
                value={brief}
                onChange={(event) => setBrief(event.target.value)}
                rows={3}
                required
              />
            </label>
            <label>
              发布平台
              <select
                value={platform}
                onChange={(event) => setPlatform(event.target.value)}
              >
                <option value="xiaohongshu">小红书</option>
                <option value="wechat">公众号</option>
                <option value="moments">朋友圈</option>
              </select>
            </label>
            <button
              type="submit"
              className="mq-primary"
              disabled={busy || !product || !brief.trim() || !selected.length}
            >
              {busy ? "正在为门店找角度…" : "看看各家店怎么讲"}
              <span>↗</span>
            </button>
          </form>
        </div>
        <aside className="mq-campaign-aside">
          <Character state={busy ? "thinking" : "idle"} />
          <h2>差异，从顾客开始。</h2>
          <p>
            同一个产品，可以是新生的开学伙伴，也可以是商务人的差旅搭档。门店的位置、顾客的任务和店员的语气，会一起进入这次策划。
          </p>
        </aside>
      </div>
      {error && (
        <p className="mq-error" role="alert">
          {error}
        </p>
      )}
      {plan?.variants.length ? (
        <section className="mq-campaign-results" aria-label="门店内容方案">
          <header className="mq-section-heading">
            <h2>各有各的好角度。</h2>
            <span>{plan.variants.length} 份门店方案</span>
          </header>
          <div className="mq-variant-grid">
            {plan.variants.map((variant, index) => (
              <article key={variant.id}>
                <span className="mq-variant-index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <p className="mq-section-label">{variant.store_name}</p>
                <h3>{variant.angle}</h3>
                <p>{variant.audience}</p>
                <dl>
                  <div>
                    <dt>怎么讲</dt>
                    <dd>{variant.narrative_device}</dd>
                  </div>
                  <div>
                    <dt>怎么拍</dt>
                    <dd>{variant.visual_direction}</dd>
                  </div>
                  <div>
                    <dt>为什么适合</dt>
                    <dd>{variant.why}</dd>
                  </div>
                </dl>
                <button
                  className="mq-primary"
                  onClick={() => onGenerate({ ...variant.task, mode: "local" })}
                >
                  生成这家店的内容<span>↗</span>
                </button>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}
