"use client";

import { useEffect, useRef, useState } from "react";
import { sessionScopeKey, workspaceFetch } from "@/lib/workspace";
import styles from "./PublicationActions.module.css";

interface Bundle {
  content_hash: string;
  platform: string;
  plain_text: string;
  rich_html: string;
  images: { filename: string; sha256: string; bytes: number; mime: string }[];
}

export default function PublicationActions({ runId, ready, platform, preview = false, downloadOnly = false, disabled = false }: {
  runId: string; ready: boolean; platform: "wechat" | "moments";
  preview?: boolean; downloadOnly?: boolean; disabled?: boolean;
}) {
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [notice, setNotice] = useState("");
  const [working, setWorking] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const identity = `${sessionScopeKey()}:${runId}`;
  const current = useRef(identity);
  current.current = identity;
  const base = `/api/runs/${encodeURIComponent(runId)}/publication`;
  useEffect(() => {
    setBundle(null); setNotice("");
    if (!ready) return;
    const abort = new AbortController(); controller.current = abort;
    void workspaceFetch(base, { signal: abort.signal, cache: "no-store" }).then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "图文包没有准备好，请重新打开这篇内容");
      if (!abort.signal.aborted && current.current === identity) setBundle(data);
    }).catch(error => { if (!abort.signal.aborted && current.current === identity) setNotice(error.message); });
    return () => abort.abort();
  }, [base, identity, ready]);

  async function verified() {
    if (!bundle) throw new Error("图文正在准备，请稍后再试");
    const response = await workspaceFetch(`${base}?expected_hash=${bundle.content_hash}`, { cache: "no-store", signal: controller.current?.signal });
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || "内容已变化，请重新打开");
    if (current.current !== identity) throw new Error("请在当前门店重新打开这篇内容");
    return value as Bundle;
  }
  async function copy() {
    setWorking(true); setNotice("");
    try {
      if (platform === "wechat" && (typeof ClipboardItem === "undefined" || !navigator.clipboard?.write))
        throw new Error("当前浏览器未开启排版复制，请下载图文包");
      // Promise-valued representations preserve the click's user activation.
      const value = verified();
      if (platform === "wechat" && typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
        await navigator.clipboard.write([new ClipboardItem({
          "text/html": value.then(data => new Blob([data.rich_html], { type: "text/html" })),
          "text/plain": value.then(data => new Blob([data.plain_text], { type: "text/plain" })),
        })]);
      } else {
        await navigator.clipboard.writeText((await value).plain_text);
      }
      if (current.current === identity) setNotice(platform === "wechat" ? "排版已复制，可粘贴到公众号编辑器" : "文字已复制");
    } catch (error) {
      if (current.current === identity) setNotice(error instanceof Error ? error.message : "复制未完成，请下载图文包");
    } finally { if (current.current === identity) setWorking(false); }
  }
  async function download() {
    if (!bundle) return;
    setWorking(true); setNotice("");
    try {
      const response = await workspaceFetch(`${base}/package.zip?expected_hash=${bundle.content_hash}`, { cache: "no-store", signal: controller.current?.signal });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || "下载没有完成，请重试");
      }
      if (response.headers.get("content-type")?.split(";")[0] !== "application/zip" || response.headers.get("X-Content-SHA256") !== bundle.content_hash)
        throw new Error("图文包已变化，请重新打开这篇内容");
      const data = await response.blob();
      if (!data.size || current.current !== identity) throw new Error("请在当前门店重新下载");
      const url = URL.createObjectURL(data);
      const link = document.createElement("a"); link.href = url; link.download = `MateQuill-${platform}.zip`;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(platform === "moments" ? (bundle.images.length ? "文字和有序配图已下载" : "文字已下载") : "排版和原图已下载");
    } catch (error) {
      if (current.current === identity) setNotice(error instanceof Error ? error.message : "下载没有完成，请重试");
    } finally { if (current.current === identity) setWorking(false); }
  }
  if (!ready) return null;
  const downloadable = platform !== "moments" || !!bundle?.images.length;
  if (downloadOnly && !downloadable && !notice) return null;
  return <div className={styles.publication}>
    {preview && bundle && <iframe className={styles.preview} title="公众号图文预览" sandbox="" srcDoc={`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'"><body style="margin:0">${bundle.rich_html}</body></html>`} />}
    <div className={styles.actions}>
      {!downloadOnly && <button type="button" disabled={!bundle || working || disabled} onClick={() => void copy()}>{platform === "wechat" ? "复制公众号排版" : "复制文字"} ↗</button>}
      {downloadable && <button type="button" disabled={!bundle || working || disabled} onClick={() => void download()}>{working ? "正在准备…" : platform === "moments" ? "下载配图包" : "下载图文包"} ↓</button>}
    </div>
    {notice && <p className={styles.notice} role="status">{notice}</p>}
  </div>;
}
