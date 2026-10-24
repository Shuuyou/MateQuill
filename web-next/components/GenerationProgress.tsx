"use client";

import { useEffect, useState } from "react";
import Character from "./Character";

const CONTENT_STEPS: [string, string][] = [
  ["understanding", "理解你的需要"],
  ["retrieval", "整理产品资料与门店信息"],
  ["drafting", "撰写正文与配图建议"],
  ["reviewing", "核对事实与品牌口径"],
  ["repairing", "按审查意见修订"],
  ["finalizing", "收尾与导出准备"],
];
const POSTER_STEPS: [string, string][] = [
  ["copy", "撰写海报文案"],
  ["layout", "设计画面排版"],
  ["background_image", "绘制背景画面"],
  ["visual_review", "检查整体视觉效果"],
];

function formatElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export default function GenerationProgress({
  phase,
  query,
  stage,
  percent,
  detail,
  startedAt,
  variant = "content",
  onCancel,
  cancelling = false,
}: {
  phase: "understanding" | "generating";
  query: string;
  stage: string;
  percent: number;
  detail?: string;
  startedAt: number;
  variant?: "content" | "poster";
  onCancel?: () => void;
  cancelling?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const steps = variant === "poster" ? POSTER_STEPS : CONTENT_STEPS;
  const current = stage ? steps.findIndex(([key]) => key === stage) : -1;
  const width = Math.min(98, Math.max(0, percent));
  return (
    <section className="mq-generating" role="status" aria-live="polite">
      <Character state="thinking" />
      <h1>
        {phase === "understanding"
          ? "让我想想，这次怎么讲。"
          : "正在把这个故事，好好写出来。"}
      </h1>
      <p>{query}</p>
      {current >= 0 ? (
        <ol className="mq-steps">
          {steps.map(([key, label], index) => (
            <li
              key={key}
              className={
                index < current
                  ? "is-done"
                  : index === current
                    ? "is-active"
                    : ""
              }
              aria-current={index === current ? "step" : undefined}
              aria-label={`${label}，${index < current ? "已完成" : index === current ? "正在进行" : "未开始"}`}
            >
              <i aria-hidden="true">
                {index < current ? "✅" : index === current ? "⏳" : "○"}
              </i>
              <span>{label}</span>
              {index === current && detail ? <em>{detail}</em> : null}
            </li>
          ))}
        </ol>
      ) : (
        <span>
          {phase === "understanding"
            ? "理解顾客的需要"
            : "整理产品资料，准备正文与配图"}
        </span>
      )}
      {width > 0 && (
        <div
          className="mq-generating-bar"
          role="progressbar"
          aria-label="创作进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(width)}
        >
          <i style={{ width: `${width}%` }} />
        </div>
      )}
      <span className="mq-generating-elapsed">
        {startedAt > 0 ? `已等待 ${formatElapsed(now - startedAt)} · ` : ""}
        通常 1–3 分钟
      </span>
      {onCancel && (
        <button
          type="button"
          className="mq-text-button mq-generating-stop"
          disabled={cancelling}
          onClick={onCancel}
        >
          {cancelling ? "正在停止…" : "停止这次创作"}
        </button>
      )}
    </section>
  );
}
