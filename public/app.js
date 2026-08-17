let data = { categories: [] };
let selectedCard = null;
let selectedCategoryId = null;
let authToken = localStorage.getItem('spw_token');
let isDirty = false;

function markDirty() {
  if (isDirty) return;
  isDirty = true;
  document.getElementById('btn-save-card').classList.add('dirty');
}

function markClean() {
  isDirty = false;
  document.getElementById('btn-save-card').classList.remove('dirty');
}

async function apiFetch(url, options = {}) {
  const headers = { ...options.headers, 'x-auth-token': authToken };
  return fetch(url, { ...options, headers });
}

async function checkAuth() {
  const res = await fetch('/api/auth/status');
  const { hasPassword } = await res.json();
  if (!hasPassword) {
    document.getElementById('auth-setup').style.display = 'block';
    document.getElementById('auth-login').style.display = 'none';
  } else if (!authToken) {
    document.getElementById('auth-setup').style.display = 'none';
    document.getElementById('auth-login').style.display = 'block';
  } else {
    const checkRes = await apiFetch('/api/passwords');
    if (checkRes.ok) {
      showApp();
    } else {
      authToken = null;
      localStorage.removeItem('spw_token');
      document.getElementById('auth-setup').style.display = 'none';
      document.getElementById('auth-login').style.display = 'block';
    }
  }
}

function showApp() {
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  loadData();
}

function showAuthError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

document.getElementById('btn-setup').addEventListener('click', async () => {
  const pw = document.getElementById('setup-password').value;
  const pw2 = document.getElementById('setup-password-confirm').value;
  if (!pw || pw.length < 4) return showAuthError('パスワードは4文字以上');
  if (pw !== pw2) return showAuthError('パスワードが一致しません');
  const res = await fetch('/api/auth/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw })
  });
  const result = await res.json();
  if (result.success) {
    authToken = result.token;
    localStorage.setItem('spw_token', authToken);
    showApp();
  } else {
    showAuthError(result.error);
  }
});

document.getElementById('btn-login').addEventListener('click', async () => {
  const pw = document.getElementById('login-password').value;
  if (!pw) return showAuthError('パスワードを入力してください');
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw })
  });
  const result = await res.json();
  if (result.success) {
    authToken = result.token;
    localStorage.setItem('spw_token', authToken);
    showApp();
  } else {
    showAuthError(result.error);
  }
});

document.getElementById('btn-logout').addEventListener('click', async () => {
  await apiFetch('/api/auth/logout', { method: 'POST' });
  authToken = null;
  localStorage.removeItem('spw_token');
  document.getElementById('app').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'flex';
  document.getElementById('auth-setup').style.display = 'none';
  document.getElementById('auth-login').style.display = 'block';
  document.getElementById('login-password').value = '';
});

['setup-password', 'setup-password-confirm', 'login-password'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      if (id.startsWith('setup')) document.getElementById('btn-setup').click();
      else document.getElementById('btn-login').click();
    }
  });
});

async function loadData() {
  const res = await apiFetch('/api/passwords');
  if (!res.ok) {
    authToken = null;
    localStorage.removeItem('spw_token');
    location.reload();
    return;
  }
  data = await res.json();
  renderSidebar();
}

async function saveData() {
  await apiFetch('/api/passwords', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  markClean();
}

function collectCurrentInputs() {
  if (!selectedCard) return;
  const nameInput = document.getElementById('detail-card-name');
  if (nameInput) selectedCard.name = nameInput.value;
  const memoInput = document.getElementById('detail-memo-input');
  if (memoInput) selectedCard.memo = memoInput.value;
  document.querySelectorAll('#detail-fields .field-row').forEach((row, idx) => {
    if (!selectedCard.fields[idx]) return;
    const keyInput = row.querySelector('.field-label-input');
    const valInput = row.querySelector('.field-value-input');
    if (keyInput) selectedCard.fields[idx].key = keyInput.value;
    if (valInput) selectedCard.fields[idx].value = valInput.value;
  });
}

function sortedByName(arr) {
  return [...arr].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}

function renderSidebar(filter = '') {
  const list = document.getElementById('category-list');
  list.innerHTML = '';
  const lf = filter.toLowerCase();

  sortedByName(data.categories).forEach(cat => {
    const sortedCards = sortedByName(cat.cards);
    const filteredCards = sortedCards.filter(c =>
      !lf || c.name.toLowerCase().includes(lf) ||
      c.fields.some(f => f.value.toLowerCase().includes(lf) || f.key.toLowerCase().includes(lf))
    );
    if (lf && filteredCards.length === 0) return;

    const catEl = document.createElement('div');
    catEl.className = 'category-item';
    catEl.innerHTML = `
      <div class="category-header" data-id="${cat.id}">
        <span class="cat-name">${escHtml(cat.name)}</span>
        <span class="cat-actions">
          <button class="btn-small btn-add-card" data-cat="${cat.id}" title="カード追加">+</button>
          <button class="btn-small btn-rename-cat" data-cat="${cat.id}" title="名前変更">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="btn-small btn-del-cat" data-cat="${cat.id}" title="削除">×</button>
        </span>
      </div>
    `;
    list.appendChild(catEl);

    (lf ? filteredCards : sortedCards).forEach(card => {
      const cardEl = document.createElement('div');
      cardEl.className = 'card-item' + (selectedCard && selectedCard.id === card.id ? ' active' : '');
      cardEl.dataset.cardId = card.id;
      cardEl.dataset.catId = cat.id;
      cardEl.innerHTML = `<span class="card-name">${escHtml(card.name)}</span>`;
      list.appendChild(cardEl);
    });
  });
}

function isPasswordField(key) {
  const k = key.toLowerCase();
  return k.includes('パスワード') || k.includes('password') || k.includes('pass') || k.includes('secret');
}

function renderCategorySelect() {
  const sel = document.getElementById('detail-category-select');
  if (!sel) return;
  sel.innerHTML = '';
  sortedByName(data.categories).forEach(cat => {
    const opt = document.createElement('option');
    opt.value = cat.id;
    opt.textContent = cat.name;
    sel.appendChild(opt);
  });
  if (selectedCategoryId) sel.value = selectedCategoryId;
}

function showCardDetail(catId, cardId) {
  collectCurrentInputs();
  const cat = data.categories.find(c => c.id === catId);
  if (!cat) return;
  const card = cat.cards.find(c => c.id === cardId);
  if (!card) return;

  selectedCard = card;
  selectedCategoryId = catId;
  renderCategorySelect();

  document.getElementById('welcome-screen').style.display = 'none';
  document.getElementById('card-detail').style.display = 'block';
  document.getElementById('detail-card-name').value = card.name;

  const memoInput = document.getElementById('detail-memo-input');
  if (memoInput) memoInput.value = card.memo || '';

  const fieldsDiv = document.getElementById('detail-fields');
  fieldsDiv.innerHTML = '';

  card.fields.forEach((field, idx) => {
    const row = document.createElement('div');
    row.className = 'field-row';
    const isPw = field.masked !== undefined ? field.masked : isPasswordField(field.key);
    row.innerHTML = `
      <input type="text" class="field-label-input" data-idx="${idx}" value="${escAttr(field.key)}">
      <div class="field-input-wrap">
        <input type="${isPw ? 'password' : 'text'}" class="field-value-input" data-idx="${idx}" value="${escAttr(field.value)}">
        ${isPw ? `<button class="btn-toggle-pw" data-idx="${idx}" title="表示切替"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>` : ''}
        <button class="btn-copy" data-idx="${idx}">コピー</button>
        <button class="btn-delete-field" data-idx="${idx}" title="削除">×</button>
      </div>
    `;
    fieldsDiv.appendChild(row);
  });

  renderSidebar(document.getElementById('search-input').value);
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function escAttr(s) {
  return s.replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function genId() {
  return 'id-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;opacity:0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

document.getElementById('category-list').addEventListener('click', e => {
  const addCard = e.target.closest('.btn-add-card');
  if (addCard) {
    collectCurrentInputs();
    const catId = addCard.dataset.cat;
    const cat = data.categories.find(c => c.id === catId);
    if (!cat) return;
    const card = { id: genId(), name: '新しいカード', memo: '', fields: [{ key: 'ID', value: '' }, { key: 'パスワード', value: '' }] };
    cat.cards.push(card);
    saveData();
    showCardDetail(catId, card.id);
    return;
  }

  const renameCat = e.target.closest('.btn-rename-cat');
  if (renameCat) {
    const catId = renameCat.dataset.cat;
    const cat = data.categories.find(c => c.id === catId);
    if (!cat) return;
    const newName = prompt('カテゴリー名を入力:', cat.name);
    if (!newName || newName === cat.name) return;
    cat.name = newName;
    saveData();
    renderSidebar(document.getElementById('search-input').value);
    renderCategorySelect();
    return;
  }

  const delCat = e.target.closest('.btn-del-cat');
  if (delCat) {
    const catId = delCat.dataset.cat;
    if (!confirm('カテゴリーを削除しますか？')) return;
    data.categories = data.categories.filter(c => c.id !== catId);
    saveData();
    if (selectedCategoryId === catId) {
      selectedCard = null;
      selectedCategoryId = null;
      document.getElementById('card-detail').style.display = 'none';
      document.getElementById('welcome-screen').style.display = 'flex';
    }
    renderSidebar(document.getElementById('search-input').value);
    renderCategorySelect();
    return;
  }

  const cardEl = e.target.closest('.card-item');
  if (cardEl) showCardDetail(cardEl.dataset.catId, cardEl.dataset.cardId);
});

document.getElementById('search-input').addEventListener('input', e => {
  renderSidebar(e.target.value);
});

document.getElementById('btn-add-category').addEventListener('click', () => {
  const name = prompt('カテゴリー名を入力:');
  if (!name) return;
  const cat = { id: genId(), name, cards: [] };
  data.categories.push(cat);
  saveData();
  renderSidebar();
  renderCategorySelect();
});

document.getElementById('btn-add-field').addEventListener('click', () => {
  if (!selectedCard) return;
  collectCurrentInputs();
  selectedCard.fields.push({ key: '新しい項目', value: '', masked: true });
  markDirty();
  showCardDetail(selectedCategoryId, selectedCard.id);
});

document.getElementById('detail-category-select').addEventListener('change', e => {
  if (!selectedCard || !selectedCategoryId) return;
  const newCatId = e.target.value;
  if (newCatId === selectedCategoryId) return;
  collectCurrentInputs();
  const oldCat = data.categories.find(c => c.id === selectedCategoryId);
  const newCat = data.categories.find(c => c.id === newCatId);
  if (!oldCat || !newCat) return;
  oldCat.cards = oldCat.cards.filter(c => c.id !== selectedCard.id);
  newCat.cards.push(selectedCard);
  selectedCategoryId = newCatId;
  saveData();
  renderSidebar(document.getElementById('search-input').value);
});

document.getElementById('btn-delete-card').addEventListener('click', () => {
  if (!selectedCard || !selectedCategoryId) return;
  if (!confirm('カードを削除しますか？')) return;
  const cat = data.categories.find(c => c.id === selectedCategoryId);
  if (!cat) return;
  cat.cards = cat.cards.filter(c => c.id !== selectedCard.id);
  saveData();
  selectedCard = null;
  selectedCategoryId = null;
  document.getElementById('card-detail').style.display = 'none';
  document.getElementById('welcome-screen').style.display = 'flex';
  renderSidebar(document.getElementById('search-input').value);
});

document.getElementById('detail-fields').addEventListener('click', e => {
  const toggleBtn = e.target.closest('.btn-toggle-pw');
  if (toggleBtn) {
    const row = toggleBtn.closest('.field-row');
    const input = row.querySelector('.field-value-input');
    const idx = parseInt(toggleBtn.dataset.idx);
    if (selectedCard && selectedCard.fields[idx]) {
      selectedCard.fields[idx].masked = input.type === 'password' ? false : true;
    }
    if (input.type === 'password') {
      input.type = 'text';
      toggleBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
    } else {
      input.type = 'password';
      toggleBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    }
    return;
  }

  const copyBtn = e.target.closest('.btn-copy');
  if (copyBtn) {
    const row = copyBtn.closest('.field-row');
    const input = row.querySelector('.field-value-input');
    const text = input.value;
    function showCopied() {
      copyBtn.textContent = 'コピー済み';
      copyBtn.classList.add('copied');
      setTimeout(() => { copyBtn.textContent = 'コピー'; copyBtn.classList.remove('copied'); }, 1500);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(showCopied).catch(() => { fallbackCopy(text); showCopied(); });
    } else {
      fallbackCopy(text); showCopied();
    }
    return;
  }

  const delBtn = e.target.closest('.btn-delete-field');
  if (delBtn) {
    const idx = parseInt(delBtn.dataset.idx);
    if (!selectedCard) return;
    collectCurrentInputs();
    selectedCard.fields.splice(idx, 1);
    markDirty();
    showCardDetail(selectedCategoryId, selectedCard.id);
  }
});

document.getElementById('btn-save-card').addEventListener('click', () => {
  if (!selectedCard) return;
  collectCurrentInputs();
  saveData();
  renderSidebar(document.getElementById('search-input').value);
});

document.getElementById('btn-export').addEventListener('click', () => {
  document.getElementById('export-modal').style.display = 'flex';
});

document.getElementById('btn-export-cancel').addEventListener('click', () => {
  document.getElementById('export-modal').style.display = 'none';
});

document.getElementById('btn-export-confirm').addEventListener('click', async () => {
  const res = await apiFetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  });
  if (!res.ok) { alert('エクスポートに失敗しました'); return; }
  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const filename = disposition.includes('filename=')
    ? disposition.split('filename=')[1].replace(/"/g, '')
    : 'spw.zip';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
  document.getElementById('export-modal').style.display = 'none';
});

document.getElementById('export-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('export-modal').style.display = 'none';
});

document.getElementById('btn-import').addEventListener('click', () => {
  document.getElementById('import-modal').style.display = 'flex';
  document.getElementById('import-password').value = '';
  document.getElementById('import-file').value = '';
  document.getElementById('import-password').focus();
});

document.getElementById('btn-import-cancel').addEventListener('click', () => {
  document.getElementById('import-modal').style.display = 'none';
});

document.getElementById('btn-import-confirm').addEventListener('click', async () => {
  const password = document.getElementById('import-password').value;
  const fileInput = document.getElementById('import-file');
  if (!password) return alert('パスワードを入力してください');
  if (!fileInput.files.length) return alert('ファイルを選択してください');
  const file = fileInput.files[0];
  const reader = new FileReader();
  reader.onload = async () => {
    const base64 = reader.result.split(',')[1];
    const res = await apiFetch('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, fileData: base64 })
    });
    const result = await res.json();
    if (result.success) {
      alert('インポートが完了しました');
      document.getElementById('import-modal').style.display = 'none';
      await loadData();
    } else {
      alert('インポートに失敗しました: ' + (result.error || '不明なエラー'));
    }
  };
  reader.readAsDataURL(file);
});

document.getElementById('import-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('import-modal').style.display = 'none';
});

document.getElementById('btn-change-pw').addEventListener('click', () => {
  document.getElementById('change-pw-modal').style.display = 'flex';
  document.getElementById('current-password').value = '';
  document.getElementById('new-password').value = '';
  document.getElementById('new-password-confirm').value = '';
  document.getElementById('current-password').focus();
});

document.getElementById('btn-change-pw-cancel').addEventListener('click', () => {
  document.getElementById('change-pw-modal').style.display = 'none';
});

document.getElementById('btn-change-pw-confirm').addEventListener('click', async () => {
  const current = document.getElementById('current-password').value;
  const newPw = document.getElementById('new-password').value;
  const newPw2 = document.getElementById('new-password-confirm').value;
  if (!current) return alert('現在のパスワードを入力してください');
  if (!newPw || newPw.length < 4) return alert('新しいパスワードは4文字以上');
  if (newPw !== newPw2) return alert('パスワードが一致しません');
  const res = await apiFetch('/api/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ currentPassword: current, newPassword: newPw })
  });
  const result = await res.json();
  if (result.success) {
    alert('パスワードを変更しました');
    document.getElementById('change-pw-modal').style.display = 'none';
  } else {
    alert(result.error || '変更に失敗しました');
  }
});

document.getElementById('change-pw-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('change-pw-modal').style.display = 'none';
});

document.getElementById('detail-card-name').addEventListener('input', () => markDirty());

document.getElementById('detail-memo-input').addEventListener('input', () => markDirty());

document.getElementById('detail-fields').addEventListener('input', e => {
  if (e.target.classList.contains('field-label-input') || e.target.classList.contains('field-value-input')) {
    markDirty();
  }
});

checkAuth();
