"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import IntakeComposer from "@/components/IntakeComposer";
import Clarification from "@/components/Clarification";
import ResultWorkbench from "@/components/ResultWorkbench";
import PosterWorkbench from "@/components/PosterWorkbench";
import StoreCampaign from "@/components/StoreCampaign";
import ProductLibrary from "@/components/ProductLibrary";
import Character from "@/components/Character";
import { Icon } from "@/components/Icons";
import { catalog } from "@/lib/api";
import { useCreation } from "@/hooks/useCreation";
import type { CatalogItem, RunTask } from "@/lib/types";

type View = "create" | "history" | "campaign" | "catalog";
const NAV: {
  view: View;
  icon: "write" | "history" | "store" | "book";
  label: string;
}[] = [
  { view: "create", icon: "write", label: "开始创作" },
  { view: "campaign", icon: "store", label: "千店千面" },
  { view: "catalog", icon: "book", label: "产品与生态" },
  { view: "history", icon: "history", label: "创作记录" },
];

export default function Page() {
  const creation = useCreation();
  const [view, setView] = useState<View>("create");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [product, setProduct] = useState("");
  const [expanded, setExpanded] = useState(false);
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
  }, []);
  useEffect(() => {
    const body = document.body;
    body.classList.add("mq-next");
    return () => body.classList.remove("mq-next");
  }, []);
  function generate(task: RunTask) {
    setView("create");
    void creation.direct(task);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function selectProduct(name: string) {
    setProduct(name);
    creation.update({ result: null, poster: null, answers: {} });
    creation.clearQuestions();
    setView("create");
  }
  return (
    <div className={`mq-app ${expanded ? "mq-nav-expanded" : ""}`}>
      <aside className="mq-sidebar" aria-label="工作空间导航">
        <button
          className="mq-brand"
          aria-label={expanded ? "收起导航" : "展开导航"}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <img src="/youju.svg" alt="" />
          <span>
            MateQuill<small>场景有据</small>
          </span>
        </button>
        <nav>
          {NAV.map((item) => (
            <button
              key={item.view}
              className={view === item.view ? "is-selected" : ""}
              aria-current={view === item.view ? "page" : undefined}
              aria-label={item.label}
              title={item.label}
              disabled={busy}
              onClick={() => {
                if (item.view === "create") {
                  creation.reset();
                  setProduct("");
                }
                setView(item.view);
              }}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <Link
          href="/settings"
          className="mq-settings-link"
          aria-label="连接设置"
          title="连接设置"
        >
          <Icon name="settings" />
          <span>连接设置</span>
        </Link>
      </aside>
      <div className="mq-topline">
        <span>
          MateQuill<span className="mq-topline-name">场景有据</span>
        </span>
        <span>门店里的好故事，有据可讲。</span>
      </div>
      <main
        className={`mq-main ${view === "create" && !creation.result && !creation.poster && !creation.questions.length ? "mq-main-intake" : ""}`}
      >
        {view === "create" && (
          <>
            {busy ? (
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
                  creation.update({ query, answers: {}, task: null })
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
            {creation.error && (
              <div className="mq-error" role="alert">
                <p>{creation.error}</p>
                <button
                  className="mq-text-button"
                  onClick={() => {
                    if (creation.task) void creation.direct(creation.task);
                    else void creation.resolve();
                  }}
                >
                  重新尝试
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
          <StoreCampaign products={items} onGenerate={generate} />
        )}
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
              <p className="mq-section-label">好内容，也值得再打开</p>
              <h1>这次的创作记录。</h1>
            </header>
            {creation.history.length ? (
              <ol>
                {creation.history.map((item) => (
                  <li key={item.id}>
                    <button
                      onClick={() => {
                        void creation.reopen(item.id);
                        setView("create");
                      }}
                    >
                      <span>{item.title}</span>
                      <time dateTime={item.at}>
                        {new Date(item.at).toLocaleString("zh-CN", {
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
                  onClick={() => setView("create")}
                >
                  开始创作<span>↗</span>
                </button>
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
