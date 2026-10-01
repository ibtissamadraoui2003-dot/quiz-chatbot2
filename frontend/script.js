/* =========================================================================
   Study — logique du frontend
   Un seul fichier, vanilla JS, sans framework : le frontend est servi par
   Flask lui-même donc toutes les requêtes utilisent des chemins relatifs
   (même origine, pas besoin de configurer d'URL de serveur).
   ========================================================================= */

const API_BASE = '';

/* ------------------------------ Références DOM ------------------------------ */

const sidebar = document.getElementById('sidebar');
const sidebarClose = document.getElementById('sidebarClose');
const sidebarOpen = document.getElementById('sidebarOpen');
const sidebarScrim = document.getElementById('sidebarScrim');

const btnAddCours = document.getElementById('btnAddCours');
const btnAddExamen = document.getElementById('btnAddExamen');
const fileInputCours = document.getElementById('fileInputCours');
const fileInputExamen = document.getElementById('fileInputExamen');
const listCours = document.getElementById('listCours');
const listExamens = document.getElementById('listExamens');
const progressBody = document.getElementById('progressBody');
const aiStatusDot = document.getElementById('aiStatusDot');
const aiStatusText = document.getElementById('aiStatusText');

const chatHeaderSub = document.getElementById('chatHeaderSub');
const btnReset = document.getElementById('btnReset');
const chatThread = document.getElementById('chatThread');
const emptyState = document.getElementById('emptyState');
const suggestionChips = document.getElementById('suggestionChips');

const attachBtn = document.getElementById('attachBtn');
const attachMenu = document.getElementById('attachMenu');
const composerInput = document.getElementById('composerInput');
const sendBtn = document.getElementById('sendBtn');

const modalOverlay = document.getElementById('modalOverlay');
const modalTitleEl = document.getElementById('modalTitle');
const modalFileName = document.getElementById('modalFileName');
const modalTitleInput = document.getElementById('modalTitleInput');
const modalSubjectSelect = document.getElementById('modalSubjectSelect');
const modalSubjectOther = document.getElementById('modalSubjectOther');
const modalCancel = document.getElementById('modalCancel');
const modalConfirm = document.getElementById('modalConfirm');

let pendingUpload = null;

/* ------------------------------ Utilitaires ------------------------------ */

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

// Mise en forme minimale et sûre : échappe tout, puis n'introduit que les
// balises **gras**, _italique_ et retours à la ligne que NOUS contrôlons.
function formatInline(text) {
  let safe = escapeHtml(text || '');
  safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  safe = safe.replace(/(^|[^\w])_(.+?)_(?!\w)/g, '$1<em>$2</em>');
  safe = safe.replace(/\n/g, '<br>');
  return safe;
}

const SUBJECT_PALETTE = ['#D9A441', '#4C8E7A', '#8B6F9E', '#6E8AA6', '#B9862E', '#7C9473', '#C1554A', '#5B7C99'];
function colorForSubject(subject) {
  const s = subject || 'Non spécifié';
  let hash = 0;
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
  return SUBJECT_PALETTE[hash % SUBJECT_PALETTE.length];
}

function scrollThreadToBottom() {
  chatThread.scrollTop = chatThread.scrollHeight;
}

function hideEmptyState() {
  emptyState.style.display = 'none';
}

function showToast(message) {
  let wrap = document.querySelector('.toast-wrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'toast-wrap';
    document.body.appendChild(wrap);
  }
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  wrap.appendChild(toast);
  setTimeout(() => toast.remove(), Math.max(4500, message.length * 70));
}

async function apiFetch(path, { method = 'GET', body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, options);
  } catch (networkErr) {
    showToast("Impossible de contacter le serveur. Vérifie qu'il est démarré (python app.py).");
    throw networkErr;
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* réponse sans corps JSON */ }
  if (!res.ok) {
    const message = (data && data.error) || `Erreur ${res.status}`;
    showToast(message);
    throw new Error(message);
  }
  return data;
}

/* ------------------------------ Icônes (SVG inline) ------------------------------ */

const ICONS = {
  summary: '<svg viewBox="0 0 24 24" fill="none"><path d="M6 4h9l5 5v11H6z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 12h6M9 16h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  quiz: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6"/><path d="M9.5 9.3a2.5 2.5 0 1 1 3.7 2.2c-.7.4-1.2.9-1.2 1.7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="12" cy="16.3" r="0.9" fill="currentColor"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 7h14M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-8 0 1 13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

/* ------------------------------ Rendu des messages ------------------------------ */

function renderMessage(msg) {
  hideEmptyState();
  const row = document.createElement('div');
  row.className = `msg-row from-${msg.role}`;

  if (msg.message_type === 'summary') {
    row.appendChild(buildSummaryCard(msg));
  } else if (msg.message_type === 'exam_analysis') {
    row.appendChild(buildExamCard(msg));
  } else if (msg.message_type === 'quiz') {
    row.appendChild(buildQuizCard(msg));
  } else if (msg.message_type === 'weak_points') {
    row.appendChild(buildWeakPointsCard(msg));
  } else {
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = formatInline(msg.content || '');
    row.appendChild(bubble);
  }

  chatThread.appendChild(row);
  return row;
}

function buildCardShell(msg) {
  const card = document.createElement('div');
  card.className = 'card';
  const lead = document.createElement('p');
  lead.className = 'card-lead';
  lead.innerHTML = formatInline(msg.content);
  card.appendChild(lead);
  const body = document.createElement('div');
  body.className = 'card-body';
  card.appendChild(body);
  return { card, body };
}

function buildTag(subject, label) {
  const tag = document.createElement('div');
  tag.className = 'card-tag';
  tag.innerHTML = `<span class="dot" style="background:${colorForSubject(subject)}"></span> ${escapeHtml(subject || label)}`;
  return tag;
}

function buildSummaryCard(msg) {
  const meta = msg.metadata || {};
  const { card, body } = buildCardShell(msg);
  body.appendChild(buildTag(meta.subject, 'Cours'));

  if (meta.summary) {
    const p = document.createElement('p');
    p.className = 'card-summary-text';
    p.textContent = meta.summary;
    body.appendChild(p);
  }

  if (meta.key_points && meta.key_points.length) {
    const title = document.createElement('p');
    title.className = 'card-list-title';
    title.textContent = 'Points essentiels';
    body.appendChild(title);

    const ul = document.createElement('ul');
    ul.className = 'point-list';
    meta.key_points.forEach((pt) => {
      const li = document.createElement('li');
      li.textContent = pt;
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }

  if (meta.concepts && meta.concepts.length) {
    const title = document.createElement('p');
    title.className = 'card-list-title';
    title.textContent = 'Notions à connaître — clique pour une explication';
    body.appendChild(title);

    const pills = document.createElement('div');
    pills.className = 'concept-pills';
    meta.concepts.forEach((c) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pill';
      btn.textContent = c;
      btn.addEventListener('click', () => sendMessage(`Explique-moi : ${c}`));
      pills.appendChild(btn);
    });
    body.appendChild(pills);
  }

  return card;
}

function buildExamCard(msg) {
  const meta = msg.metadata || {};
  const { card, body } = buildCardShell(msg);
  body.appendChild(buildTag(meta.subject, 'Examen'));

  if (meta.resume) {
    const p = document.createElement('p');
    p.className = 'card-summary-text';
    p.textContent = meta.resume;
    body.appendChild(p);
  }

  if (meta.themes && meta.themes.length) {
    const title = document.createElement('p');
    title.className = 'card-list-title';
    title.textContent = 'Thèmes qui reviennent';
    body.appendChild(title);

    const pills = document.createElement('div');
    pills.className = 'theme-pills';
    meta.themes.forEach((t) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pill';
      btn.textContent = t;
      btn.addEventListener('click', () => sendMessage(`Explique-moi : ${t}`));
      pills.appendChild(btn);
    });
    body.appendChild(pills);
  }

  if (meta.questions_extraites && meta.questions_extraites.length) {
    const title = document.createElement('p');
    title.className = 'card-list-title';
    title.textContent = 'Questions déjà tombées';
    body.appendChild(title);

    const ul = document.createElement('ul');
    ul.className = 'exam-questions';
    meta.questions_extraites.forEach((q) => {
      const li = document.createElement('li');
      li.className = 'exam-question';
      const rep = q.reponse ? `<div class="r">${escapeHtml(q.reponse)}</div>` : '';
      li.innerHTML = `<div class="q">${escapeHtml(q.question)}</div>${rep}`;
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }

  return card;
}

function buildWeakPointsCard(msg) {
  const meta = msg.metadata || {};
  const { card, body } = buildCardShell(msg);
  const weak = meta.weak || [];

  if (weak.length) {
    const ul = document.createElement('ul');
    ul.className = 'weak-list';
    weak.forEach((w) => {
      const li = document.createElement('li');
      li.className = 'weak-item';
      li.innerHTML = `<span class="concept">${escapeHtml(w.concept)}</span>` +
        `<span class="accuracy">${w.accuracy}%</span>` +
        `<span class="explain-hint">Expliquer</span>`;
      li.addEventListener('click', () => sendMessage(`Explique-moi : ${w.concept}`));
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }

  return card;
}

/* ------------------------------ Quiz interactif ------------------------------ */

function buildQuizCard(msg) {
  const questions = (msg.metadata && msg.metadata.questions) || [];
  const { card, body } = buildCardShell(msg);

  let startIndex = questions.findIndex((q) => !q.answered);
  if (startIndex === -1) startIndex = questions.length;
  const state = { index: startIndex };

  function renderStep() {
    body.innerHTML = '';
    if (state.index >= questions.length) {
      body.appendChild(buildRecap());
      return;
    }
    body.appendChild(buildQuestionStep(questions[state.index], state.index, questions.length));
  }

  function buildQuestionStep(q, idx, total) {
    const wrap = document.createElement('div');

    const progress = document.createElement('p');
    progress.className = 'quiz-progress';
    progress.textContent = `Question ${idx + 1} / ${total}`;
    wrap.appendChild(progress);

    const qText = document.createElement('p');
    qText.className = 'quiz-question';
    qText.textContent = q.question;
    wrap.appendChild(qText);

    const opts = document.createElement('div');
    opts.className = 'quiz-options';
    wrap.appendChild(opts);

    const explanationSlot = document.createElement('div');
    wrap.appendChild(explanationSlot);

    const navSlot = document.createElement('div');
    wrap.appendChild(navSlot);

    function lockOptions() {
      [...opts.children].forEach((b) => { b.disabled = true; });
    }

    function showAnsweredState(selectedIndex, correctIndex, isCorrect, explanation) {
      lockOptions();
      [...opts.children].forEach((btn, i) => {
        if (i === correctIndex) btn.classList.add('is-correct');
        if (i === selectedIndex && !isCorrect) btn.classList.add('is-wrong');
      });
      if (explanation) {
        explanationSlot.innerHTML =
          `<div class="quiz-explanation"><span class="verdict ${isCorrect ? 'ok' : 'ko'}">` +
          `${isCorrect ? 'Bonne réponse.' : 'Pas tout à fait.'}</span> ${formatInline(explanation)}</div>`;
      }
      const navWrap = document.createElement('div');
      navWrap.className = 'quiz-nav';
      const nextBtn = document.createElement('button');
      nextBtn.type = 'button';
      nextBtn.className = 'quiz-next-btn';
      nextBtn.textContent = idx + 1 < total ? 'Question suivante' : 'Voir le résultat';
      nextBtn.addEventListener('click', () => {
        state.index += 1;
        renderStep();
      });
      navWrap.appendChild(nextBtn);
      navSlot.innerHTML = '';
      navSlot.appendChild(navWrap);
    }

    q.options.forEach((optText, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'quiz-option';
      btn.innerHTML = `<span class="letter">${String.fromCharCode(65 + i)}</span><span>${escapeHtml(optText)}</span>`;
      btn.addEventListener('click', async () => {
        if (btn.disabled) return;
        lockOptions();
        try {
          const result = await apiFetch('/api/quiz/answer', {
            method: 'POST',
            body: { question_id: q.id, selected_index: i },
          });
          q.answered = true;
          q.selected_index = i;
          q.is_correct = result.is_correct;
          showAnsweredState(i, result.correct_index, result.is_correct, result.explanation);
          refreshProgress();
        } catch (e) {
          [...opts.children].forEach((b) => { b.disabled = false; });
        }
      });
      opts.appendChild(btn);
    });

    if (q.answered) {
      showAnsweredState(q.selected_index, q.correct_index, q.is_correct, q.explanation);
    }

    return wrap;
  }

  function recapMessage(correct, total) {
    const pct = total ? Math.round((correct / total) * 100) : 0;
    if (pct === 100) return 'Sans faute ! Tu maîtrises bien ce chapitre.';
    if (pct >= 70) return 'Bon score — encore quelques détails à consolider.';
    if (pct >= 40) return 'Pas mal, mais il reste du travail sur certains points.';
    return 'Ce chapitre mérite d\u2019être retravaillé : regarde tes points faibles ci-dessous.';
  }

  function buildRecap() {
    const wrap = document.createElement('div');
    wrap.className = 'quiz-recap';
    const total = questions.length;
    const correct = questions.filter((q) => q.is_correct).length;
    wrap.innerHTML = `<div class="score">${correct}<span>/${total}</span></div>` +
      `<p class="msg">${recapMessage(correct, total)}</p>`;

    const actions = document.createElement('div');
    actions.className = 'recap-actions';

    const weakBtn = document.createElement('button');
    weakBtn.type = 'button';
    weakBtn.className = 'recap-btn';
    weakBtn.textContent = 'Voir mes points faibles';
    weakBtn.addEventListener('click', () => sendMessage('Quels sont mes points faibles ?'));
    actions.appendChild(weakBtn);

    const againBtn = document.createElement('button');
    againBtn.type = 'button';
    againBtn.className = 'recap-btn';
    againBtn.textContent = 'Refaire un quiz';
    againBtn.addEventListener('click', () => sendMessage('Fais-moi un autre quiz'));
    actions.appendChild(againBtn);

    wrap.appendChild(actions);
    return wrap;
  }

  renderStep();
  return card;
}

/* ------------------------------ Indicateur de saisie ------------------------------ */

function showTyping() {
  hideEmptyState();
  const row = document.createElement('div');
  row.className = 'msg-row from-assistant msg-typing';
  row.id = 'typingIndicator';
  row.innerHTML = '<div class="msg-bubble"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>';
  chatThread.appendChild(row);
  scrollThreadToBottom();
}

function hideTyping() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

/* ------------------------------ Envoi de message ------------------------------ */

async function sendMessage(content) {
  content = (content || '').trim();
  if (!content) return;

  composerInput.value = '';
  autoResizeComposer();

  renderMessage({ role: 'user', message_type: 'text', content });
  scrollThreadToBottom();
  showTyping();
  sendBtn.disabled = true;

  try {
    const data = await apiFetch('/api/chat/message', { method: 'POST', body: { content } });
    hideTyping();
    (data.assistant_messages || []).forEach((m) => renderMessage(m));
    scrollThreadToBottom();
    refreshProgress();
  } catch (e) {
    hideTyping();
  } finally {
    sendBtn.disabled = false;
  }
}

/* ------------------------------ Upload de PDF ------------------------------ */

function openUploadModal(file, docType) {
  pendingUpload = { file, docType };
  modalTitleEl.textContent = docType === 'cours' ? 'Nouveau cours' : 'Nouvel examen passé';
  modalFileName.textContent = file.name;
  modalTitleInput.value = file.name.replace(/\.pdf$/i, '').replace(/[_-]+/g, ' ');
  modalSubjectSelect.value = 'Mathématiques';
  modalSubjectOther.hidden = true;
  modalSubjectOther.value = '';
  modalOverlay.classList.add('open');
  modalTitleInput.focus();
}

function closeUploadModal() {
  modalOverlay.classList.remove('open');
  pendingUpload = null;
}

async function confirmUpload() {
  if (!pendingUpload) return;
  const { file, docType } = pendingUpload;
  const title = modalTitleInput.value.trim() || file.name.replace(/\.pdf$/i, '');
  const subject = modalSubjectSelect.value === 'Autre'
    ? (modalSubjectOther.value.trim() || 'Autre')
    : modalSubjectSelect.value;
  closeUploadModal();

  renderMessage({ role: 'user', message_type: 'text', content: `📎 ${file.name}` });
  scrollThreadToBottom();
  showTyping();

  const formData = new FormData();
  formData.append('pdf', file);
  formData.append('title', title);
  formData.append('subject', subject);
  formData.append('doc_type', docType);

  try {
    const res = await fetch(`${API_BASE}/api/documents/upload`, { method: 'POST', body: formData });
    let data = null;
    try { data = await res.json(); } catch (_) { /* pas de corps JSON */ }
    hideTyping();
    if (!res.ok || !data || data.error) {
      throw new Error((data && data.error) || `Erreur ${res.status}`);
    }
    renderMessage(data.message);
    scrollThreadToBottom();
    await Promise.all([refreshDocuments(), refreshProgress()]);
  } catch (e) {
    hideTyping();
    showToast(e.message || "Échec de l'envoi du PDF.");
  }
}

/* ------------------------------ Barre latérale : documents ------------------------------ */

function fillDocList(container, docs, type) {
  container.innerHTML = '';
  if (!docs.length) {
    const p = document.createElement('p');
    p.className = 'doc-empty';
    p.textContent = type === 'cours' ? "Aucun cours pour l'instant." : "Aucun examen pour l'instant.";
    container.appendChild(p);
    return;
  }

  docs.forEach((doc) => {
    const item = document.createElement('div');
    item.className = 'doc-item';

    const dot = document.createElement('span');
    dot.className = 'doc-dot';
    dot.style.background = colorForSubject(doc.subject);
    item.appendChild(dot);

    const info = document.createElement('div');
    info.className = 'doc-info';
    const pageLabel = doc.page_count === 1 ? '1 page' : `${doc.page_count} pages`;
    info.innerHTML = `<p class="doc-title">${escapeHtml(doc.title)}</p>` +
      `<p class="doc-meta">${escapeHtml(doc.subject || '')} · ${pageLabel}</p>`;
    item.appendChild(info);

    const actions = document.createElement('div');
    actions.className = 'doc-actions';

    const askBtn = document.createElement('button');
    askBtn.type = 'button';
    askBtn.className = 'doc-action';
    askBtn.innerHTML = ICONS.summary;
    if (type === 'cours') {
      askBtn.title = 'Résumer';
      askBtn.addEventListener('click', () => { sendMessage(`Résume le cours ${doc.title}`); closeSidebarOnMobile(); });
    } else {
      askBtn.title = 'Que couvre cet examen ?';
      askBtn.addEventListener('click', () => { sendMessage(`Que couvre l'examen ${doc.title} ?`); closeSidebarOnMobile(); });
    }
    actions.appendChild(askBtn);

    if (type === 'cours') {
      const quizBtn = document.createElement('button');
      quizBtn.type = 'button';
      quizBtn.className = 'doc-action';
      quizBtn.title = 'Générer un quiz';
      quizBtn.innerHTML = ICONS.quiz;
      quizBtn.addEventListener('click', () => { sendMessage(`Fais-moi un quiz sur ${doc.title}`); closeSidebarOnMobile(); });
      actions.appendChild(quizBtn);
    }

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'doc-action danger';
    delBtn.title = 'Supprimer';
    delBtn.innerHTML = ICONS.trash;
    delBtn.addEventListener('click', () => deleteDocument(doc.id, doc.title));
    actions.appendChild(delBtn);

    item.appendChild(actions);
    container.appendChild(item);
  });
}

function renderDocLists(documents) {
  fillDocList(listCours, documents.filter((d) => d.doc_type === 'cours'), 'cours');
  fillDocList(listExamens, documents.filter((d) => d.doc_type === 'examen'), 'examen');
}

async function deleteDocument(id, title) {
  if (!confirm(`Supprimer « ${title} » ? Cette action est définitive.`)) return;
  try {
    await apiFetch(`/api/documents/${id}`, { method: 'DELETE' });
    await refreshDocuments();
  } catch (e) {
    /* le toast d'erreur est déjà affiché par apiFetch */
  }
}

function updateHeaderSub(docs) {
  if (!docs.length) {
    chatHeaderSub.textContent = 'Envoie un cours pour commencer';
    return;
  }
  const nCours = docs.filter((d) => d.doc_type === 'cours').length;
  const nExamens = docs.filter((d) => d.doc_type === 'examen').length;
  const parts = [];
  if (nCours) parts.push(`${nCours} cours`);
  if (nExamens) parts.push(`${nExamens} examen${nExamens > 1 ? 's' : ''}`);
  chatHeaderSub.textContent = `${parts.join(' · ')} en mémoire`;
}

async function refreshDocuments() {
  const docs = await apiFetch('/api/documents');
  renderDocLists(docs);
  updateHeaderSub(docs);
}

/* ------------------------------ Barre latérale : progression ------------------------------ */

function renderProgress(stats) {
  progressBody.innerHTML = '';
  const attempted = stats.filter((s) => s.total > 0);

  if (!attempted.length) {
    const p = document.createElement('p');
    p.className = 'doc-empty';
    p.textContent = 'Fais un quiz pour voir ta progression apparaître ici.';
    progressBody.appendChild(p);
    progressBody.onclick = null;
    return;
  }

  attempted.sort((a, b) => a.accuracy - b.accuracy);
  progressBody.onclick = () => { sendMessage('Quels sont mes points faibles ?'); closeSidebarOnMobile(); };

  attempted.slice(0, 5).forEach((s) => {
    const color = s.accuracy >= 70 ? 'var(--teal-deep)' : (s.accuracy >= 40 ? 'var(--accent-deep)' : 'var(--brick)');
    const row = document.createElement('div');
    row.className = 'progress-row';
    row.innerHTML = `<span class="progress-label">${escapeHtml(s.concept)}</span>` +
      `<span class="progress-track"><span class="progress-fill" style="width:${s.accuracy}%;background:${color}"></span></span>` +
      `<span class="progress-pct">${s.accuracy}%</span>`;
    progressBody.appendChild(row);
  });
}

async function refreshProgress() {
  const stats = await apiFetch('/api/progress');
  renderProgress(stats);
}

/* ------------------------------ État de l'IA ------------------------------ */

async function refreshHealth() {
  try {
    // check=1 : le serveur fait un vrai petit appel à l'IA pour vérifier que la
    // clé ET le modèle fonctionnent (résultat mis en cache côté serveur).
    const data = await apiFetch('/api/health?check=1');
    const available = !!(data.ia && data.ia.available);
    const test = data.ia_test;
    const working = available && !!(test && test.ok);

    aiStatusDot.classList.toggle('on', working);
    aiStatusDot.classList.toggle('off', !working);

    if (!available) {
      aiStatusText.textContent = 'Aucune clé IA : mode simplifié';
    } else if (working) {
      aiStatusText.textContent = `IA active (${data.ia.model})`;
    } else {
      const reason = (test && test.message) || "L'IA ne répond pas.";
      aiStatusText.textContent = 'IA en erreur';
      aiStatusText.title = reason;
      showToast(reason);
    }
  } catch (e) {
    aiStatusText.textContent = 'Serveur injoignable';
  }
}

/* ------------------------------ Historique ------------------------------ */

async function loadHistory() {
  const messages = await apiFetch('/api/chat/history');
  if (!messages.length) return;
  messages.forEach((m) => renderMessage(m));
  scrollThreadToBottom();
}

/* ------------------------------ Barre latérale mobile ------------------------------ */

function toggleSidebar(open) {
  sidebar.classList.toggle('open', open);
  sidebarScrim.classList.toggle('open', open);
}

function closeSidebarOnMobile() {
  if (window.matchMedia('(max-width: 860px)').matches) toggleSidebar(false);
}

/* ------------------------------ Composer ------------------------------ */

function autoResizeComposer() {
  composerInput.style.height = 'auto';
  composerInput.style.height = `${Math.min(composerInput.scrollHeight, 140)}px`;
  sendBtn.classList.toggle('is-ready', composerInput.value.trim().length > 0);
}

/* ------------------------------ Câblage des événements ------------------------------ */

function wireEvents() {
  sendBtn.addEventListener('click', () => sendMessage(composerInput.value));
  composerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(composerInput.value);
    }
  });
  composerInput.addEventListener('input', autoResizeComposer);

  suggestionChips.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => sendMessage(chip.dataset.suggestion));
  });

  btnReset.addEventListener('click', async () => {
    const ok = confirm(
      'Recommencer la conversation ? Tes cours, tes examens et ta progression restent en mémoire : seul le fil de discussion est effacé.'
    );
    if (!ok) return;
    await apiFetch('/api/chat/reset', { method: 'DELETE' });
    chatThread.innerHTML = '';
    chatThread.appendChild(emptyState);
    emptyState.style.display = '';
  });

  sidebarOpen.addEventListener('click', () => toggleSidebar(true));
  sidebarClose.addEventListener('click', () => toggleSidebar(false));
  sidebarScrim.addEventListener('click', () => toggleSidebar(false));

  btnAddCours.addEventListener('click', () => fileInputCours.click());
  btnAddExamen.addEventListener('click', () => fileInputExamen.click());
  fileInputCours.addEventListener('change', () => {
    if (fileInputCours.files[0]) openUploadModal(fileInputCours.files[0], 'cours');
    fileInputCours.value = '';
  });
  fileInputExamen.addEventListener('change', () => {
    if (fileInputExamen.files[0]) openUploadModal(fileInputExamen.files[0], 'examen');
    fileInputExamen.value = '';
  });

  attachBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    attachMenu.classList.toggle('open');
  });
  attachMenu.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      attachMenu.classList.remove('open');
      if (btn.dataset.target === 'cours') fileInputCours.click();
      else fileInputExamen.click();
    });
  });
  document.addEventListener('click', (e) => {
    if (!attachMenu.contains(e.target) && !attachBtn.contains(e.target)) {
      attachMenu.classList.remove('open');
    }
  });

  modalSubjectSelect.addEventListener('change', () => {
    modalSubjectOther.hidden = modalSubjectSelect.value !== 'Autre';
    if (!modalSubjectOther.hidden) modalSubjectOther.focus();
  });
  modalCancel.addEventListener('click', closeUploadModal);
  modalConfirm.addEventListener('click', confirmUpload);
  modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeUploadModal();
  });
  modalTitleInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmUpload(); }
  });
}

/* ------------------------------ Démarrage ------------------------------ */

async function init() {
  wireEvents();
  autoResizeComposer();
  await Promise.all([refreshHealth(), refreshDocuments(), refreshProgress(), loadHistory()]);
}

document.addEventListener('DOMContentLoaded', init);
