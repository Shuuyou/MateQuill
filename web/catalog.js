'use strict';

// Product choices and recent work come only from the application's real data.
let catalogRequest = 0;
const catalogSourceNames = {local:'赛事知识库与华为官方资料',live:'ima 共享知识库',demo:'演练资料'};

function catalogText(value, limit = 240) {
  return String(value ?? '').slice(0, limit);
}

function renderProductCatalog() {
  const list = $('#catalog-list');
  if (!list) return;
  const query = catalogText($('#catalog-search')?.value, 200).trim().toLocaleLowerCase();
  const items = Array.isArray(state.catalog) ? state.catalog : [];
  const rows = items.filter(item => item && typeof item === 'object').map(item => ({
    name:catalogText(item.name || item.product || item.id),
    system:catalogText(item.system, 120)
  })).filter(item => item.name && (!query || `${item.name} ${item.system}`.toLocaleLowerCase().includes(query)));
  list.replaceChildren();
  if (!rows.length) {
    const message = document.createElement('p');
    message.className = 'catalog-empty';
    message.textContent = query ? '没有找到匹配的产品，请尝试其他型号或系统名称。' : '当前资料来源没有返回产品，可在创作时输入完整型号。';
    list.append(message);
    return;
  }
  const table = document.createElement('table');
  table.className = 'catalog-table';
  table.innerHTML = '<thead><tr><th scope="col">产品</th><th scope="col">系统</th><th scope="col">操作</th></tr></thead><tbody></tbody>';
  const body = table.querySelector('tbody');
  for (const item of rows) {
    const row = document.createElement('tr');
    row.innerHTML = `<td>${esc(item.name)}</td><td>${esc(item.system || '资料未注明')}</td><td><button type="button" class="catalog-use">用于这次创作</button></td>`;
    row.querySelector('button').addEventListener('click', () => {
      if (state.busy) return;
      const product = $('#product');
      if (product) product.value = item.name;
      state.intakeExplicit={...(state.intakeExplicit||{}),product:item.name};
      if ($('#context-product')) $('#context-product').textContent = item.name;
      if ($('#catalog-panel')) $('#catalog-panel').hidden = true;
      showCreate();
      if (document.body.classList.contains('has-result')) document.body.classList.add('editing-brief');
      $('#audience')?.focus();
      if(typeof saveCurrentWorkspace==='function')saveCurrentWorkspace();
    });
    body.append(row);
  }
  list.append(table);
}

async function showCatalog() {
  if (state.busy) return;
  const panel = $('#catalog-panel'), list = $('#catalog-list');
  if (!panel || !list) return;
  if ($('#create-panel')) $('#create-panel').hidden = true;
  if ($('#history-panel')) $('#history-panel').hidden = true;
  panel.hidden = false;
  $$('[data-nav]').forEach(button => button.classList.toggle('selected', button.dataset.nav === 'catalog'));
  if ($('#catalog-source')) $('#catalog-source').textContent = catalogSourceNames[state.mode] || '当前资料来源';
  const request = ++catalogRequest;
  const mode = state.mode;
  if (!Array.isArray(state.catalog) || (state.catalogMode && state.catalogMode !== mode) || state.catalogError) {
    list.textContent = '正在读取产品列表…';
    try {
      await loadCatalog();
      if (request !== catalogRequest || mode !== state.mode) return;
      const note = $('#catalog-note')?.textContent || '';
      if (state.catalogError || note.startsWith('产品列表读取失败')) {
        throw new Error(state.catalogError || note.replace(/^产品列表读取失败：?/, ''));
      }
    } catch (error) {
      if (request !== catalogRequest) return;
      list.replaceChildren();
      const message = document.createElement('p');
      message.className = 'catalog-empty';
      message.textContent = `产品列表读取失败：${catalogText(error.message, 500)}`;
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'secondary';
      retry.textContent = '重新读取';
      retry.addEventListener('click', showCatalog);
      list.append(message, retry);
      return;
    }
  }
  if (request === catalogRequest) renderProductCatalog();
}

function renderRecentTasks() {
  const list = $('#recent-tasks'), section = $('#recent-section');
  if (!list || !section) return;
  const entries = Array.isArray(state.history) ? state.history.slice(0, 5) : [];
  section.hidden = entries.length === 0;
  list.replaceChildren();
  for (const entry of entries) {
    if (!entry?.result || !entry?.brief) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'recent-task';
    button.textContent = catalogText(entry.result.title || entry.brief.scene || entry.brief.product || '创作任务', 160);
    button.addEventListener('click', () => {
      if (state.busy) return;
      if ($('#catalog-panel')) $('#catalog-panel').hidden = true;
      showCreate();
      render(entry.result, entry.brief);
    });
    list.append(button);
  }
  section.hidden = list.childElementCount === 0;
}

$$('[data-nav="catalog"]').forEach(button => button.addEventListener('click', showCatalog));
$('#catalog-search')?.addEventListener('input', renderProductCatalog);
renderRecentTasks();
