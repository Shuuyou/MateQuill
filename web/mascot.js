/* Stateful face animation; static navigation mark deliberately stays static. */
(() => {
  'use strict';
  if (typeof document === 'undefined') return;
  const selector = 'img.welcome-character,img.loading-character,img.clarification-character';
  function replaceCharacters() {
    document.querySelectorAll(selector).forEach(img => {
      const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 144 144');
      svg.setAttribute('class',`${img.className} youju-character`);
      svg.setAttribute('aria-hidden','true');
      svg.innerHTML = `<path d="M22 28C38 9 77 8 104 23c29 16 38 53 22 79-16 26-50 38-79 24C17 113 6 80 13 53c2-10 5-18 9-25Z" fill="#d5ed83"/><g transform="rotate(-7 72 72)"><g class="yj-head"><path d="M35 40c0-10 8-17 18-17h43c13 0 23 11 23 24v35c0 15-11 26-26 26H63l-21 15 3-20c-13-3-22-14-22-28V58c0-8 4-14 12-18Z" fill="#24261f"/><g class="yj-gaze"><g class="yj-open-eyes" fill="#fffef8"><rect class="yj-eye yj-eye-left" x="49" y="51" width="14" height="28" rx="7"/><rect class="yj-eye yj-eye-right" x="83" y="50" width="14" height="28" rx="7"/></g><g class="yj-smile-eyes" stroke="#fffef8" stroke-width="4" stroke-linecap="round" fill="none"><path d="M49 64q7-12 14 0M83 63q7-12 14 0"/></g><path d="M68 88q7 5 14-1" stroke="#fffef8" stroke-width="3" stroke-linecap="round" fill="none"/></g></g></g>`;
      img.replaceWith(svg);
    });
  }
  function update() {
    replaceCharacters();
    const body = document.body;
    if (!body) return;
    const focused = document.activeElement?.matches('textarea,input:not([type="hidden"])');
    const state = body.classList.contains('is-running') ? 'thinking' : body.classList.contains('is-clarifying') ? 'waiting' : body.classList.contains('is-error') ? 'error' : body.classList.contains('has-result') ? 'done' : focused || body.classList.contains('is-intaking') ? 'focus' : 'idle';
    document.querySelectorAll('.youju-character').forEach(el => {
      if (el.dataset.state !== state) el.dataset.state = state;
      el.dataset.paused = String(document.hidden);
    });
  }
  function init() {
    update();
    new MutationObserver(update).observe(document.body,{attributes:true,attributeFilter:['class'],childList:true,subtree:true});
    document.addEventListener('focusin',update);
    document.addEventListener('focusout',() => queueMicrotask(update));
    document.addEventListener('visibilitychange',update);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init,{once:true}); else init();
})();
