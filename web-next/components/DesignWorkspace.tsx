"use client";
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createDesign, getDesign, getDesignJob, reviseDesign, type DesignBundle } from '@/lib/design';
import { sessionScopeKey, workspaceFetch } from '@/lib/workspace';
import styles from './DesignWorkspace.module.css';

export default function DesignWorkspace({ initial, sourceRunId, platform, onBack, onChange }: { initial?: DesignBundle; sourceRunId?: string; platform?: string; onBack: () => void; onChange?: (value: DesignBundle) => void }) {
  const [bundle, setBundle] = useState(initial);
  const [selected, setSelected] = useState(0);
  const [instruction, setInstruction] = useState('');
  const [job, setJob] = useState('');
  const [phase, setPhase] = useState(initial ? '' : '正在构思这一组图片');
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const alive = useRef(true);
  const focusReturn = useRef<HTMLElement | null>(null);
  const operation = useRef(false);
  const scope = useRef(sessionScopeKey());
  const storage = `matequill-design:${scope.current}:${sourceRunId || initial?.id}:${platform || ''}`;
  const current = () => alive.current && scope.current === sessionScopeKey();
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  function savePending(value: { key: string; job?: string; instruction?: string }) { try { sessionStorage.setItem(storage, JSON.stringify(value)); } catch {} }
  async function begin(revision?: string, retry = false) {
    if (operation.current || !current()) return;
    operation.current = true;
    setError('');
    setPhase(revision ? '正在调整画面' : '正在构思这一组图片');
    try {
      let saved: { key: string; job?: string; instruction?: string } | null = null;
      try { saved = JSON.parse(sessionStorage.getItem(storage) || 'null'); } catch {}
      const completed = !revision && !saved ? sessionStorage.getItem(`${storage}:completed`) : '';
      if (completed) { const value = await getDesign(completed); if (current()) { setBundle(value); setPhase(''); } return; }
      const pending = saved?.job ? saved : !retry && saved ? saved : { key: crypto.randomUUID(), instruction: revision };
      savePending(pending);
      const response = pending.job ? { id: pending.job } : pending.instruction && bundle
        ? await reviseDesign(bundle.id, pending.instruction, pending.key)
        : await createDesign(sourceRunId!, platform, pending.key);
      savePending({ ...pending, job: response.id });
      if (current()) setJob(response.id);
    } catch (reason) {
      if (current()) { setError(reason instanceof Error ? reason.message : '图片暂时没有完成，请再试一次。'); setPhase(''); }
    } finally { operation.current = false; }
  }
  useEffect(() => {
    async function resume() {
      let pending = false;
      let completed = '';
      try { pending = !!sessionStorage.getItem(storage); completed = sessionStorage.getItem(`${storage}:completed`) || ''; } catch {}
      if (pending) { void begin(); return; }
      if (initial) return;
      if (completed) {
        try { const value = await getDesign(completed); if (current()) { setBundle(value); setPhase(''); } }
        catch (reason) { if (current()) { setError(reason instanceof Error ? reason.message : '暂时没有取回图片，请重试。'); setPhase(''); } }
      } else if (sourceRunId) void begin();
    }
    void resume();
  }, []); // Resume the original request, including after leaving this workbench.
  useEffect(() => {
    if (!job) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await getDesignJob(job);
        if (stopped || !current()) return;
        if (value.status === 'succeeded') {
          if (!value.result?.artboards?.length) throw new Error('图片结果没有完整返回，请重新打开作品。');
          setBundle(value.result); onChange?.(value.result); setSelected(0); setPhase(''); setJob(''); setInstruction('');
          try { sessionStorage.removeItem(storage); sessionStorage.setItem(`${storage}:completed`, value.result.id); } catch {} return;
        }
        if (value.status === 'failed' || value.status === 'cancelled') { sessionStorage.removeItem(storage); throw new Error(value.error?.message || '这次图片没有完成，请再试一次。'); }
        setPhase(value.status === 'queued' ? '图片创作已排队' : bundle ? '正在调整画面' : '正在制作这一组图片');
        timer = setTimeout(poll, 1800);
      } catch (reason) {
        if (!stopped && current()) { setError(reason instanceof Error ? reason.message : '暂时连接不上，请继续查看进度。'); setPhase(''); setJob(''); }
      }
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [job]);
  useEffect(() => {
    if (!expanded) return;
    const previous = focusReturn.current;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false); if (event.key === 'Tab') { event.preventDefault(); document.querySelector<HTMLButtonElement>(`[data-design-close]`)?.focus(); } };
    window.addEventListener('keydown', close); return () => { window.removeEventListener('keydown', close); previous?.focus(); };
  }, [expanded]);
  const board = bundle?.artboards[selected];
  const busy = !!phase || !!job;
  async function downloadAll() {
    if (!bundle || downloading) return;
    setDownloading(true); setError('');
    try {
      for (const [index, item] of bundle.artboards.entries()) {
        const response = await workspaceFetch(item.export_urls.png);
        if (!response.ok) throw new Error('图片下载没有完成，请重试。');
        const url = URL.createObjectURL(await response.blob());
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${bundle.title}-${index + 1}.png`; document.body.appendChild(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 20000);
      }
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : '下载没有完成。'); }
    finally { if (current()) setDownloading(false); }
  }
  return <section className={`${styles.workspace} mq-enter`} aria-label="图片设计作品">
    <header className={styles.heading}><button className="mq-text-button" onClick={onBack}>← 返回内容</button><h1>{bundle?.title || '把这次的灵感，做成图片。'}</h1>{bundle && <button className={styles.download} disabled={downloading} onClick={() => void downloadAll()}>{downloading ? '正在下载…' : '下载整组 ↗'}</button>}</header>
    {error && <div className={styles.error} role="alert"><p>{error}</p><button onClick={() => void begin(instruction || undefined, false)}>继续创作</button></div>}
    {!board && busy && <div className={styles.waiting} role="status"><div className={styles.paper}><span /><span /><span /></div><h2>{phase}</h2><p>可以先回到对话，稍后再来看。</p></div>}
    {board && <><div className={styles.stage} aria-busy={busy}><button className={styles.preview} onClick={(event) => { focusReturn.current = event.currentTarget; setExpanded(true); }} aria-label={`放大查看：${board.title}`}><img src={board.preview_url} alt={board.title || `第 ${selected + 1} 张图片`} /></button>{busy && <div className={styles.progress} role="status">{phase}<span /></div>}</div>
      <div className={styles.filmstrip} aria-label="整组图片">{bundle!.artboards.map((item, index) => <button key={item.id} aria-pressed={index === selected} onClick={() => setSelected(index)}><img src={item.preview_url} alt={item.title || `第 ${index + 1} 张`} /><span>{index + 1}</span></button>)}</div>
      <div className={styles.actions}><span>{selected + 1} / {bundle!.artboards.length}</span><a href={board.export_urls.png} download>下载这张</a><details className={styles.exportMenu}><summary>更多导出</summary><div>{board.export_urls.json && <a href={board.export_urls.json} download>设计源文件</a>}<a href={board.export_urls.svg} download>SVG 矢量图</a></div></details></div>
      <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); if (instruction.trim() && !busy) void begin(instruction.trim(), true); }}><label className={styles.inputLabel} htmlFor="design-revision">想再调整哪里？</label><textarea id="design-revision" value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={2} disabled={busy} /><button disabled={!instruction.trim() || busy} aria-label="修改图片">↑</button></form></>}
    {expanded && board && createPortal(<div className={styles.lightbox} role="dialog" aria-modal="true" aria-label={board.title} onClick={() => setExpanded(false)}><button data-design-close autoFocus onClick={() => setExpanded(false)} aria-label="关闭大图">关闭 ×</button><img src={board.preview_url} alt={board.title} onClick={(event) => event.stopPropagation()} /></div>, document.body)}
  </section>;
}

export function DesignLauncher({ sourceRunId, platform }: { sourceRunId: string; platform?: string }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const dialog = panel.current;
    if (!open || !dialog) return;
    const previous = document.body.style.overflow;
    const returnTo = trigger.current;
    document.body.style.overflow = 'hidden';
    dialog.focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      // The image lightbox owns focus and Escape while it is above this panel.
      if (event.defaultPrevented || event.isComposing || document.querySelector('[data-design-close]')) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]',
      )).filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden');
      const first = controls[0];
      const last = controls.at(-1);
      const active = document.activeElement;
      if (!first || !last) {
        event.preventDefault();
        dialog.focus({ preventScroll: true });
      } else if (!dialog.contains(active) || active === dialog || (event.shiftKey ? active === first : active === last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previous;
      if (returnTo?.isConnected) returnTo.focus({ preventScroll: true });
    };
  }, [open]);
  return <><button ref={trigger} type="button" className={styles.launch} aria-haspopup="dialog" onClick={() => setOpen(true)}>设计配图 <span aria-hidden="true">↗</span></button>{open && createPortal(<div ref={panel} className={styles.overlay} role="dialog" aria-modal="true" aria-label="设计配图" tabIndex={-1}><DesignWorkspace sourceRunId={sourceRunId} platform={platform} onBack={() => setOpen(false)} /></div>, document.body)}</>;
}
