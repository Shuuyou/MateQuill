"use client";
import { useState } from "react";
import type { CatalogItem, MediaIntake } from "@/lib/types";
import Character from "./Character";
import MediaCapture from "./MediaCapture";

export default function IntakeComposer({
  query,
  onQuery,
  media,
  onMedia,
  onRemove,
  onSubmit,
  product,
  onProduct,
  items,
  busy,
}: {
  query: string;
  onQuery: (query: string) => void;
  media: MediaIntake[];
  onMedia: (media: MediaIntake, replaceId?: string) => void;
  onRemove: (id: string) => void;
  onSubmit: (answers: Record<string, string>) => void;
  product: string;
  onProduct: (product: string) => void;
  items: CatalogItem[];
  busy: boolean;
}) {
  const [focused, setFocused] = useState(false);
  const [details, setDetails] = useState(false);
  const [platform, setPlatform] = useState("");
  const [mediaBusy, setMediaBusy] = useState(false);
  return (
    <section className="mq-create mq-enter">
      <header className="mq-welcome">
        <Character
          className="welcome-character"
          state={focused ? "focus" : "idle"}
        />
        <div>
          <h1>
            今天，有什么值得分享<span>？</span>
          </h1>
          <p>拍一张，说一句。把门店里的好故事，讲给对的人。</p>
        </div>
      </header>
      <form
        className="mq-composer"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit({
            ...(product ? { product } : {}),
            ...(platform ? { platform } : {}),
          });
        }}
      >
        {product && (
          <div className="mq-product-context">
            <span>{product}</span>
            <button
              type="button"
              onClick={() => onProduct("")}
              aria-label="取消已选产品"
            >
              ×
            </button>
          </div>
        )}
        <label className="mq-sr-only" htmlFor="scene">
          说说顾客与这次想分享的内容
        </label>
        <textarea
          id="scene"
          value={query}
          maxLength={3000}
          rows={4}
          onChange={(event) => onQuery(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          disabled={busy}
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
        />
        <MediaCapture
          showUpload={details}
          disabled={busy}
          media={media}
          onMedia={onMedia}
          onRemove={onRemove}
          onBusy={setMediaBusy}
          onTranscript={(text) =>
            onQuery([query.trim(), text].filter(Boolean).join("\n"))
          }
        />
        {details && (
          <div className="mq-composer-details">
            <label>
              主推产品
              <select
                value={product}
                onChange={(event) => onProduct(event.target.value)}
              >
                <option value="">从需求中识别</option>
                {items.map((item) => (
                  <option value={item.name} key={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              发布到
              <select
                value={platform}
                onChange={(event) => setPlatform(event.target.value)}
              >
                <option value="">从需求中判断</option>
                <option value="xiaohongshu">小红书</option>
                <option value="wechat">公众号</option>
                <option value="moments">朋友圈</option>
                <option value="poster">海报</option>
              </select>
            </label>
          </div>
        )}
        <footer className="mq-composer-footer">
          <button
            type="button"
            className="mq-text-button"
            onClick={() => setDetails((value) => !value)}
            aria-expanded={details}
          >
            {details ? "收起条件" : "补充条件"}
            <span aria-hidden="true">{details ? " −" : " ＋"}</span>
          </button>
          <button
            type="submit"
            className="mq-primary"
            disabled={busy || mediaBusy || (!query.trim() && !media.length)}
          >
            开始创作<span aria-hidden="true">↗</span>
          </button>
        </footer>
      </form>
      <div className="mq-starter-note">
        <span>从一次真实接待开始</span>
        <p>顾客今天问了什么？有什么功能想演示给大家？</p>
      </div>
    </section>
  );
}
