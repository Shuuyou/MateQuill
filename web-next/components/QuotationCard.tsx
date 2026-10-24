"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getQuotation, quotationMoney, type AuthorizedQuotation } from "@/lib/quotation";
import { sessionScopeKey } from "@/lib/workspace";
import { useWorkspace } from "./WorkspaceProvider";
import styles from "./QuotationCard.module.css";

/** Reads the live authorized quote; conversation history contains only its ID. */
export function QuotationCard({ quoteId }: { quoteId: string }) {
  const { scopeKey } = useWorkspace();
  const identity = `${scopeKey}:${quoteId}`;
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const epoch = useRef(0);
  const mounted = useRef(false);
  const [storedQuote, setQuote] = useState<AuthorizedQuotation | null>(null);
  const [loadedIdentity, setLoadedIdentity] = useState("");
  // Suppress the previous store's card immediately, before effect cleanup runs.
  const quote = loadedIdentity === identity ? storedQuote : null;
  const [error, setError] = useState("");
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const refresh = useCallback(async () => {
    const requestEpoch = epoch.current;
    const live = () => mounted.current && epoch.current === requestEpoch
      && currentIdentity.current === identity && sessionScopeKey() === scopeKey;
    if (!live()) return null;
    try {
      const current = await getQuotation(quoteId);
      if (!live()) return null;
      setQuote(current);
      setLoadedIdentity(identity);
      setError("");
      return current;
    } catch (cause) {
      if (!live()) return null;
      setQuote(null);
      setError(cause instanceof Error ? cause.message : "报价暂未读取完成。请再试一次。");
      return null;
    }
  }, [identity, quoteId, scopeKey]);

  useEffect(() => {
    mounted.current = true;
    epoch.current += 1;
    setQuote(null);
    setLoadedIdentity("");
    setError("");
    setCopied(false);
    setCopying(false);
    void refresh();
    return () => { mounted.current = false; epoch.current += 1; };
  }, [identity, refresh]);

  // Recheck when returning to the tab and at expiry, rather than trusting old history.
  useEffect(() => {
    const onFocus = () => { void refresh(); };
    window.addEventListener("focus", onFocus);
    const expires = quote ? new Date(quote.valid_until).getTime() : NaN;
    const timeout = quote?.status === "ready" && Number.isFinite(expires)
      ? window.setTimeout(onFocus, Math.min(Math.max(expires - Date.now(), 0) + 100, 2_147_483_647))
      : undefined;
    return () => {
      window.removeEventListener("focus", onFocus);
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [quote, refresh]);

  async function copyQuotation() {
    const copyEpoch = epoch.current;
    const live = () => mounted.current && epoch.current === copyEpoch
      && currentIdentity.current === identity && sessionScopeKey() === scopeKey;
    if (!live()) return;
    setCopying(true);
    setCopied(false);
    try {
      const current = await refresh();
      if (!live() || !current || current.status !== "ready" || current.total_minor === null) return;
      const lines = [current.is_demo ? "演示报价" : "门店报价", current.product_name,
        `${current.configuration} · ${current.quantity} 件`,
        ...current.trace.map((step) => `${step.label}：减 ${quotationMoney(step.deduction_minor)}`),
        `含税合计 ${quotationMoney(current.total_minor)}`,
        `有效至 ${new Date(current.valid_until).toLocaleString("zh-CN", { hour12: false })}`];
      if (!live()) return;
      await navigator.clipboard.writeText(lines.join("\n"));
      if (live()) setCopied(true);
    } catch {
      if (live()) setError("复制未完成，请再试一次。");
    } finally {
      if (live()) setCopying(false);
    }
  }

  return <section className={styles.card} aria-label="门店报价">
    {!quote ? <div className={styles.message}>
      <p>{error || "正在读取这份门店报价…"}</p>
      {error && <button type="button" onClick={() => void refresh()}>重新读取</button>}
    </div> : <>
      <header className={styles.header}>
        <div><p className={styles.eyebrow}>{quote.is_demo ? "演示报价" : "门店报价"}</p>
          <h3>{quote.product_name}</h3><p>{quote.configuration} · {quote.quantity} 件</p></div>
        {quote.status === "ready" && quote.total_minor !== null
          ? <div className={styles.total}><span>含税合计</span><strong>{quotationMoney(quote.total_minor)}</strong></div>
          : <p className={styles.changed}>价格资料已更新</p>}
      </header>
      {quote.status !== "ready" ? <p className={styles.message}>{quote.message || "请接着告诉我，重新准备这份报价。"}</p> : <>
        <div className={styles.breakdown}>
          <div><span>商品合计</span><span>{quote.base_minor === null ? "—" : quotationMoney(quote.base_minor)}</span></div>
          {quote.trace.map((step) => <div key={step.promotion_id}><span>{step.label}</span><span>−{quotationMoney(step.deduction_minor)}</span></div>)}
          {quote.fees_minor !== null && quote.fees_minor > 0 && <div><span>其他已核准费用</span><span>{quotationMoney(quote.fees_minor)}</span></div>}
        </div>
        <details className={styles.details}><summary>查看报价依据</summary>
          <dl><div><dt>销售配置</dt><dd>{quote.sku}</dd></div>
            <div><dt>价格版本</dt><dd>第 {quote.rule_version} 版</dd></div>
            <div><dt>资料来源</dt><dd>{quote.source_id}</dd></div>
            <div><dt>有效时间</dt><dd>{new Date(quote.valid_until).toLocaleString("zh-CN", { hour12: false })}</dd></div>
          </dl>
          {quote.components.length > 0 && <ul>{quote.components.map((part) => <li key={part.sku}>{part.name} × {part.quantity} · {quotationMoney(part.unit_minor)}</li>)}</ul>}
          <p>已比较 {quote.optimality.evaluated_combinations} 组已授权优惠组合，采用其中合计最低的有效组合。</p>
        </details>
        <footer className={styles.footer}>
          <p>{error || "报价已准备好，可以接着安排到店体验。"}</p>
          <button type="button" onClick={() => void copyQuotation()} disabled={copying}>{copying ? "正在读取…" : copied ? "已复制" : "复制报价"}</button>
        </footer>
      </>}
    </>}
  </section>;
}

export default QuotationCard;
