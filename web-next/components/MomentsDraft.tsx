"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { RunResult, RunTask } from "@/lib/types";
import { adoptCreation } from "@/lib/api";
import { sessionScopeKey, workspaceFetch } from "@/lib/workspace";
import styles from "./MomentsDraft.module.css";
import PublicationActions from "./PublicationActions";

// Session-local deduplication; adoption is distinct from publication.
const adoptionAttempts = new Set<string>();

interface Props {
  result: RunResult;
  task: RunTask | null;
  busy: boolean;
  onRevise: (instruction: string) => void;
  onBack: () => void;
}

export function momentsText(result: RunResult): string {
  return [
    result.title,
    result.intro,
    ...(result.blocks || []).map((block) =>
      [block.text, ...(block.disclosures || [])].filter(Boolean).join("\n"),
    ),
    result.cta,
  ].filter(Boolean).join("\n\n");
}

/** Remount only when the document changes, preserving edits during request state updates. */
export default function MomentsDraft(props: Props) {
  const text = momentsText(props.result);
  const identity = JSON.stringify([
    sessionScopeKey(), props.result.run_id, text,
    props.result.images?.map((image) => image.asset_id),
  ]);
  return <Draft key={identity} {...props} initialText={text} />;
}

function Draft({ result, task, busy, onRevise, onBack, initialText }: Props & { initialText: string }) {
  const [text, setText] = useState(initialText);
  const [instruction, setInstruction] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const bodyId = useId();
  const instructionId = useId();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const alive = useRef(true);
  const downloadController = useRef<AbortController | null>(null);
  const publishable = result.status === "ready" || result.status === "draft";
  const assets = (result.images || [])
    .filter((image, index, all) => all.findIndex((item) => item.asset_id === image.asset_id) === index)
    .flatMap((image) => {
      const asset = result.assets?.find((item) => item.id === image.asset_id);
      return asset?.url ? [{ ...asset, caption: image.caption }] : [];
    });

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; downloadController.current?.abort(); };
  }, []);

  useEffect(() => {
    function fitBody() {
      if (textarea.current) {
        textarea.current.style.height = "auto";
        textarea.current.style.height = `${textarea.current.scrollHeight + 4}px`;
      }
    }
    fitBody();
    window.addEventListener("resize", fitBody);
    return () => window.removeEventListener("resize", fitBody);
  }, [text]);

  async function copy() {
    const scope = sessionScopeKey();
    try {
      await navigator.clipboard.writeText(text);
      if (alive.current) setNotice("文字已复制");
      if (alive.current && scope === sessionScopeKey() && text === initialText && result.status === "ready") {
        const key = `${scope}:${result.run_id}`;
        if (!adoptionAttempts.has(key)) {
          adoptionAttempts.add(key);
          // Copy already succeeded; a failed adoption request must not undo it.
          void adoptCreation(result.run_id).catch(() => undefined);
        }
      }
    } catch {
      textarea.current?.focus();
      textarea.current?.select();
      if (alive.current) setNotice("正文已选中，可以直接复制");
    }
  }

  async function saveImage(asset: typeof assets[number], index: number) {
    setSaving(asset.id);
    setNotice("");
    const controller = new AbortController();
    downloadController.current = controller;
    const scope = sessionScopeKey();
    try {
      const url = new URL(asset.url, window.location.origin);
      if (!["http:", "https:"].includes(url.protocol)) throw new Error("image_url");
      const response = url.origin === window.location.origin
        ? await workspaceFetch(`${url.pathname}${url.search}`, { signal: controller.signal })
        : await fetch(url.href, { credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error("image_fetch");
      const blob = await response.blob();
      if (!blob.size || !blob.type.startsWith("image/")) throw new Error("image_content");
      if (!alive.current || scope !== sessionScopeKey()) return;
      const extension = blob.type.includes("png") ? "png" : blob.type.includes("webp") ? "webp" : blob.type.includes("svg") ? "svg" : "jpg";
      const localUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = localUrl;
      anchor.download = `MateQuill-朋友圈-${index + 1}.${extension}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(localUrl), 30_000);
      setNotice(`图片 ${index + 1} 已交给浏览器下载`);
    } catch {
      if (alive.current && !controller.signal.aborted)
        setNotice("这张图暂时没有下载成功，可以打开原图保存。");
    } finally {
      if (alive.current) setSaving(null);
    }
  }

  function revise(value: string) {
    if (!value.trim() || busy || !publishable) return;
    const context = text === initialText ? "" : `请以我刚编辑的正文为基础：\n${text}\n\n`;
    onRevise(`${context}${value.trim()}`);
    setInstruction("");
  }

  return (
    <section className={styles.workbench} aria-label="朋友圈创作">
      <header className={styles.heading}>
        <div><p className={styles.eyebrow}>{task?.product || "这次创作"}</p><h1>{publishable ? "今天这条，写好了。" : "继续完成今天这条。"}</h1></div>
        <button type="button" className={styles.textButton} onClick={onBack} disabled={busy}>修改需求</button>
      </header>

      <div className={styles.layout}>
        <article className={styles.paper} aria-label="朋友圈正文与配图" aria-busy={busy}>
          <div className={styles.bodyHeading}><label htmlFor={bodyId}>朋友圈正文</label><span>直接改成你的口吻</span></div>
          <textarea ref={textarea} id={bodyId} className={styles.body} value={text} onChange={(event) => setText(event.target.value)} readOnly={busy || !publishable} rows={7} spellCheck={false} />
          {assets.length > 0 && <div className={`${styles.images} ${assets.length === 1 ? styles.single : ""}`}>
            {assets.map((asset, index) => <figure key={asset.id}>
              <a href={asset.url} target="_blank" rel="noopener noreferrer" aria-label={`打开图片 ${index + 1} 原图`}><img src={asset.url} alt={asset.caption || asset.title || `朋友圈配图 ${index + 1}`} loading="lazy" /></a>
              <figcaption><span>{assets.length === 1 ? "本次配图" : `图片 ${index + 1}`}</span><button type="button" onClick={() => void saveImage(asset, index)} disabled={saving !== null || !publishable}>{saving === asset.id ? "正在准备…" : "保存图片"}</button></figcaption>
            </figure>)}
          </div>}
          <footer className={styles.actions}>
            <button className={styles.primary} type="button" onClick={() => void copy()} disabled={!publishable || !text.trim() || busy}>复制文字 <span aria-hidden="true">↗</span></button>
            <PublicationActions runId={result.run_id} ready={result.status === "ready"} platform="moments" downloadOnly disabled={busy || text !== initialText} />
            <p role="status" aria-live="polite">{notice}</p>
          </footer>
          {!publishable && <div className={styles.recovery}><p>{result.message || "这次创作还没有完成，保留需求再试一次。"}</p><button type="button" onClick={() => onRevise("重新完成这条朋友圈")} disabled={busy}>重新创作</button></div>}
        </article>

        <aside className={styles.refine} aria-label="继续修改">
          <h2>再改一点。</h2>
          <form onSubmit={(event) => { event.preventDefault(); revise(instruction); }}>
            <label htmlFor={instructionId}>你想怎么改？</label>
            <textarea id={instructionId} value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={3} disabled={busy || !publishable} />
            <div className={styles.refineFooter}>{assets.length > 0 && <span>配图保留</span>}<button type="submit" disabled={busy || !publishable || !instruction.trim()}>{busy ? "正在修改…" : "按这个改"} <span aria-hidden="true">↗</span></button></div>
          </form>
        </aside>
      </div>
    </section>
  );
}
