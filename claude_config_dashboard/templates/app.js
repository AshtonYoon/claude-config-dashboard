// ── Floating characters ──────────────────────────────────────────
(function() {
  const layer = document.getElementById('float-layer');
  if (!layer) return;
  const HAS_CHAR = $has_character;
  if (!HAS_CHAR) return;

  const spots = [
    {l:4,  t:18, s:52, dur:13, delay:0},
    {l:80, t:30, s:44, dur:10, delay:3},
    {l:18, t:62, s:48, dur:15, delay:7},
    {l:65, t:72, s:40, dur:11, delay:2},
    {l:88, t:55, s:56, dur:14, delay:9},
    {l:42, t:88, s:36, dur:12, delay:5},
    {l:55, t:10, s:42, dur:16, delay:1},
  ];

  spots.forEach(function(sp) {
    const img = document.createElement('img');
    img.src = '/character?v=' + Date.now();
    img.className = 'fc';
    img.style.left = sp.l + '%';
    img.style.top  = sp.t + '%';
    img.style.width = sp.s + 'px';
    img.style.animationDuration = sp.dur + 's';
    img.style.animationDelay = sp.delay + 's';
    layer.appendChild(img);
  });
})();

function showTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('btn-' + name).classList.add('active');
  localStorage.setItem('claude-dash-tab', name);
}
showTab(localStorage.getItem('claude-dash-tab') || (document.getElementById('tab-plugins') ? 'plugins' : 'mcp'));

const DASH_TOKEN = '$token';

function openFile(encodedPath) {
  fetch('/open?path=' + encodedPath, {
    method: 'POST',
    headers: {'X-Dashboard-Token': DASH_TOKEN},
  }).catch(() => {});
}

function buildCleanupScript(plan) {
  function q(s) { return String(s).replace(/"/g, '\\"'); }
  const lines = [
    '#!/bin/sh',
    '# Claude Config Dashboard — cleanup plan',
    '# Generated ' + new Date().toISOString().slice(0, 10),
    '#',
    '# This does NOT delete anything — it archives items unused for 30+ days',
    '# into a dated folder so you can review before removing them for good.',
    '# Read every line before running. Undo: move files back out of the archive folder.',
    '',
    'set -e',
    'ARCHIVE="' + q(plan.archiveDir) + '"',
    'mkdir -p "$$ARCHIVE"',
    '',
  ];
  const skipped = [];
  (plan.items || []).forEach(function(item) {
    if (item.archiveSource) {
      lines.push('# ' + item.kind + ': ' + item.name + ' (~' + item.tokens + ' tokens/session)');
      lines.push('[ -e "' + q(item.archiveSource) + '" ] && mv -n "' + q(item.archiveSource) + '" "$$ARCHIVE/"');
      lines.push('');
    } else {
      skipped.push(item);
    }
  });
  if (skipped.length) {
    lines.push('# SKIPPED — needs manual review:');
    skipped.forEach(function(item) {
      lines.push('#   ' + item.name + ' — ' + item.skipReason);
    });
  }
  return lines.join('\n') + '\n';
}

function readCleanupPlan() {
  const el = document.getElementById('cleanup-plan-data');
  if (!el) return null;
  try {
    return JSON.parse(el.dataset.plan);
  } catch (_err) {
    return null;
  }
}

function downloadCleanupScript() {
  const plan = readCleanupPlan();
  if (!plan) return;
  const blob = new Blob([buildCleanupScript(plan)], {type: 'text/x-sh'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'claude-config-cleanup.sh';
  a.click();
  URL.revokeObjectURL(url);
}

function copyCleanupScript() {
  const plan = readCleanupPlan();
  if (!plan) return;
  navigator.clipboard.writeText(buildCleanupScript(plan)).catch(() => {});
}

function stopServer() {
  fetch('/stop', {
    method: 'POST',
    headers: {'X-Dashboard-Token': DASH_TOKEN},
  }).then(() => window.close()).catch(() => {});
}

function sortGrid(gridId, key, btn) {
  const grid = document.getElementById(gridId);
  if (!grid) return;
  const items = Array.from(grid.children);
  items.sort((a, b) => {
    if (key === 'name') return a.dataset.name.localeCompare(b.dataset.name);
    if (key === 'count') return parseInt(b.dataset.count || 0) - parseInt(a.dataset.count || 0);
    if (key === 'last') {
      const la = a.dataset.last || '', lb = b.dataset.last || '';
      if (!la && !lb) return 0;
      if (!la) return -1;
      if (!lb) return 1;
      return la.localeCompare(lb);
    }
    return 0;
  });
  items.forEach(item => grid.appendChild(item));
  const bar = btn.closest('.sort-bar');
  if (bar) bar.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function sortTable(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const asc = table.dataset.sortAsc === '1';
  rows.sort((a, b) => {
    const ca = parseInt(a.dataset.count || 0), cb = parseInt(b.dataset.count || 0);
    return asc ? ca - cb : cb - ca;
  });
  rows.forEach(r => tbody.appendChild(r));
  table.dataset.sortAsc = asc ? '0' : '1';
}

(function() {
  const modal = document.getElementById('skill-usage-modal');
  const modalTitle = document.getElementById('skill-usage-modal-title');
  const modalBody = document.getElementById('skill-usage-modal-body');
  const closeBtn = document.getElementById('skill-usage-modal-close');
  if (!modal || !modalTitle || !modalBody || !closeBtn) return;

  function esc(text) {
    return String(text)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }

  function usageBadge(item) {
    if (!item.last_used) return '<span class="badge stale-never">never used</span>';
    return '<span class="badge usage-count">' + esc(item.count) + ' uses</span>' +
      ' <span class="badge badge-gray">' + esc(item.last_used) + '</span>';
  }

  function renderRows(items) {
    if (!items.length) {
      return '<div class="modal-empty">No recorded usage yet</div>';
    }
    const rows = items.map((item) => {
      return '<tr data-count="' + esc(item.count) + '">' +
        '<td style="font-weight:500">' + esc(item.name) + '</td>' +
        '<td class="whitespace-nowrap">' + esc(item.count) + '</td>' +
        '<td class="whitespace-nowrap">' + usageBadge(item) + '</td>' +
        '</tr>';
    }).join('');
    return '<div style="border-radius:8px;overflow:hidden">' +
      '<table class="at"><thead><tr><th>Skill</th><th>Uses</th><th>Last used</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>';
  }

  function openSkillUsageModal(card) {
    const title = card.dataset.skillName || 'Plugin skills';
    let items = [];
    try {
      items = JSON.parse(card.dataset.childUsage || '[]');
    } catch (_err) {
      items = [];
    }
    modalTitle.textContent = title;
    modalBody.innerHTML = renderRows(items);
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeSkillUsageModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  document.querySelectorAll('.skill-item[data-child-usage]').forEach((card) => {
    if (!card.dataset.childUsage || card.dataset.childUsage === '[]') return;
    card.addEventListener('click', (event) => {
      if (event.target.closest('a')) return;
      openSkillUsageModal(card);
    });
  });

  closeBtn.addEventListener('click', closeSkillUsageModal);
  modal.addEventListener('click', (event) => {
    if (event.target === modal) closeSkillUsageModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modal.classList.contains('open')) {
      closeSkillUsageModal();
    }
  });
})();
