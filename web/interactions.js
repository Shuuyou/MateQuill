/* Shared motion for real navigation and tab changes. */
(() => {
  'use strict';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const groups = [...document.querySelectorAll('.tabs,.evidence-tabs')];
  const animations = new WeakMap();
  function enter(panel, direction = 1) {
    if (!panel || panel.hidden || reduced.matches || !panel.animate) return;
    animations.get(panel)?.cancel();
    animations.set(panel, panel.animate([
      {opacity: 0, transform: `translate(${direction * 9}px, 4px)`},
      {opacity: 1, transform: 'translate(0, 0)'}
    ], {duration: 260, easing: 'cubic-bezier(.2,.8,.2,1)'}));
  }
  function update(group) {
    const buttons = [...group.querySelectorAll('button')];
    const selected = buttons.find(button => button.classList.contains('selected') && !button.hidden);
    if (!selected || !group.getClientRects().length) return;
    group.style.setProperty('--tab-left', selected.offsetLeft + 'px');
    group.style.setProperty('--tab-top', selected.offsetTop + selected.offsetHeight - 2 + 'px');
    group.style.setProperty('--tab-width', selected.offsetWidth + 'px');
    if (!group.classList.contains('motion-tabs')) group.classList.add('motion-tabs');
    group.setAttribute('role', 'tablist');
    for (const button of buttons) {
      const active = button === selected;
      const panelId = button.dataset.view ? 'view-' + button.dataset.view : 'evidence-' + button.dataset.evidence;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', String(active));
      button.setAttribute('aria-controls', panelId);
      button.removeAttribute('aria-pressed');
      button.tabIndex = active ? 0 : -1;
      if (!button.id) button.id = 'tab-' + panelId;
      const panel = document.getElementById(panelId);
      if (panel) {panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id);}
    }
  }
  groups.forEach(group => {
    group.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button || !group.contains(button)) return;
      const previous = Number(group.dataset.activeIndex || 0);
      const index = [...group.querySelectorAll('button')].indexOf(button);
      group.dataset.activeIndex = String(index);
      update(group);
      const panelId = button.dataset.view ? 'view-' + button.dataset.view : 'evidence-' + button.dataset.evidence;
      enter(document.getElementById(panelId), index >= previous ? 1 : -1);
    });
    group.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      const buttons = [...group.querySelectorAll('button')].filter(button => !button.hidden && !button.disabled);
      const current = buttons.indexOf(document.activeElement);
      if (current < 0) return;
      event.preventDefault();
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[index].focus(); buttons[index].click();
    });
    new ResizeObserver(() => update(group)).observe(group);
    new MutationObserver(() => update(group)).observe(group, {subtree:true, attributes:true, attributeFilter:['class','hidden']});
    update(group);
  });
  // Attribute observers follow actual UI state, including restored results.
  for (const panel of document.querySelectorAll('#create-panel,#history-panel,#catalog-panel,#intake-panel,#brief-details')) {
    new MutationObserver(() => {if (!panel.hidden) enter(panel);}).observe(panel, {attributes:true, attributeFilter:['hidden']});
  }
  document.fonts?.ready.then(() => groups.forEach(update));
})();
