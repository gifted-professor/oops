/* Oops H5 UI. Talks to the native shell through window.OopsNative ({type, payload} JSON, see docs/bridge.md).
   In a desktop browser, bridge-mock.js provides a stand-in. Pages use the design prototype's markup and
   class names (proto.css) so they look the same; the data and actions are ours. */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const native = window.OopsNative;
  const send = (type, payload = {}) => native.postMessage(JSON.stringify({ type, payload }));
  const params = new URLSearchParams(location.search);

  const ASR_TEXT = {
    off: ['识别未连接', ''],
    simulated: ['浏览器模拟识别', 'warn'],
    unconfigured: ['未配置识别密钥', 'bad'],
    connecting: ['识别连接中…', 'warn'],
    live: ['实时识别中', 'live'],
    reconnecting: ['识别重连中…', 'warn'],
    error: ['识别出错', 'bad'],
  };
  const SOURCE_TEXT = { mic: '手机麦克风', wav: '测试音频', remote: 'Mac 收音' };
  const TAG_LABELS = { commitment: '待办', decision: '决定', idea: '想法', constraint: '约束', chitchat: '寒暄', uncertain: '待判断' };
  const MANUAL_OTHER = { other: '其他人', multi: '多人同时说', unclear: '听不清' };

  const S = {
    state: { capturing: false, sessionMs: 0, bufferedMs: 0, retentionMs: 300000, asr: 'off', level: 0, savedCount: 0, asrGaps: [] },
    lines: new Map(), // utterance id → transcript card element
    records: new Map(),
    commitments: [],
    ownerEdits: new Map(), // manual selections remain visible when model hints refresh
    summary: {}, notices: new Map(), activeNotices: [],
    sheet: null, card: null, draft: null,
    playingKey: null, loadingKey: null, latency: null, autoScroll: true,
    voiceprintName: '', voiceprintDelete: null,
    team: [],
    route: { name: 'home' },
    liveTab: 'live', meetingTab: 'overview', memoryTab: 'projects',
    history: null, historyItems: null, historyFilter: '', transcriptFilter: { speaker: null, query: '' },
    tasks: { mine: true, status: 'open', items: null, query: '' }, openTaskCount: 0,
    projects: { items: null, open: null, section: 'decisions' }, liveProject: null,
    review: null, pendingReview: null, sessionsQuery: '',
  };

  // ---- helpers --------------------------------------------------------------------------

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const clock = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };
  const hms = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map((n) => String(n).padStart(2, '0')).join(':');
  };
  const mmss = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  };
  const ago = (ms) => {
    const s = Math.max(0, Math.round(ms / 1000));
    return s < 60 ? `${s} 秒前` : `${Math.floor(s / 60)} 分 ${s % 60} 秒前`;
  };
  const speakerLabel = (id) => {
    if (id == null || id === '') return '';
    const n = parseInt(id, 10);
    return Number.isNaN(n) ? String(id) : `说话人 ${String.fromCharCode(65 + n)}`;
  };
  const manualLabel = (v) => MANUAL_OTHER[v] || speakerLabel(v);
  const initial = (name) => (name ? [...String(name)][0] : '?');
  const formatDate = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };
  const shortDate = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };
  const icon = (name, size = 22) => `<img class="icon" alt="" width="${size}" height="${size}" draggable="false" src="icons/${name}.svg">`;
  const meetingName = (m) => m.title || m.display_title || m.goal || '会议';
  const reviewPending = (i) => Object.values(i.review_pending || {}).reduce((a, b) => a + b, 0);
  // Meetings over an hour read 1:23:04, shorter ones 18:24, as in the prototype.
  const duration = (m) => {
    if (!m.ended_at) return '';
    const ms = new Date(m.ended_at) - new Date(m.started_at);
    return ms >= 3600000 ? `${Math.floor(ms / 3600000)}:${mmss(ms % 3600000)}` : mmss(ms);
  };

  let toastTimer = null;
  function toast(message, bad = false) {
    const t = $('toast');
    t.textContent = message;
    t.classList.toggle('bad', bad);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  function applyInsets(i) {
    if (!i) return;
    const root = $('root');
    root.style.setProperty('--mobile-status-bar-height', `${i.top || 0}px`);
    root.style.setProperty('--mobile-safe-area-height', `${i.bottom || 0}px`);
  }
  // ?frame=ios lays the page out like the prototype's iPhone frame, for side-by-side screenshots.
  if (params.get('frame') === 'ios') applyInsets({ top: 54, bottom: 34 });
  else applyInsets({ top: 0, bottom: 0 });

  // ---- messages from native -------------------------------------------------------------

  native.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    try { handle(msg.type, msg.payload || {}); } catch (e) { console.error(e); }
  };

  function handle(type, p) {
    switch (type) {
      case 'snapshot':
        if (params.get('frame') !== 'ios') applyInsets(p.insets);
        resetTranscript();
        S.state.sessionId = p.state.sessionId; // the snapshot already is this session's transcript
        S.team = p.team || S.team;
        (p.transcript || []).forEach(addLine);
        setState(p.state);
        if (p.summary) setSummary(p.summary);
        if (p.pendingActions?.length) {
          openDraft(p.pendingActions[0].draft);
          onActionResult(p.pendingActions[0].result);
        }
        enter();
        render();
        break;
      case 'state': setState(p); break;
      case 'notice': showNotice(p); break;
      case 'history.list': S.historyItems = p.items || []; if (['home', 'sessions'].includes(S.route.name)) render(); if (S.sheet?.kind === 'history') renderSheet(); break;
      case 'history.open': historyOpen(p); break;
      case 'history.delete': toast('会议文字已删除'); closeSheet(); go('sessions'); break;
      case 'history.search': historySearch(p); break;
      case 'history.renamed': onRenamed(p); break;
      case 'projects.list': S.projects.items = p.items || []; if (S.route.name === 'memory') render(); if (S.sheet?.kind === 'projectPick') renderSheet(); break;
      case 'project.open': S.projects.open = p; if (S.route.name === 'project') render(); break;
      case 'meeting.project': onMeetingProject(p); break;
      case 'speaker.corrected': onSpeakerCorrected(p); break;
      case 'review.saved': onReviewSaved(p); break;
      case 'tasks.list':
        S.tasks.items = p.items || [];
        if (p.mine && p.status === 'open') S.openTaskCount = S.tasks.items.length;
        renderNav();
        if (['tasks', 'home'].includes(S.route.name)) render();
        break;
      case 'voiceprint.open': openSheet({ kind: 'voiceprint' }); break;
      case 'insets': if (params.get('frame') !== 'ios') applyInsets(p); break;
      case 'asr.partial':
        $('partial').textContent = p.text ? `… ${p.text}` : '';
        scrollToEnd();
        break;
      case 'asr.final':
        $('partial').textContent = '';
        addLine(p);
        renderLiveRecent();
        break;
      case 'asr.speaker': setLineSpeaker(p.id, p.speaker, p.segments, p.score, p.speakerState, p.speakerPreview, p.candidates, p.trialSegments); break;
      case 'asr.tag':
        if (S.records.has(p.id)) {
          // A tag can land after the speaker answer; it must not reset speaker, split or hint fields.
          S.records.set(p.id, { ...S.records.get(p.id), tag: p.tag, tagProb: p.tagProb, tagEngine: p.tagEngine });
          renderLine(p.id);
        }
        break;
      case 'session.summary': setSummary(p); break;
      case 'transcript.pruned':
        (p.ids || []).forEach(removeLine);
        S.pruned = true;
        break;
      case 'recall.pending':
        setBusy(true);
        openSheet({ kind: 'pending', title: '找回原话', sub: '按时间和语义定位刚才的原话' });
        break;
      case 'recall.card':
        setBusy(false);
        openCard(p);
        break;
      case 'recall.notFound':
        setBusy(false);
        openSheet({ kind: 'notFound', reason: p.reason });
        break;
      case 'clip.state': onClip(p); break;
      case 'recall.saved':
        S.state.savedCount = p.savedCount;
        renderStatus();
        toast('已保存到长期记忆');
        if (S.sheet?.kind === 'card' && S.card?.data.id === p.cardId) closeSheet();
        break;
      case 'action.draft': openDraft(p); break;
      case 'action.suggestion':
        if (window.OopsDraftState.merge(S.draft, p) && S.sheet?.kind === 'draft') renderSheet();
        break;
      case 'action.result': onActionResult(p); break;
      case 'latency': S.latency = p; break;
      case 'saved.items': openSheet({ kind: 'saved', items: p.items || [] }); break;
      case 'toast': toast(p.message); break;
      case 'error': onError(p); break;
      default: break;
    }
  }

  // ---- state ----------------------------------------------------------------------------

  function setState(s) {
    // A new capture session starts an empty timeline; old lines and cards no longer apply.
    if (s.sessionId && s.sessionId !== S.state.sessionId) resetTranscript();
    const before = { capturing: S.state.capturing, sessionId: S.state.sessionId, voiceprint: JSON.stringify(S.state.voiceprint),
      preview: S.state.speakerPreviewEnabled, ready: S.state.sourceReady };
    S.state = { ...S.state, ...s };
    if (s.notices) { s.notices.forEach((n) => S.notices.set(n.id, n)); renderNoticeBanners(); }
    if (before.preview !== S.state.speakerPreviewEnabled) S.records.forEach((_, id) => renderLine(id));
    if (S.sheet?.kind === 'voiceprint' && before.voiceprint !== JSON.stringify(S.state.voiceprint)) renderSheet();
    if (S.route.name === 'memory' && S.memoryTab === 'people' && before.voiceprint !== JSON.stringify(S.state.voiceprint)) render();
    // Starting or stopping changes which controls a page shows; level and time only update text in place.
    if (before.capturing !== S.state.capturing || before.sessionId !== S.state.sessionId || before.ready !== S.state.sourceReady) {
      if (S.state.capturing && !before.capturing && S.route.name !== 'live') go('live');
      else render();
    } else renderStatus();
  }

  function resetTranscript() {
    S.liveProject = null;
    $('lines').innerHTML = '';
    S.lines.clear();
    S.records.clear();
    S.commitments = []; S.summary = {}; S.notices.clear(); S.activeNotices = []; renderNoticeBanners();
    S.ownerEdits.clear();
    $('partial').textContent = '';
    S.pruned = false;
    S.autoScroll = true;
  }

  function setSummary(p) {
    if (p.sessionId !== S.state.sessionId) return;
    p = { ...p, commitments: (p.commitments || []).map((u) => u.reviewStatus === 'confirmed' ? u : { ...u, ...(S.ownerEdits.get(u.id) || {}) }) };
    S.summary = p; S.commitments = p.commitments || [];
    if (S.route.name === 'live' && S.liveTab !== 'transcript') render();
    if (S.sheet?.kind === 'commitments') renderSheet();
  }

  /** Text that changes many times a second (time, status) is patched in place so forms and scroll survive. */
  function renderStatus() {
    const s = S.state;
    const set = (id, text) => { const el = $(id); if (el && el.textContent !== text) el.textContent = text; };
    set('rc-time', mmss(s.sessionMs));
    set('live-elapsed', mmss(s.sessionMs));
    const [text] = liveStatus();
    set('live-rec-title', text);
    set('live-rec-sub', liveDetail());
    set('rc-status', s.capturing ? (s.sourceReady ? '收音中' : '等待收音') : '已停止');
    set('rc-saved', `已保存 ${s.savedCount ?? 0}`);
    const warn = $('live-warn');
    if (warn) { const w = warnings(); warn.hidden = !w.length; warn.textContent = w.join('；'); }
    if (S.route.name === 'settings') set('diag', diagText());
    if (S.sheet?.kind === 'card') {
      const el = $('card-ago');
      if (el) el.textContent = ago(s.sessionMs - S.card.data.items[S.card.sel].endMs);
    }
  }

  function liveStatus() {
    const s = S.state;
    if (!s.capturing) return ['已停止收音', ''];
    const [text, cls] = !s.sourceReady && !['error', 'unconfigured'].includes(s.asr) ? ['等待音源', ''] : (ASR_TEXT[s.asr] || [s.asr, '']);
    return [`正在收音 · ${text}`, cls];
  }
  function liveDetail() {
    const s = S.state;
    const source = s.capturing && s.sourceDetail ? s.sourceDetail : SOURCE_TEXT[s.source] || s.source || '';
    return `${source} · 缓存 ${clock(s.bufferedMs)} / ${clock(s.retentionMs)}`;
  }
  function warnings() {
    const s = S.state, w = [];
    const gaps = s.asrGaps || [];
    if (s.asr === 'unconfigured' || s.asr === 'error') w.push(s.asrDetail || (ASR_TEXT[s.asr] || [s.asr])[0]);
    else if (gaps.length) { const g = gaps[gaps.length - 1]; w.push(`识别中断过 ${gaps.length} 次，最近一次 ${clock(g.fromMs)}–${clock(g.toMs)} 没有转写`); }
    if (s.capturing && s.micBusy) w.push('麦克风被其他应用占用，这段时间没有声音进来');
    if (s.capturing && s.recordingCapped) w.push('录音已达 3 小时上限，之后只保留文字');
    if (!s.memorySync) w.push('会议文字同步已关闭');
    else if (s.syncStatus === 'failed') w.push('文字同步已停止，请检查服务器');
    else if (s.syncStatus === 'unsupported') w.push('当前后端不支持会议文字同步');
    if (s.capturing && s.speakerPreviewEnabled && s.liveOverlap) w.push('疑似多人同时说话（试验检测）');
    return w;
  }

  // ---- live transcript ------------------------------------------------------------------

  function addLine(u) {
    if (S.lines.has(u.id)) return;
    // A lone interjection ('嗯', '呃') carries nothing; it stays in the archive and the sync, just not on screen.
    if (u.filler) return;
    const card = document.createElement('div');
    card.className = 'card clickable session-transcript live-line';
    card.dataset.id = u.id;
    card.dataset.start = u.startMs;
    const list = $('lines');
    // Keep time order: finals may interleave after an ASR reconnect.
    const next = [...list.children].find((el) => Number(el.dataset.start) > u.startMs) || null;
    list.insertBefore(card, next);
    S.lines.set(u.id, card);
    S.records.set(u.id, u);
    renderLine(u.id);
    scrollToEnd();
  }

  /** The voiceprint answer arrives after the caption; replace the name in place. */
  function setLineSpeaker(id, speaker, segments = [], score = null, state = null, preview = null, candidates = [], trial = []) {
    const u = S.records.get(id);
    if (!u) return;
    Object.assign(u, { speaker, speakerCandidates: candidates || [], trialSegments: trial || [], segments, speakerScore: score,
      speakerState: state, speakerPreview: preview });
    renderLine(id);
    renderLiveRecent();
  }

  // Below-threshold voiceprint ranking, shown only as a grey guess; it never fills an owner.
  const HINT_FLOOR = 0.35, HINT_MARGIN = 0.05;
  function speakerGuess(u) {
    const c = (u.speakerCandidates || []).filter((x) => x && typeof x.score === 'number' && Number.isFinite(x.score));
    if (u.speakerState !== 'unconfirmed' || !c.length || c[0].score < HINT_FLOOR) return '';
    return c.length < 2 || c[0].score - c[1].score >= HINT_MARGIN
      ? `可能是 ${speakerLabel(c[0].name)}` : `${speakerLabel(c[0].name)} / ${speakerLabel(c[1].name)}？`;
  }
  function trialGuess(s) {
    const c = (s.candidates || []).filter((x) => x && typeof x.score === 'number' && Number.isFinite(x.score));
    return c.length && c[0].score >= HINT_FLOOR && (c.length < 2 || c[0].score - c[1].score >= HINT_MARGIN)
      ? `可能是 ${speakerLabel(c[0].name)}` : '身份待确认';
  }
  const SPEAKER_STATE = { pending: '声纹识别中', unconfirmed: '身份待确认', too_short: '语音太短', busy: '声纹请求拥挤',
    audio_unavailable: '声纹音频不可用', unsupported: '声纹服务不可用', error: '声纹请求失败' };

  /** One transcript card: who (with how sure), when, what, and the value tag. */
  function lineHtml(u) {
    const name = u.manualSpeaker ? manualLabel(u.manualSpeaker) : speakerLabel(u.speaker);
    const who = u.manualSpeaker ? `${name} · 已改` : name ? `${name}${u.segments?.some((s) => s.tier === 'A') ? '' : ' · 待核对'}` :
      speakerGuess(u) || SPEAKER_STATE[u.speakerState] || '身份待确认';
    // A joined phone number no longer matches the segments' text, so such a line is shown whole.
    const stitched = !!u.display && u.display !== u.text;
    let body;
    if (!u.manualSpeaker && u.segments?.length && !stitched) {
      body = u.segments.map((s) => {
        const status = s.overlap ? '多人同时说' : s.tier === 'A' ? '高可信' : '建议 · 待核对';
        const names = s.overlap ? (s.candidates || []).map((c) => c.name).join(' / ') : speakerLabel(s.speaker) || '身份待确认';
        return `<span class="speaker-segment"><span class="segment-who">${esc(names)} · ${status}</span>${esc(s.text)}</span>`;
      }).join('');
    } else if (!u.manualSpeaker && u.trialSegments?.length > 1 && !stitched) {
      body = `<span class="segment-note">按人拆分（试验 · 建议）</span>` + u.trialSegments.map((s) => {
        const names = s.overlap ? (s.candidates || []).map((c) => speakerLabel(c.name)).join(' / ') + ' · 多人同时说'
          : s.speaker ? `${speakerLabel(s.speaker)} · 建议` : trialGuess(s);
        return `<span class="speaker-segment"><span class="segment-who">${esc(names)}</span>${esc(s.text)}</span>`;
      }).join('');
    } else body = esc(u.display || u.text);
    const tag = TAG_LABELS[u.tag] && !['chitchat', 'uncertain'].includes(u.tag) ? ` <span class="badge ${u.tag === 'decision' ? 'green' : u.tag === 'commitment' ? 'purple' : 'gray'}">${TAG_LABELS[u.tag]}</span>` : '';
    return `<div class="session-speaker"><span class="avatar">${esc(name ? initial(name) : '?')}</span><strong class="${u.manualSpeaker ? 'manual-name' : name ? '' : 'unknown-name'}">${esc(who)}</strong><span>${hms(u.startMs)}</span></div>
      <p>${body}${tag}</p>`;
  }
  function renderLine(id) {
    const u = S.records.get(id), el = S.lines.get(id);
    if (!u || !el) return;
    el.innerHTML = lineHtml(u);
    el.hidden = !!(S.transcriptFilter.speaker && (u.manualSpeaker || u.speaker) !== S.transcriptFilter.speaker);
  }
  function removeLine(id) {
    const el = S.lines.get(id);
    if (el) el.remove();
    S.lines.delete(id);
    S.records.delete(id);
  }
  function highlight(ids) {
    ids.forEach((id, i) => {
      const el = S.lines.get(id);
      if (!el) return;
      el.classList.add('hit');
      if (i === 0 && el.isConnected) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(() => el.classList.remove('hit'), 2600);
    });
  }
  function scrollToEnd() {
    if (!S.autoScroll || S.route.name !== 'live' || S.liveTab !== 'transcript') return;
    const t = $('scroll');
    t.scrollTop = t.scrollHeight;
  }
  $('scroll').addEventListener('scroll', () => {
    const t = $('scroll');
    S.autoScroll = t.scrollHeight - t.scrollTop - t.clientHeight < 80;
  });
  function setBusy(busy) {
    const b = $('rc-recall');
    if (b) b.classList.toggle('busy', busy);
  }
  /** The live overview shows the last few sentences; it is redrawn as they arrive. */
  function renderLiveRecent() {
    const box = $('live-recent');
    if (!box || S.route.name !== 'live' || S.liveTab !== 'live') return;
    const recent = [...S.records.values()].filter((u) => !u.filler).sort((a, b) => a.startMs - b.startMs).slice(-3);
    box.innerHTML = recent.map((u) => `<div><button class="card clickable session-transcript" data-act="line-speaker" data-id="${esc(u.id)}">${lineHtml(u)}</button></div>`).join('') ||
      `<p class="meta">${S.state.capturing ? '开始说话后，原话会出现在这里。' : '开始收音后，最近 5 分钟的对话会出现在这里；事后点「找回刚才」就能找回原话。'}</p>`;
  }

  // ---- routing ----------------------------------------------------------------------------

  const NAV = [['home', '首页', 'house'], ['sessions', '会话', 'chat-circle'], ['tasks', '任务', 'check-square'], ['memory', '记忆', 'bookmark-simple'], ['me', '我的', 'user']];
  const NAV_OF = { home: 'home', sessions: 'sessions', live: 'sessions', meeting: 'sessions', tasks: 'tasks', memory: 'memory', project: 'memory', me: 'me', settings: 'me' };

  function go(name, extra = {}) {
    if (S.sheet && S.sheet.kind !== 'pending') closeSheet();
    S.route = { name, ...extra };
    enter();
    render();
    $('scroll').scrollTop = 0;
  }
  /** What a page needs from native or the server each time it is opened. */
  function enter() {
    const r = S.route.name;
    if (r === 'home') { send('history.list'); if (S.state.selfName) send('tasks.list', { mine: true, status: 'open' }); }
    if (r === 'sessions') { S.historyItems = S.historyItems || null; send('history.list'); }
    if (r === 'tasks') openTasks();
    if (r === 'memory') { if (S.memoryTab === 'projects') { S.projects.items = null; send('projects.list'); } else send('voiceprint.list'); }
    if (r === 'live') { S.transcriptFilter = { speaker: null, query: '' }; S.autoScroll = true; }
  }

  function render() {
    const r = S.route.name;
    const root = $('root');
    const tabs = ['live', 'meeting'].includes(r);
    root.dataset.route = r;
    root.className = `oops-root ${r === 'home' ? 'is-home' : ''} ${tabs ? 'has-session-tabs' : ''} ${S.state.capturing ? 'has-recording-controls' : ''}`;
    $('hdr').className = `app-header ${r === 'home' ? 'home-header' : ''}`;
    $('hdr').innerHTML = headerHtml(r);
    $('tabs').hidden = !tabs;
    if (tabs) $('tabs').innerHTML = tabsHtml(r);
    const html = ({ home: homeHtml, sessions: sessionsHtml, live: liveHtml, meeting: meetingHtml, tasks: tasksHtml,
      memory: memoryHtml, project: projectHtml, me: meHtml, settings: settingsHtml })[r];
    $('screen').setAttribute('aria-label', NAV.find((n) => n[0] === NAV_OF[r])?.[1] || '');
    $('screen').className = `screen-content ${r === 'home' ? 'home-main' : ''}`;
    $('screen').innerHTML = html ? html() : '';
    mountTranscript();
    renderLiveRecent();
    renderNav();
    renderRecordingControls();
    renderStatus();
  }

  function mountTranscript() {
    const slot = $('slot-lines');
    if (!slot) { $('holder').appendChild($('lines')); $('holder').appendChild($('partial')); return; }
    slot.appendChild($('lines'));
    slot.appendChild($('partial'));
    S.records.forEach((_, id) => renderLine(id));
    scrollToEnd();
  }

  function headerHtml(r) {
    const button = (act, label, name, size = 22) => `<button class="icon-button" data-act="${act}" aria-label="${label}">${icon(name, size)}</button>`;
    const spacer = '<span class="icon-button" aria-hidden="true"></span>';
    if (r === 'home') {
      const unread = [...S.notices.values()].some((n) => !n.dismissed);
      return `<img class="wordmark" alt="Oops" src="brand/wordmark.png"><div class="header-tools">
        <button class="icon-button notification-button" data-act="notices-open" aria-label="提醒">${icon('bell', 25)}${unread ? '<i></i>' : ''}</button></div>`;
    }
    const title = { sessions: '会话', live: '会话', meeting: '会话', tasks: '任务', memory: '记忆', project: '项目', me: '我的', settings: '收音与服务' }[r];
    const left = ['live', 'meeting', 'project', 'settings'].includes(r) ? button('back', '返回上一页', 'caret-left', 23)
      : button('ask-open', '搜索全部内容', 'magnifying-glass', 23);
    const right = r === 'sessions' || r === 'live' || r === 'meeting' ? (S.state.capturing ? spacer : button('capture-start', '新建记录', 'plus'))
      : ['memory', 'me', 'project', 'settings'].includes(r) ? button('go', '回到首页', 'house').replace('data-act="go"', 'data-act="go" data-route="home"') : spacer;
    return `${left}<h1>${title}</h1>${right}`;
  }

  function tabsHtml(r) {
    const items = r === 'live' ? [['live', '现场'], ['transcript', '原文'], ['actions', '行动']] : [['overview', '概览'], ['transcript', '原文'], ['actions', '行动']];
    const current = r === 'live' ? S.liveTab : S.meetingTab;
    return `<div class="segmented" role="tablist">${items.map(([k, label]) => `<button role="tab" aria-selected="${k === current}" data-act="tab" data-tab="${k}">${label}</button>`).join('')}</div>`;
  }

  function renderNav() {
    const current = NAV_OF[S.route.name];
    $('nav').innerHTML = NAV.map(([k, label, ic]) => `<button aria-label="${label}" class="${k === current ? 'selected' : ''}" ${k === current ? 'aria-current="page"' : ''} data-act="go" data-route="${k}">${icon(ic, 25)}<span>${label}</span>${k === 'tasks' && S.openTaskCount ? `<b>${S.openTaskCount}</b>` : ''}</button>`).join('');
  }

  function renderRecordingControls() {
    const rc = $('rc');
    rc.hidden = !S.state.capturing;
    if (!S.state.capturing) { rc.innerHTML = ''; return; }
    rc.innerHTML = `<div class="rc-heading"><span class="rc-dot ${S.state.sourceReady ? '' : 'paused'}"></span>
        <div class="rc-session"><strong>${esc(liveTitle())}</strong><span><span id="rc-status">${S.state.sourceReady ? '收音中' : '等待收音'}</span> · <time id="rc-time">${mmss(S.state.sessionMs)}</time></span></div>
        ${S.route.name === 'live' && S.liveTab === 'live' ? '' : `<button class="rc-scene" data-act="go" data-route="live">现场${icon('caret-right', 13)}</button>`}</div>
      <div class="rc-actions">
        <button id="rc-recall" data-act="recall">${icon('clock-counter-clockwise', 19)}<span>找回刚才</span></button>
        <button data-act="saved-list">${icon('bookmark-simple', 19)}<span id="rc-saved">已保存 ${S.state.savedCount ?? 0}</span></button>
        <button class="rc-end" data-act="capture-stop">${icon('stop', 19)}<span>结束</span></button></div>`;
  }

  function liveTitle() {
    const a = S.state.agenda || {};
    if (a.goal) return a.goal;
    if (!S.state.sessionId) return '本场会议';
    // Native reports elapsed session time, not the clock time it began; good to the minute.
    S.liveStartedAt = S.liveStartedAt?.id === S.state.sessionId ? S.liveStartedAt : { id: S.state.sessionId, at: new Date(Date.now() - (S.state.sessionMs || 0)) };
    return `会议 · ${shortDate(S.liveStartedAt.at.toISOString())}`;
  }

  // ---- 首页 ---------------------------------------------------------------------------------

  const listRow = ({ act, data = '', ic, title, sub, badge = '', chevron = true, cls = '' }) =>
    `<button class="list-row ${cls}" data-act="${act}" ${data}><span class="row-icon">${icon(ic)}</span><span class="row-copy"><strong>${esc(title)}</strong>${sub ? `<small>${esc(sub)}</small>` : ''}</span>${badge}${chevron ? icon('caret-right', 18) : ''}</button>`;
  const sectionTitle = (title, action = '', act = '', data = '') =>
    `<div class="section-title"><h2>${title}</h2>${action ? `<button class="text-action" data-act="${act}" ${data}>${esc(action)}${icon('caret-right', 14)}</button>` : ''}</div>`;

  function homeHtml() {
    const now = new Date();
    const week = '日一二三四五六'[now.getDay()];
    const recent = (S.historyItems || []).slice(0, 2);
    const live = S.state.sessionId && (S.state.capturing || S.records.size);
    const tasks = (S.state.selfName ? (S.tasks.items || []) : []).filter((t) => ['open', 'unknown'].includes(t.feishu?.state)).slice(0, 3);
    const pendingMeeting = (S.historyItems || []).find((i) => reviewPending(i));
    const pendingTotal = (S.historyItems || []).reduce((a, i) => a + reviewPending(i), 0);
    return `<div class="home-content">
      <div class="home-date">${now.getMonth() + 1}/${now.getDate()}星期${week}</div>
      <div class="home-hero"><h1>今天想<br>一起解决什么？</h1><img alt="Oops伙伴" src="brand/mascot.png"></div>
      <div class="ask-card">
        <textarea id="home-ask" aria-label="想问 Oops 的问题" placeholder="问问刚才或往期会议里说过什么…" rows="2" maxlength="500"></textarea>
        <div class="ask-footer"><small>${icon('lock-key', 13)}团队会议原话</small>
          <button class="input-text" data-act="home-ask-scope">${S.homeAskHistory ? '查往期会议' : '查本场'}</button>
          <button class="mic-button" data-act="home-ask" aria-label="提问">${icon('paper-plane-tilt', 23)}</button></div></div>
      ${S.state.capturing ? `<button class="button primary" type="button" data-act="go" data-route="live">${icon('waveform')}<span>回到正在进行的记录</span></button>`
        : `<button class="button primary" type="button" data-act="capture-start">${icon('microphone')}<span>开始记录</span></button>`}
      <div class="record-under"><span>会议 · ${esc(SOURCE_TEXT[S.state.source] || '手机麦克风')}</span>
        <button data-act="saved-list">${icon('bookmark-simple', 18)}已保存 ${S.state.savedCount ?? 0} 段</button></div>
      ${listRow({ act: 'go', data: 'data-route="settings"', ic: 'waveform', title: '收音与唤醒', sub: `${SOURCE_TEXT[S.state.source] || '手机麦克风'} · ${S.state.voiceTrigger !== false ? '说「Oops」找回刚才' : '语音唤醒已关闭'}` })}
      ${sectionTitle('接着聊', '全部', 'go', 'data-route="sessions"')}
      <div class="home-recent">
        ${live ? listRow({ act: 'go', data: 'data-route="live"', ic: 'waveform', title: liveTitle(), sub: `${S.state.capturing ? '正在收音' : '本场已停止'} · ${S.commitments.length}项待办` }) : ''}
        ${recent.map((m) => listRow({ act: 'meeting-open', data: `data-id="${esc(m.session_id)}"`, ic: 'users-three', title: meetingName(m),
          sub: `${formatDate(m.started_at)} · ${reviewPending(m) ? `${reviewPending(m)}项待核对` : `${m.utterance_count}句`}` })).join('')}
        ${!live && !recent.length ? `<p class="meta">${S.historyItems ? '还没有记录。开始记录后，会议会出现在这里。' : '正在读取…'}</p>` : ''}
      </div>
      ${sectionTitle('需要你处理', S.state.selfName ? `${tasks.length + (pendingMeeting ? 1 : 0)}项待处理` : '选择本人', 'go', `data-route="${S.state.selfName ? 'tasks' : 'me'}"`)}
      ${tasks.map((t) => listRow({ act: 'task-open', data: `data-url="${esc(t.url || '')}"`, ic: 'check-square', title: t.what,
        sub: t.overdue ? '期限已到，核对进度' : t.feishu?.due_ms ? `截止 ${shortDate(new Date(t.feishu.due_ms).toISOString()).split(' ')[0]}` : '在飞书里推进' })).join('')}
      ${pendingMeeting ? listRow({ act: 'review-start', data: `data-id="${esc(pendingMeeting.session_id)}"`, ic: 'check-circle', title: `会后核对 · ${meetingName(pendingMeeting)}`,
        sub: `${reviewPending(pendingMeeting)}项待核对（待办、决定、说话人）` }) : ''}
      ${!tasks.length && !pendingMeeting ? `<p class="meta">${S.state.selfName ? '没有待处理的事。' : '在「我的」里选择本人后，这里会列出你负责的飞书任务。'}</p>` : ''}
      ${sectionTitle('刚才说过什么？', '问已有记录', 'ask-open')}
      <button class="card clickable recall-entry" data-act="recall">${icon('clock-counter-clockwise')}<div><strong>找回最近5分钟</strong><p>选择重要的片段，再决定是否留下</p></div>${icon('caret-right', 17)}</button>
      ${pendingTotal ? listRow({ act: 'go', data: 'data-route="sessions"', ic: 'bookmark-simple', title: '核对会后结果', sub: `${pendingTotal}项待核对 · 确认后才会用于冲突提醒` }) : ''}
    </div>`;
  }

  // ---- 会话列表 ------------------------------------------------------------------------------

  function sessionCard(m) {
    const pending = reviewPending(m);
    const counts = m.digest_counts || {};
    const preview = [counts.decision ? `决定 ${counts.decision}` : '', counts.commitment ? `待办 ${counts.commitment}` : '', counts.constraint ? `约束 ${counts.constraint}` : ''].filter(Boolean).join(' · ') || `${m.utterance_count} 句原话`;
    return `<div class="session-list-item"><button class="card clickable" data-act="meeting-open" data-id="${esc(m.session_id)}">
      <div class="session-card-top"><span class="session-kind">${icon('users-three')}</span>${pending ? `<span class="badge amber">待核对 ${pending}</span>` : '<span class="badge gray">已保存</span>'}</div>
      <h3>${esc(meetingName(m))}</h3>
      <p class="meta">${formatDate(m.started_at)}${duration(m) ? ` · ${duration(m)}` : ''} · 会议</p>
      <p class="session-preview">${esc(preview)}</p>
      <div class="session-card-bottom"><span>${esc(m.project || '未归项目')}</span><strong>${pending ? '查看并核对' : '查看'}</strong></div></button>
      <button class="session-more" data-act="meeting-tools" data-id="${esc(m.session_id)}" aria-label="管理${esc(meetingName(m))}">${icon('dots-three')}</button></div>`;
  }

  function sessionsHtml() {
    const all = S.historyItems;
    const q = S.sessionsQuery.trim();
    const items = (all || []).filter((m) => !q || meetingName(m).includes(q) || (m.project || '').includes(q));
    const pending = items.filter((m) => reviewPending(m)), done = items.filter((m) => !reviewPending(m));
    const live = S.state.sessionId && (S.state.capturing || S.records.size);
    return `<div class="stack sessions-feature">
      ${S.state.capturing ? `<button class="button primary" type="button" data-act="go" data-route="live">${icon('waveform')}<span>回到正在进行的记录</span></button>`
        : `<button class="button primary" type="button" data-act="capture-start">${icon('microphone')}<span>开始记录</span></button>`}
      <div class="session-list-search"><div class="search-input">${icon('magnifying-glass', 18)}<input id="sessions-search" aria-label="搜索记录" placeholder="搜索会议名称或项目" value="${esc(S.sessionsQuery)}"></div>
        <button class="session-filter-button" data-act="ask-open" aria-label="问已有记录">${icon('quotes')}</button></div>
      <div class="session-list-secondary"><button data-act="saved-list">${icon('bookmark-simple', 18)}已保存片段</button><span>${all ? `${all.length}段记录` : ''}</span></div>
      ${live ? `<section class="session-list-group">${sectionTitle('正在记录')}<div class="session-list-item"><button class="card clickable" data-act="go" data-route="live">
        <div class="session-card-top"><span class="session-kind">${icon('waveform')}</span><span class="badge purple">${S.state.capturing ? '进行中' : '已停止'}</span></div>
        <h3>${esc(liveTitle())}</h3><p class="meta">${S.state.capturing ? `收音中 · ${mmss(S.state.sessionMs)}` : '本场已停止'} · 会议</p>
        <p class="session-preview">${S.records.size} 句原话 · 待办 ${S.commitments.length}</p>
        <div class="session-card-bottom"><span>${esc(S.liveProject || '未归项目')}</span><strong>${S.state.capturing ? '回到现场' : '查看'}</strong></div></button></div></section>` : ''}
      ${all == null ? spinnerHtml('正在读取会议…', '团队服务器') : `
        ${pending.length ? `<section class="session-list-group">${sectionTitle('待核对')}${pending.map(sessionCard).join('')}</section>` : ''}
        ${done.length ? `<section class="session-list-group">${sectionTitle('已完成')}${done.map(sessionCard).join('')}</section>` : ''}
        ${!items.length ? `<div class="empty-state"><span>${icon('chat-circle-text', 28)}</span><strong>${q ? '没有找到' : '还没有会议'}</strong><p>${q ? '换个名称或项目再试试。' : '开始记录后，会议文字会同步到团队服务器，出现在这里。'}</p></div>` : ''}`}
      <p class="meta">团队共享。会议文字与原始录音长期保留在团队服务器，删除会议时一并删除。</p>
    </div>`;
  }

  // ---- 现场（本场会议） ------------------------------------------------------------------------

  function sessionHeading(title, meta, act) {
    return `<div class="session-heading"><div><h2>${esc(title)}</h2><p class="meta">${meta}</p></div>
      <button class="session-filter-button" data-act="${act}" aria-label="本次会话工具">${icon('dots-three')}</button></div>`;
  }
  const p0Grid = (items) => `<section class="card session-p0-entry"><div class="session-p0-grid">${items.map(([ic, label, act, data = '']) =>
    `<button data-act="${act}" ${data}>${icon(ic)}<span>${label}</span></button>`).join('')}</div></section>`;

  function liveHtml() {
    const s = S.state;
    const heading = sessionHeading(liveTitle(), `会议 · ${esc(S.liveProject || '个人记录')} · <span id="live-elapsed">${mmss(s.sessionMs)}</span>`, 'live-tools');
    const stage = `<div class="session-stage"><span class="badge ${s.capturing ? 'purple' : 'gray'}">${s.capturing ? '进行中' : s.sessionId ? '已停止 · 可会后核对' : '未开始'}</span></div>`;
    if (S.liveTab === 'transcript') return liveTranscriptHtml(heading, stage);
    if (S.liveTab === 'actions') return liveActionsHtml(heading, stage);
    const counts = s.digestCounts || {};
    const notices = [...S.notices.values()].filter((n) => !n.dismissed).length;
    const speakers = new Set([...S.records.values()].map((u) => u.manualSpeaker || u.speaker).filter(Boolean));
    const a = s.agenda || {};
    const current = (a.items || []).find((i) => i.id === a.currentItemId);
    const covered = (a.items || []).filter((i) => i.coveredMs > 0).length;
    const w = warnings();
    return `<div class="stack sessions-feature">${heading}${stage}
      ${p0Grid([['check-circle', '本场纪要', 'commitments-open'], ['bell', notices ? `会议提醒 ${notices}` : '会议提醒', 'notices-open'],
        ['list-checks', '议程与时间', 'agenda-open'], ['folder-simple', S.liveProject ? `项目：${S.liveProject}` : '所属项目', 'live-project']])}
      <div class="session-recording"><span class="recording-dot ${s.capturing && s.sourceReady ? '' : 'idle'}"></span>
        <div><strong id="live-rec-title">${esc(liveStatus()[0])}</strong><small id="live-rec-sub">${esc(liveDetail())}</small>
          <small class="live-warn" id="live-warn" ${w.length ? '' : 'hidden'}>${esc(w.join('；'))}</small></div>
        ${s.capturing ? '' : `<button class="button secondary" type="button" data-act="capture-start">${icon('microphone', 18)}<span>开始</span></button>`}</div>
      <div class="session-live-summary">
        <button data-act="tab" data-tab="transcript">${icon('user-circle')}<strong>${speakers.size ? `${speakers.size}位发言人` : '发言人待识别'}</strong></button>
        <button data-act="tab" data-tab="actions">${icon('check-square')}<strong>本次行动 ${counts.commitment || 0}</strong></button></div>
      ${listRow({ act: 'agenda-open', ic: 'list-checks', title: '本次议程', sub: a.goal || a.items?.length
        ? `${covered}/${a.items?.length || 0} 项已讨论 · ${current?.title || a.goal || '等待讨论'}${a.plannedMs ? ` · 计划 ${clock(a.plannedMs)}` : ''}` : '没有预设议题，也可以继续记录' })}
      ${sectionTitle('本次讨论', '完整原文', 'tab', 'data-tab="transcript"')}
      <div class="stack live-recent" id="live-recent"></div>
      <div class="action-grid">
        <button class="button secondary" type="button" data-act="ask-open">${icon('magnifying-glass')}<span>问本场</span></button>
        <button class="button secondary" type="button" data-act="saved-list">${icon('bookmark-simple')}<span>已保存 ${s.savedCount ?? 0}</span></button></div>
      ${!s.capturing && s.sessionId ? `<section class="card session-review-entry"><div><h3>会后核对</h3><p class="meta">逐条确认待办、决定和不知道谁说的话；确认后的决定会用于之后的冲突提醒。</p></div>
        <button class="button primary" type="button" data-act="review-start" data-id="${esc(s.sessionId)}"><span>开始 / 继续核对</span></button></section>` : ''}
      ${s.capturing ? '<button class="button quiet" type="button" data-act="capture-stop"><span>结束当前记录</span></button>' : ''}
    </div>`;
  }

  function speakerChips(names, selected) {
    return `<div class="mobile-carousel chip-carousel" role="region" aria-label="筛选"><div class="mobile-carousel-content chip-track">
      <button class="chip ${!selected ? 'selected' : ''}" aria-pressed="${!selected}" data-act="filter-speaker" data-name="">全部</button>
      ${names.map((n) => `<button class="chip ${selected === n ? 'selected' : ''}" aria-pressed="${selected === n}" data-act="filter-speaker" data-name="${esc(n)}">${esc(manualLabel(n))}</button>`).join('')}</div></div>`;
  }

  function liveTranscriptHtml(heading, stage) {
    const names = [...new Set([...S.records.values()].map((u) => u.manualSpeaker || u.speaker).filter(Boolean))];
    return `<div class="stack sessions-feature">${heading}${stage}
      <div class="search-input">${icon('magnifying-glass', 18)}<input id="live-ask" aria-label="问这场会" placeholder="问这场会：预算上限是多少？" maxlength="500"></div>
      ${names.length ? speakerChips(names, S.transcriptFilter.speaker) : ''}
      <p class="meta">点一句可以改说话人。${S.pruned ? '更早的字幕已移出列表，本场纪要仍可查看。' : '最近 5 分钟的原声可以在「找回刚才」里回听。'}</p>
      <div class="stack" id="slot-lines"></div>
      ${!S.records.size ? `<div class="empty-state"><span>${icon('waveform', 28)}</span><strong>还没有原话</strong><p>${S.state.capturing ? '开始说话后，转写会一句句出现在这里。' : '开始收音后，最近 5 分钟的对话会出现在这里。'}</p></div>` : ''}
      ${S.state.capturing ? '<button class="button quiet" type="button" data-act="capture-stop"><span>结束当前记录</span></button>' : ''}
    </div>`;
  }

  function commitmentCard(u, act) {
    const reviewed = u.reviewStatus === 'confirmed' || u.review_status === 'confirmed' || u.review?.verdict === 'confirmed';
    const created = u.taskStatus === 'created' || u.task_status === 'created';
    const owner = u.review?.owner || u.ownerHint || u.owner_hint || u.suggestedOwner || u.suggested_owner;
    const badge = created ? '<span class="badge green">已建飞书任务</span>' : reviewed ? '<span class="badge purple">人工已确认</span>' : '<span class="badge gray">待核对</span>';
    return `<button class="card clickable" data-act="${act}" data-id="${esc(u.id)}"><div class="session-task-top"><h3>${esc(u.text)}</h3>${badge}</div>
      <p class="meta">${esc(owner || '负责人待定')} · ${esc(u.whenRaw || u.when_raw || '期限未定')}</p>
      <p class="meta">${hms(u.startMs ?? u.start_ms)} · ${esc(speakerLabel(u.speaker) || '说话人待确认')}${created ? '' : ' · 点开可建飞书任务'}</p></button>`;
  }

  function liveActionsHtml(heading, stage) {
    const rows = S.commitments;
    const created = rows.filter((u) => u.taskStatus === 'created'), open = rows.filter((u) => u.taskStatus !== 'created');
    return `<div class="stack sessions-feature">${heading}${stage}
      ${listRow({ act: 'commitments-open', ic: 'sparkle', title: '识别到的任务建议', sub: '按原话核对负责人、期限，确认后才建飞书任务' })}
      ${open.length ? `<section class="session-list-group">${sectionTitle('待承接')}${open.map((u) => commitmentCard(u, 'commitment-open')).join('')}</section>` : ''}
      ${created.length ? `<section class="session-list-group">${sectionTitle('已建任务')}${created.map((u) => commitmentCard(u, 'commitment-open')).join('')}</section>` : ''}
      ${!rows.length ? `<div class="empty-state"><span>${icon('check-square', 28)}</span><strong>还没有识别到待办</strong><p>有人说"我来做""你周五前给我"这类话时，会出现在这里。</p></div>` : ''}
      <button class="button primary" type="button" data-act="recall">${icon('clock-counter-clockwise')}<span>从刚才的话整理行动</span></button>
      <button class="button quiet" type="button" data-act="go" data-route="tasks"><span>查看全部任务</span></button>
    </div>`;
  }

  // ---- 往期会议 ------------------------------------------------------------------------------

  function meetingHtml() {
    const h = S.history;
    if (!h) return spinnerHtml('正在打开会议…', '团队服务器');
    const pending = (h.review_queue || []).length;
    const heading = sessionHeading(meetingName(h), `会议 · ${esc(h.project || '未归项目')} · ${formatDate(h.started_at)}${duration(h) ? ` · ${duration(h)}` : ''}`, 'meeting-tools');
    const stage = `<div class="session-stage"><span class="badge ${pending ? 'gray' : 'green'}">${pending ? '已保存 · 待核对' : '已保存 · 已核对'}</span></div>`;
    if (S.meetingTab === 'transcript') return meetingTranscriptHtml(heading, stage);
    if (S.meetingTab === 'actions') return meetingActionsHtml(heading, stage);
    const decisions = (h.digest?.decision || []), constraints = (h.digest?.constraint || []);
    const confirmed = [...decisions, ...constraints].filter((u) => u.review?.verdict === 'confirmed');
    const unconfirmed = [...decisions, ...constraints].filter((u) => u.review?.verdict !== 'confirmed');
    const todos = h.digest?.commitment || [];
    return `<div class="stack sessions-feature">${heading}${stage}
      ${p0Grid([['sparkle', pending ? `会后核对 ${pending}` : '会后核对', 'review-start', `data-id="${esc(h.session_id)}"`], ['bell', `会议提醒 ${(h.notices || []).length}`, 'meeting-notices'],
        ['quotes', '全文与说话人', 'tab', 'data-tab="transcript"'], ['folder-simple', h.project ? `项目：${h.project}` : '所属项目', 'history-project']])}
      <div class="stack session-overview">
        <section class="card session-result-card">
          <div class="session-task-top"><span class="badge gray">${pending ? `仍有${pending}项待核对` : '已全部核对'}</span><button class="text-action" data-act="review-start" data-id="${esc(h.session_id)}">核对</button></div>
          <h3>本次确认的结论</h3>
          ${confirmed.length ? confirmed.map((u) => `<p class="confirmed-line"><span class="badge ${u.tag === 'constraint' ? 'gray' : 'green'}">${u.tag === 'constraint' ? '约束' : '决定'}</span> ${esc(u.text)}</p>`).join('')
            : '<p class="meta">还没有人工确认的结论。会后核对里点「是决定」后会出现在这里，并用于之后开会时的冲突提醒。</p>'}
          ${unconfirmed.length ? `<details class="session-details"><summary>已有整理 · 尚待核对</summary>${unconfirmed.map((u) => `<p>${esc(u.text)}</p>`).join('')}</details>` : ''}
        </section>
        ${sectionTitle('下一步行动')}
        ${todos.map((u) => listRow({ act: 'history-quote', data: `data-id="${esc(u.id)}"`, ic: 'check-square', title: u.text,
          sub: `${u.review?.owner || u.owner_hint || '负责人待定'} · ${u.when_raw || '期限待定'} · ${u.task_status === 'created' ? '已建飞书任务' : u.review?.verdict === 'confirmed' ? '人工已确认' : '待核对'}`,
          badge: `<span class="badge ${u.task_status === 'created' ? 'green' : 'purple'}">${u.task_status === 'created' ? '已建任务' : '待承接'}</span>` })).join('') || '<p class="meta">没有识别到待办。</p>'}
        ${(h.digest?.idea || []).length ? `${sectionTitle('想法')}${(h.digest.idea || []).slice(0, 5).map((u) => `<p class="meta">· ${esc(u.text)}</p>`).join('')}` : ''}
        ${pending ? `<section class="card session-review-entry"><div><h3>${pending}项可以逐项核对</h3><p class="meta">待办、决定与不知道谁说的话分别处理，也可以稍后再说。</p></div>
          <button class="button primary" type="button" data-act="review-start" data-id="${esc(h.session_id)}"><span>开始 / 继续核对</span></button></section>` : ''}
        <div class="action-grid">
          <button class="button secondary" type="button" data-act="rename-open"><span>改名</span></button>
          <button class="button secondary" type="button" data-act="tab" data-tab="transcript">${icon('quotes')}<span>查看完整原文</span></button></div>
      </div></div>`;
  }

  function meetingTranscriptHtml(heading, stage) {
    const h = S.history;
    const rows = (h.utterances || []).filter((u) => (u.text || '').trim());
    const who = (u) => u.review?.speaker || u.speaker;
    const names = [...new Set(rows.map(who).filter(Boolean))];
    const q = S.transcriptFilter.query.trim();
    const shown = rows.filter((u) => (!S.transcriptFilter.speaker || who(u) === S.transcriptFilter.speaker) && (!q || u.text.includes(q)));
    return `<div class="stack sessions-feature">${heading}${stage}
      <div class="search-input">${icon('magnifying-glass', 18)}<input id="meeting-search" aria-label="搜索这次原文" placeholder="搜索这次原文" value="${esc(S.transcriptFilter.query)}"></div>
      ${names.length ? speakerChips(names, S.transcriptFilter.speaker) : ''}
      <p class="meta">点一句可以改说话人，▶ 从服务器回听原声（会后录音上传完才有）。</p>
      ${shown.map((u) => {
        const manual = u.review?.speaker;
        const name = manual ? manualLabel(manual) : speakerLabel(u.speaker);
        return `<div><div class="card clickable session-transcript" data-act="history-speaker" data-id="${esc(u.id)}">
          <div class="session-speaker"><span class="avatar">${esc(name ? initial(name) : '?')}</span><strong class="${manual ? 'manual-name' : name ? '' : 'unknown-name'}">${esc(name ? (manual ? `${name} · 已改` : name) : '未识别')}</strong>
            <span>${hms(u.start_ms)}</span><button class="play-mini" data-act="history-play" data-id="${esc(u.id)}" data-key="h-${esc(u.id)}" aria-label="回听原声">${icon('play', 16)}</button></div>
          <p>${esc(u.text)}${TAG_LABELS[u.tag] && !['chitchat', 'uncertain'].includes(u.tag) ? ` <span class="badge ${u.tag === 'decision' ? 'green' : u.tag === 'commitment' ? 'purple' : 'gray'}">${TAG_LABELS[u.tag]}</span>` : ''}</p></div></div>`;
      }).join('') || `<div class="empty-state"><strong>${q || S.transcriptFilter.speaker ? '没有符合条件的原话' : '这场会议没有文字'}</strong></div>`}
    </div>`;
  }

  function meetingActionsHtml(heading, stage) {
    const h = S.history;
    const todos = h.digest?.commitment || [];
    const created = todos.filter((u) => u.task_status === 'created'), open = todos.filter((u) => u.task_status !== 'created');
    return `<div class="stack sessions-feature">${heading}${stage}
      ${listRow({ act: 'review-start', data: `data-id="${esc(h.session_id)}"`, ic: 'sparkle', title: '识别到的任务建议', sub: '会后核对：是否是待办、谁负责' })}
      ${open.length ? `<section class="session-list-group">${sectionTitle('待承接')}${open.map((u) => commitmentCard(u, 'history-quote')).join('')}</section>` : ''}
      ${created.length ? `<section class="session-list-group">${sectionTitle('已建任务')}${created.map((u) => commitmentCard(u, 'history-quote')).join('')}</section>` : ''}
      ${!todos.length ? `<div class="empty-state"><span>${icon('check-square', 28)}</span><strong>这场会议没有识别到待办</strong></div>` : ''}
      <button class="button quiet" type="button" data-act="go" data-route="tasks"><span>查看全部任务</span></button>
    </div>`;
  }

  // ---- 任务 ---------------------------------------------------------------------------------

  const TASK_STATE = { done: ['已完成', 'green'], deleted: ['飞书里已删除', 'gray'], unknown: ['状态未知', 'gray'], open: ['未完成', 'purple'] };
  function openTasks() {
    S.tasks.items = null;
    if (!S.tasks.mine || S.state.selfName) send('tasks.list', { mine: S.tasks.mine, status: S.tasks.status });
  }
  function taskCard(t) {
    const [label, tone] = t.overdue ? ['逾期', 'rose'] : TASK_STATE[t.feishu?.state] || TASK_STATE.open;
    const due = t.feishu?.due_ms ? `${new Date(t.feishu.due_ms).getMonth() + 1}月${String(new Date(t.feishu.due_ms).getDate()).padStart(2, '0')}日` : t.when?.raw || '无固定期限';
    return `<button class="card clickable task-card task-list-card" data-act="task-open" data-url="${esc(t.url || '')}">
      <div class="section-title"><span class="badge ${tone === 'rose' ? 'red' : tone}">${label}</span>${t.feishu?.stale ? '<span class="badge gray">可能不是最新</span>' : ''}</div>
      <h3>${esc(t.what)}</h3>
      <p class="meta">${esc(t.who || '负责人待定')}负责 · ${esc(due)}${t.meeting ? ` · ${esc(t.meeting.title || t.meeting.goal || formatDate(t.meeting.started_at))}` : ''}</p>
      ${t.quote ? `<p class="meta">“${esc(t.quote)}”</p>` : ''}
      <div class="task-card-next">${icon('flag', 18)}<span>${t.feishu?.state === 'done' ? '在飞书里查看' : t.overdue ? '期限已到，核对进度' : '在飞书里推进'}</span></div></button>`;
  }
  function tasksHtml() {
    const t = S.tasks;
    const needSelf = t.mine && !S.state.selfName;
    const q = t.query.trim();
    const items = (t.items || []).filter((x) => !q || (x.what || '').includes(q) || (x.who || '').includes(q));
    const overdue = items.filter((x) => x.overdue), open = items.filter((x) => !x.overdue && ['open', 'unknown'].includes(x.feishu?.state));
    const done = items.filter((x) => x.feishu?.state === 'done'), gone = items.filter((x) => x.feishu?.state === 'deleted');
    const group = (title, rows) => rows.length ? `<section class="stack task-group"><div class="section-title"><h2>${title} <span class="meta">${rows.length}</span></h2></div>${rows.map(taskCard).join('')}</section>` : '';
    const mineCount = t.mine && t.items ? (t.items.filter((x) => x.feishu?.state !== 'done').length) : null;
    return `<div class="stack task-list">
      <div class="section-title"><div><h1 class="title">把事情往前推进</h1><p class="meta">${mineCount != null ? `${mineCount}项我的工作` : '团队在飞书里的任务'}</p></div></div>
      <div class="segmented" role="tablist">${[['mine', '我的任务'], ['all', '全部任务']].map(([k, label]) => `<button role="tab" aria-selected="${(k === 'mine') === t.mine}" data-act="tasks-scope" data-value="${k}">${label}</button>`).join('')}</div>
      <div class="task-search-row"><div class="search-input">${icon('magnifying-glass', 18)}<input id="tasks-search" aria-label="搜索任务或负责人" placeholder="搜索任务或负责人" value="${esc(t.query)}"></div>
        <button class="task-filter-button" data-act="tasks-status-cycle" aria-label="任务筛选">${icon('sliders-horizontal', 18)}<span>${{ open: '未完成', done: '已完成', all: '全部' }[t.status]}</span></button></div>
      <p class="meta">${t.mine ? `${esc(S.state.selfName || '本人')}负责` : '全部负责人'} · ${{ open: '未结束', done: '已完成', all: '全部状态' }[t.status]} · 只含经确认后建的飞书任务</p>
      ${needSelf ? `<section class="card"><h3>先选你是谁</h3><p class="meta">选好后，这里只列你负责的任务。</p><div class="inline-actions">${S.team.map((n) => `<button class="chip" data-act="tasks-self" data-name="${esc(n)}">${esc(n)}</button>`).join('')}</div></section>`
        : t.items == null ? spinnerHtml('正在读取飞书任务…', '状态最多缓存 5 分钟')
        : `${group('需要跟进', overdue)}${group('进行中', open)}${group('已完成', done)}${group('飞书里已删除', gone)}
          ${!items.length ? `<div class="empty-state"><span>${icon('check-square', 28)}</span><strong>没有符合条件的任务</strong><p>会议里确认的待办建成飞书任务后会出现在这里。</p></div>` : ''}`}
    </div>`;
  }

  // ---- 记忆 ---------------------------------------------------------------------------------

  function memoryHtml() {
    const tab = S.memoryTab;
    const seg = `<div class="segmented" role="tablist">${[['projects', '项目'], ['people', '人物']].map(([k, label]) => `<button role="tab" aria-selected="${k === tab}" data-act="memory-tab" data-value="${k}">${label}</button>`).join('')}</div>`;
    if (tab === 'people') {
      const v = S.state.voiceprint || {};
      const people = v.catalog?.people || [];
      return `<div class="stack memory-settings">${seg}
        <button class="card clickable ms-pending" data-act="vp-open"><span class="badge purple">声音身份</span><h3>声纹录入与匹配测试</h3><p class="meta">由本人朗读 30 秒；会议里据此判断谁在说话。</p></button>
        ${sectionTitle('团队成员')}
        ${people.length ? people.map((p) => `<section class="card"><div class="ms-person-head"><span class="avatar">${esc(initial(p.name))}</span><div><h3>${esc(p.name)}</h3>
          <p class="meta">${p.registered ? `已录入声纹 · ${esc(SOURCE_TEXT[p.source] || p.source || '')} · ${p.seconds} 秒` : '还没有录入声纹'}</p></div></div></section>`).join('')
          : `<p class="meta">${v.phase === 'loading' ? '正在读取…' : '还没有读到成员名单。打开声纹录入可以刷新。'}</p>`}
      </div>`;
    }
    const items = S.projects.items;
    return `<div class="stack memory-settings">${seg}
      <button class="card clickable ms-pending" data-act="go" data-route="sessions"><span class="badge purple">项目记忆</span><h3>同一项目里确认过的决定</h3><p class="meta">会在之后开会时用来提醒"和之前定的不一样"。在会议里点「所属项目」把会议归进项目。</p></button>
      ${sectionTitle('项目')}
      ${items == null ? spinnerHtml('正在读取项目…', '团队服务器') : items.map((p) => `<button class="card clickable" data-act="project-open" data-name="${esc(p.name)}">
        <div class="ms-card-heading"><span class="badge purple">项目</span>${icon('folder-simple', 18)}</div><h3>${esc(p.name)}</h3>
        <p class="ms-preview">${p.meeting_count} 场会议 · 已确认决定 ${p.confirmed_decisions}</p><p class="meta">最近 ${formatDate(p.last_started_at)}</p></button>`).join('')
        || `<div class="empty-state"><span>${icon('folder-simple', 28)}</span><strong>还没有项目</strong><p>打开一场会议，点「所属项目」新建或选择项目。</p></div>`}
    </div>`;
  }

  function projectHtml() {
    const p = S.projects.open;
    if (!p) return spinnerHtml('正在读取项目…', S.route.name_ || '');
    const sec = S.projects.section;
    const seg = `<div class="segmented" role="tablist">${[['decisions', `决定 ${p.decisions.length}`], ['tasks', `任务 ${p.tasks.length}`], ['meetings', `会议 ${p.meetings.length}`]]
      .map(([k, label]) => `<button role="tab" aria-selected="${k === sec}" data-act="project-section" data-value="${k}">${label}</button>`).join('')}</div>`;
    const body = sec === 'decisions'
      ? `${p.unreviewed_decisions ? `<p class="meta">另有 ${p.unreviewed_decisions} 条决定还没会后核对。核对确认后会出现在这里，并用于开会时的冲突提醒。</p>` : ''}
        ${p.decisions.map((d) => `<button class="card clickable" data-act="meeting-open" data-id="${esc(d.session_id)}"><div class="ms-card-heading"><span class="badge ${d.tag === 'constraint' ? 'gray' : 'green'}">${d.tag === 'constraint' ? '约束' : '决定'}</span></div>
          <h3>${esc(d.text)}</h3><p class="meta">${formatDate(d.started_at)} ·《${esc(d.meeting_title || '会议')}》${d.speaker ? ` · ${esc(manualLabel(d.speaker))}` : ''}</p></button>`).join('')
          || `<div class="empty-state"><span>${icon('check-circle', 28)}</span><strong>还没有确认过的决定</strong><p>在会后核对里点「是决定」。</p></div>`}`
      : sec === 'tasks' ? (p.tasks.map(taskCard).join('') || `<div class="empty-state"><strong>这个项目的会议还没有建过飞书任务</strong></div>`)
      : p.meetings.map(sessionCard).join('');
    return `<div class="stack memory-settings"><div class="section-title"><div><h1 class="title">${esc(p.name)}</h1><p class="meta">${p.meetings.length} 场会议 · 已确认决定 ${p.decisions.length}</p></div></div>${seg}${body}</div>`;
  }

  // ---- 我的 ---------------------------------------------------------------------------------

  function meHtml() {
    const s = S.state;
    const group = (title, rows) => `<div>${sectionTitle(title)}<section class="card">${rows.join('')}</section></div>`;
    return `<div class="stack">
      <section class="card ms-profile"><div class="ms-person-head"><span class="avatar">${esc(s.selfName ? initial(s.selfName) : '?')}</span>
        <div><h2>${esc(s.selfName || '还没选择本人')}</h2><p class="meta">${s.selfName ? '团队成员 · 用于"我的任务"' : '选择后，任务和待处理只列你负责的'}</p></div></div>
        <button class="ms-space" data-act="self-pick">${esc(s.selfName ? '切换本人' : '选择本人')}${icon('caret-down', 13)}</button></section>
      ${group('我与助理', [
        listRow({ act: 'self-pick', ic: 'user-circle', title: '身份与角色', sub: s.selfName || '未选择' }),
        listRow({ act: 'vp-open', ic: 'waveform', title: '我的声音身份', sub: '声纹录入与匹配测试' })])}
      ${group('连接', [
        listRow({ act: 'go', data: 'data-route="settings"', ic: 'microphone', title: '收音与唤醒', sub: `${SOURCE_TEXT[s.source] || s.source || ''} · ${s.voiceTrigger !== false ? '语音唤醒开' : '语音唤醒关'}` }),
        listRow({ act: 'go', data: 'data-route="settings"', ic: 'plugs-connected', title: '团队服务器', sub: s.backend === 'mock' ? 'Mock 后端（不连服务器）' : s.backendUrl || '未配置' })])}
      ${group('数据与运行', [
        listRow({ act: 'go', data: 'data-route="settings"', ic: 'shield-check', title: '会议文字与录音', sub: `${s.memorySync !== false ? '文字自动同步' : '文字同步已关'} · ${s.recordSession ? '保存原始录音' : '不保存录音'}` }),
        listRow({ act: 'go', data: 'data-route="settings"', ic: 'chart-bar', title: '诊断与召回耗时', sub: S.latency?.recentMs?.length ? `召回中位数 ${(S.latency.medianMs / 1000).toFixed(1)} 秒` : '还没有召回记录' })])}
      <p class="meta ms-center">Oops · 会议文字保存在团队服务器</p>
    </div>`;
  }

  function settingsHtml() {
    const s = S.state;
    const l = S.latency;
    const latency = l && l.recentMs && l.recentMs.length
      ? `中位数 ${(l.medianMs / 1000).toFixed(2)} 秒 · 最近 ${l.recentMs.map((ms) => (ms / 1000).toFixed(1)).join(' / ')} 秒` : '还没有召回记录';
    const toggle = (id, title, sub, on) => `<label class="toggle-row"><span><strong>${title}</strong>${sub ? `<small>${sub}</small>` : ''}</span><input type="checkbox" id="${id}" ${on ? 'checked' : ''}></label>`;
    return `<div class="stack">
      <section class="card"><h3>收音</h3>
        <label class="form-field"><span>音源（保存后立即切换，保留本场记录）</span><select id="set-source">
          <option value="mic" ${s.source === 'mic' ? 'selected' : ''}>手机麦克风</option>
          <option value="wav" ${s.source === 'wav' ? 'selected' : ''}>测试 WAV（预录音频）</option>
          <option value="remote" ${s.source === 'remote' ? 'selected' : ''}>Mac 收音（经 USB 接收电脑上的麦克风）</option></select></label>
        <label class="form-field"><span>Mac 收音地址</span><input id="set-remote" inputmode="url" placeholder="127.0.0.1:7790" value="${esc(s.remoteAddress || '127.0.0.1:7790')}"><small>配合 adb reverse 用 127.0.0.1</small></label>
        <p class="meta">测试音频：${esc(s.testWavName || '内置合成对话')} · 需要 PCM 16kHz / 单声道 / 16bit WAV</p>
        <div class="inline-actions"><button class="chip" data-act="pick-audio">选择测试 WAV</button><button class="chip" data-act="reset-audio">恢复内置音频</button></div>
        ${toggle('set-voice', '说「Oops」触发找回/提问', '', s.voiceTrigger !== false)}
        ${toggle('set-vibrate', '提醒短震动', '', s.noticeVibrate !== false)}</section>
      <section class="card"><h3>会议数据</h3>
        ${toggle('set-sync', '会议文字自动同步', '把转写文字、标签、说话人和提醒存到团队服务器，不含音频，可在会议里删除。', s.memorySync !== false)}
        ${toggle('set-record', '保存原始录音与转写', `会后自动上传团队服务器，约 1.9 MB/分钟。${s.recordingsPending ? `${s.recordingsPending} 场待上传${s.recordingUpload === 'uploading' ? '（上传中）' : ''}` : '全部已上传'}`, s.recordSession)}</section>
      <section class="card"><h3>团队服务器</h3>
        ${toggle('set-mock', '使用 Mock 后端', '只在本机模拟，不连服务器', s.backend === 'mock')}
        <label class="form-field"><span>后端地址</span><input id="set-url" inputmode="url" placeholder="https://…" value="${esc(s.backendUrl || '')}"></label>
        <label class="form-field"><span>服务访问凭据</span><input id="set-token" type="password" autocomplete="off" placeholder="${s.hasBackendToken ? '已配置；留空保留原凭据' : '远程服务提供的访问凭据'}"></label>
        ${toggle('set-clear-token', '清除当前地址的访问凭据', '', false)}</section>
      <section class="card"><h3>试验功能</h3>
        ${toggle('set-spk', '说话人区分', '实验：测试中会丢句', s.speakerInfo)}
        ${toggle('set-preview', '实时换人拆分与重叠提示', '试验', s.speakerPreviewEnabled)}</section>
      <button class="button primary" type="button" data-act="apply-settings"><span>保存设置</span></button>
      <section class="card"><h3>召回耗时</h3><p class="meta">${esc(latency)}（验收：中位数 ≤ 5 秒）</p>
        <h3>诊断</h3><pre class="diag" id="diag">${esc(diagText())}</pre></section>
    </div>`;
  }

  function diagText() {
    const s = S.state;
    return [
      `session  ${clock(s.sessionMs)}   buffer ${clock(s.bufferedMs)}`,
      `source   ${s.source}${s.sourceDetail ? ` — ${s.sourceDetail}` : ''}`,
      `asr      ${s.asr}${s.asrDetail ? ` — ${s.asrDetail}` : ''}`,
      `backend  ${s.backend}${s.backend === 'http' ? ` ${s.backendUrl}` : ''}`,
      `gaps     ${(s.asrGaps || []).length}`,
    ].join('\n');
  }

  function applySettings() {
    const settings = {
      voiceTrigger: $('set-voice').checked, noticeVibrate: $('set-vibrate').checked, memorySync: $('set-sync').checked,
      source: $('set-source').value,
      remoteAddress: $('set-remote').value.trim(),
      mockBackend: $('set-mock').checked,
      backendBaseUrl: $('set-url').value.trim(),
      speakerInfo: $('set-spk').checked,
      speakerPreview: $('set-preview').checked,
      recordSession: $('set-record').checked,
    };
    if ($('set-clear-token').checked) settings.backendToken = '';
    else if ($('set-token').value.trim()) settings.backendToken = $('set-token').value.trim();
    send('settings.set', settings);
    toast('设置已保存');
  }

  // ---- sheets -----------------------------------------------------------------------------

  function openSheet(sheet) {
    if (S.sheet?.kind === 'voiceprint' && sheet.kind !== 'voiceprint') {
      if (['saving', 'testing'].includes(S.state.voiceprint?.phase)) return toast('请等待当前请求结束');
      send('voiceprint.cancel');
    }
    S.sheet = sheet;
    renderSheet();
    $('sheet').hidden = false;
    $('backdrop').hidden = false;
  }

  function closeSheet() {
    if (S.sheet?.kind === 'voiceprint') {
      if (['saving', 'testing'].includes(S.state.voiceprint?.phase)) return toast('请等待当前请求结束');
      send('voiceprint.cancel');
    }
    if (S.playingKey) send('clip.stop');
    S.sheet = null;
    $('sheet').hidden = true;
    $('backdrop').hidden = true;
  }

  const frame = (title, body, desc = '') => `<div class="sheet-handle-zone" data-act="close"><div class="sheet-handle"></div></div>
    <div class="sheet-header"><h2 class="sheet-title">${title}</h2>${desc ? `<p class="sheet-description">${desc}</p>` : ''}</div>
    <div class="sheet-content"><div class="sheet-content">${body}</div></div>`;
  const spinnerHtml = (title, sub) => `<div class="empty-state oops-loading"><div class="oops-spinner"></div><strong>${esc(title)}</strong><p>${esc(sub)}</p></div>`;

  function renderSheet() {
    const k = S.sheet?.kind;
    const fresh = $('sheet').dataset.kind !== (k || '');
    $('sheet').dataset.kind = k || '';
    const html = {
      pending: () => frame(esc(S.sheet.title || '请稍等'), spinnerHtml(S.sheet.title || '请稍等', S.sheet.sub || '')),
      draftPending: () => frame('整理行动草稿', spinnerHtml('正在整理行动草稿…', '提取负责人、事项、截止时间和约束')),
      notFound: notFoundHtml,
      card: cardHtml,
      draft: draftHtml,
      result: resultHtml,
      voiceprint: voiceprintHtml,
      saved: savedHtml,
      commitments: commitmentsHtml,
      ask: askHtml,
      agenda: agendaHtml,
      notices: () => frame('会议提醒', noticeRecordsHtml(S.sheet.rows || [...S.notices.values()], !!S.sheet.rows)),
      speaker: speakerPickHtml,
      review: reviewHtml,
      projectPick: projectPickHtml,
      rename: renameHtml,
      tools: toolsHtml,
      selfPick: selfPickHtml,
    }[k];
    // Re-rendering the same sheet (a play state, a late result) keeps its scroll; a different sheet starts at the top.
    const scroller = $('sheet').querySelector ? $('sheet').querySelector('.sheet-content') : null;
    const keep = !fresh && scroller ? scroller.scrollTop : 0;
    $('sheet').innerHTML = html ? html() : '';
    const next = $('sheet').querySelector ? $('sheet').querySelector('.sheet-content') : null;
    if (next) next.scrollTop = keep;
    syncPlayButtons();
  }

  function notFoundHtml() {
    return frame('没找到', `<div class="empty-state"><span>${icon('magnifying-glass', 28)}</span><strong>没找到</strong><p>${esc(S.sheet.reason)}</p></div>
      <div class="action-grid"><button class="button secondary" type="button" data-act="close"><span>知道了</span></button>
        <button class="button primary" type="button" data-act="retry-recall"><span>再试一次</span></button></div>`);
  }

  // ---- 本场纪要 ------------------------------------------------------------------------------

  function commitmentsHtml() {
    const summary = S.summary;
    function group(title, rows, todo = false, ideas = false) {
      const shown = ideas ? rows.slice(0, S.showAllIdeas ? rows.length : 5) : rows;
      return `${sectionTitle(`${title} <span class="meta">${rows.length}</span>`)}${shown.map((u) => {
        const reviewed = u.review?.verdict === 'confirmed';
        const hint = todo ? `${u.review?.owner || u.ownerHint || u.owner_hint || '负责人待定'}${!reviewed && (u.suggestedOwner || u.suggested_owner) ? ' · 建议 ' + (u.suggestedOwner || u.suggested_owner) : ''} · ${reviewed || u.reviewStatus === 'confirmed' ? '人工已确认' : u.ownerReviewStatus === 'selected' ? '人工已选择，待确认任务' : '待核对'} · ${u.whenRaw || u.when_raw || '截止待定'}${u.taskStatus === 'created' ? ' · 已建飞书任务' : ''}` : '';
        return `<button class="list-row" data-act="commitment-open" data-id="${esc(u.id)}"><span class="row-copy"><strong>${esc(u.text)}</strong>
          <small>${hms(u.startMs)}${u.speaker ? ' · ' + esc(speakerLabel(u.speaker)) : ''}${u.audioExpired ? ' · 原声未保留' : ''}${hint ? `<br>${esc(hint)}` : ''}</small></span>${icon('caret-right', 18)}</button>`;
      }).join('') || '<p class="meta">暂无</p>'}${ideas && rows.length > 5 && !S.showAllIdeas ? '<button class="button quiet" type="button" data-act="ideas-all"><span>展开全部想法</span></button>' : ''}`;
    }
    return frame('本场纪要', `${S.state.tagsSupported === false ? '<p class="meta">当前后端不支持标签分析。</p>' : ''}
      ${S.state.sessionTruncated ? '<p class="meta">文字索引已达上限，较早条目已移出。</p>' : ''}
      ${group('决定', summary.decisions || [])}${group('待办', summary.commitments || [], true)}${group('约束', summary.constraints || [])}${group('想法', summary.ideas || [], false, true)}
      ${!S.state.capturing && S.state.sessionId ? `<button class="button primary" type="button" data-act="review-start" data-id="${esc(S.state.sessionId)}"><span>会后核对（待办、决定、说话人）</span></button>` : ''}
      <p class="meta">本场索引保留到下次收音。开启文字同步后可在会话里查看；原声最多暂存 50 段。</p>`, '点一条可以回听原话，或整理成飞书任务。');
  }

  // ---- recall card --------------------------------------------------------------------------

  function openCard(card) {
    S.card = { data: card, sel: 0, before: 0, after: 0 };
    openSheet({ kind: 'card' });
    highlight(card.items.map((i) => i.id));
  }

  /** Selected candidate widened by `before` / `after` neighbouring sentences. */
  function selected() {
    const { data, sel, before, after } = S.card;
    const pool = data.pool;
    const idx = Math.max(0, pool.findIndex((u) => u.id === data.items[sel].id));
    return pool.slice(Math.max(0, idx - before), Math.min(pool.length, idx + after + 1));
  }
  function selectedRange() {
    const sel = selected();
    return { startMs: sel[0].startMs, endMs: sel[sel.length - 1].endMs };
  }
  const cardClipKey = () => `${S.card.data.id}:${S.card.sel}:${S.card.before}:${S.card.after}`;

  function cardHtml() {
    const c = S.card;
    const d = c.data;
    const item = d.items[c.sel];
    const idx = d.pool.findIndex((u) => u.id === item.id);
    const canPrev = !d.readOnly && idx - c.before > 0;
    const canNext = !d.readOnly && idx + c.after < d.pool.length - 1;
    const quote = selected().map((u) => (u.id === item.id ? esc(u.text) : `<span class="ctx">${esc(u.text)}</span>`)).join(' ');
    const range = selectedRange();
    const speaker = speakerLabel(item.speaker);
    const title = d.query ? `问答 · 「${esc(d.query)}」` : d.trigger === 'digest' ? '本场原话' : d.trigger === 'conflict' ? '冲突原话' : d.readOnly ? '往期原话' : '找回刚才的原话';
    const when = item.startedAt ? `${esc(formatDate(item.startedAt))} 会议` : `<span id="card-ago">${ago(S.state.sessionMs - item.endMs)}</span>`;
    const candidates = d.single ? '' : `<p class="meta">${d.trigger === 'conflict' ? '对照这两句原话：' : `有 ${d.items.length} 条接近，选一条：`}</p>
      ${d.items.map((it, i) => `<button class="card clickable recall-cand ${i === c.sel ? 'selected' : ''}" data-act="cand" data-i="${i}">
        <p class="recall-cand-text">${esc(it.text)}</p>
        <p class="meta">${it.startedAt ? esc(formatDate(it.startedAt)) + ' 会议 · ' : ''}${clock(it.startMs)}${it.speaker != null ? ` · ${esc(speakerLabel(it.speaker))}` : ''}</p>
        <span class="prob"><i style="width:${Math.round(it.prob * 100)}%"></i></span></button>`).join('')}`;
    return frame(title, `${candidates}
      <section class="card recall-quote-card"><p class="recall-quote">“${quote}”</p>
        <p class="meta">${speaker ? `${esc(speaker)} · ` : ''}${clock(range.startMs)}–${clock(range.endMs)} · 语音转写</p></section>
      <div class="inline-actions">
        <button class="chip selected" data-act="play" data-key="${esc(cardClipKey())}" ${item.audioAvailable === false || d.readOnly ? 'disabled' : ''}>${item.audioAvailable === false || d.readOnly ? '原声未保留' : '▶ 回听原声'}</button>
        <button class="chip" data-act="prev" ${canPrev ? '' : 'disabled'}>＋前一句</button>
        <button class="chip" data-act="next" ${canNext ? '' : 'disabled'}>＋后一句</button>
        ${c.before || c.after ? '<button class="chip" data-act="reset">还原</button>' : ''}</div>
      ${d.readOnly ? '' : `<div class="action-grid"><button class="button secondary" type="button" data-act="save"><span>保存</span></button>
        <button class="button primary" type="button" data-act="draft"><span>生成行动</span></button></div>
        <button class="button quiet" type="button" data-act="dismiss"><span>丢弃</span></button>`}
      <p class="meta">${d.readOnly ? '往期文字 · 只读' : '本场原话 · 可保存或起草行动'} · 召回 ${(d.latencyMs / 1000).toFixed(1)} 秒 · ${esc(d.engine)}</p>`, when);
  }

  // ---- clips --------------------------------------------------------------------------------

  function toggleClip(key, range, sessionId) {
    if (S.playingKey === key) send('clip.stop');
    else send('clip.play', { key, startMs: range.startMs, endMs: range.endMs, ...(sessionId ? { sessionId } : {}) });
  }
  function onClip(p) {
    if (p.state === 'playing') S.playingKey = p.key;
    else if (S.playingKey === p.key) S.playingKey = null;
    if (p.state === 'expired') toast('原声已过期（只保留最近 5 分钟）', true);
    if (p.state === 'loading') S.loadingKey = p.key; else if (S.loadingKey === p.key) S.loadingKey = null;
    syncPlayButtons();
  }
  /** Updates play buttons in place so form edits in the sheet survive. */
  function syncPlayButtons() {
    document.querySelectorAll('[data-key]').forEach((btn) => {
      const playing = S.playingKey === btn.dataset.key;
      if (btn.disabled) return;
      if (btn.dataset.act === 'play') btn.textContent = playing ? '■ 停止' : '▶ 回听原声';
      else if (btn.classList.contains('play-mini')) btn.innerHTML = playing ? icon('stop', 16) : S.loadingKey === btn.dataset.key ? '…' : icon('play', 16);
      else btn.textContent = playing ? '■' : S.loadingKey === btn.dataset.key ? '…' : '▶';
    });
  }

  // ---- action draft -----------------------------------------------------------------------

  function openDraft(d) {
    S.draft = window.OopsDraftState.create(d);
    openSheet({ kind: 'draft' });
  }
  function editDraftField(field, value) {
    const previousOwner = S.draft?.data?.who || '';
    if (!window.OopsDraftState.edit(S.draft, field, value)) return false;
    if (field === 'who' && previousOwner !== (value || '')) {
      const model = S.draft.model;
      const ids = S.draft.data.evidence?.utteranceIds || [];
      ids.forEach((id) => S.ownerEdits.set(id, { ownerHint: value || null, suggestedOwner: model?.suggestedOwner || null, ownerReviewStatus: value ? 'selected' : null }));
      S.commitments = S.commitments.map((u) => ids.includes(u.id) ? { ...u, ...S.ownerEdits.get(u.id) } : u);
    }
    return true;
  }
  function taskContextHtml(c) {
    if (!c) return '';
    const role = { speaker: '说话人自己承诺', addressee: '说话人要求对方去做', named: '原话点名', unknown: '负责人未定' }[c.ownerRole] || '负责人未定';
    const who = c.clauseSpeaker ? `这句由 ${esc(c.clauseSpeaker)} 说（试验拆分，待核对）· ` : '';
    return `<section class="card task-context"><p class="meta">任务原话：“${esc(c.quote)}” · ${who}${esc(role)}</p>
      ${(c.resolutions || []).map((r) => `<p class="meta">“${esc(r.phrase)}”→ ${esc(r.meaning)}（依据：“${esc(r.evidence)}”）</p>`).join('')}
      ${(c.missing || []).length ? `<p class="warn-text">还缺：${c.missing.map(esc).join('；')}</p>` : ''}
      ${(c.otherTasks || []).length ? `<p class="meta">这句还提到：${c.otherTasks.map(esc).join('；')}</p>` : ''}</section>`;
  }
  function draftHtml() {
    const { data: d, status, error } = S.draft;
    const names = [...new Set([...(d.ownerCandidates || []).map((c) => c.name), ...(d.assigneeCandidates || []), ...(d.who ? [d.who] : []), ...(d.suggestedOwner ? [d.suggestedOwner] : [])])];
    const options = ['<option value="">请选择负责人</option>'].concat(names.map((n) => `<option ${n === d.who ? 'selected' : ''}>${esc(n)}</option>`)).join('');
    const sending = status === 'sending';
    const submitted = status === 'waiting' || status === 'failed' || sending;
    const tier = d.tier === 'A' ? ['A · 模型高可信', 'green'] : d.tier === 'B' ? ['B · 建议，请核对', 'amber'] : ['C · 模型未确认负责人', 'gray'];
    const suggestions = [...new Set([d.suggestedOwner, ...(d.ownerCandidates || []).map((c) => c.name)].filter(Boolean))].slice(0, 2);
    return frame('整理成飞书任务', `<div class="inline-actions"><span class="badge ${tier[1]}">${tier[0]}</span><span class="badge ${S.draft.reviewStatus === 'confirmed' ? 'green' : 'purple'}">${S.draft.reviewStatus === 'confirmed' ? '人工已确认' : '待你确认'}</span></div>
      <p class="meta">${esc(d.reason || '请核对负责人')}${typeof d.ownerConfidence === 'number' ? ` · 模型建议可信度 ${(d.ownerConfidence * 100).toFixed(0)}%` : ''}</p>
      ${suggestions.length ? `<div class="inline-actions">${suggestions.map((n) => `<button class="chip" data-act="choose-owner" data-name="${esc(n)}" ${submitted ? 'disabled' : ''}>建议 ${esc(n)}</button>`).join('')}</div>` : ''}
      <section class="card evidence-card"><button class="play-mini" data-act="play-evidence" data-key="ev-${esc(d.draftId)}" aria-label="回听原声">${icon('play', 16)}</button><p>“${esc(d.evidence.quote)}”</p></section>
      ${taskContextHtml(d.taskContext)}
      <label class="form-field"><span>负责人</span><select id="f-who" ${submitted ? 'disabled' : ''}>${options}</select></label>
      <label class="form-field"><span>事项</span><textarea id="f-what" rows="3" ${submitted ? 'disabled' : ''}>${esc(d.what)}</textarea></label>
      <label class="form-field"><span>截止时间</span><input type="date" id="f-when" ${submitted ? 'disabled' : ''} value="${esc(d.whenIso || '')}">${d.whenRaw ? `<small>原话：${esc(d.whenRaw)}</small>` : ''}</label>
      <label class="form-field"><span>约束</span><input id="f-constraint" ${submitted ? 'disabled' : ''} value="${esc(d.constraint || '')}" placeholder="例如：预算不超过 10 万"></label>
      <p class="meta">只有你点确认后才会写入飞书；同一次确认只会建一条任务。</p>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <button class="button primary" type="button" data-act="${status === 'waiting' ? 'query-action' : 'confirm'}" ${sending ? 'disabled' : ''}><span>${sending ? '正在查询…' : status === 'waiting' ? '查询创建结果' : status === 'failed' ? '重试同一操作' : '确认创建飞书任务'}</span></button>
      <button class="button quiet" type="button" data-act="close"><span>取消</span></button>`);
  }
  function confirmDraft() {
    const d = S.draft.data;
    const who = $('f-who').value.trim();
    const what = $('f-what').value.trim();
    if (!who || !what) { toast('请先选负责人、填写事项', true); return; }
    // Keep the edits on the draft so a re-render (e.g. after an error) doesn't lose them.
    Object.assign(d, { who, what, whenIso: $('f-when').value || null, constraint: $('f-constraint').value.trim() || null });
    window.OopsDraftState.confirm(S.draft);
    S.draft.error = null;
    renderSheet();
    send('action.confirm', {
      draftId: d.draftId, who, what, whenIso: d.whenIso || '', whenRaw: d.whenRaw || '', constraint: d.constraint || '',
      ownerSelectionSource: d.ownerSelectionSource,
    });
  }
  function onActionResult(p) {
    if (!S.draft || S.draft.data.draftId !== p.draftId) return;
    S.draft.submitted = true;
    if (p.status === 'pending') {
      S.draft.status = p.polling ? 'sending' : 'waiting';
      S.draft.error = p.message || null;
      if (S.sheet?.kind === 'draft') renderSheet();
      return;
    }
    if (p.status === 'created') {
      S.draft.status = 'done';
      S.draft.result = p;
      openSheet({ kind: 'result' });
    } else {
      S.draft.status = 'failed';
      S.draft.error = p.message || '后端报告创建失败；本次确认内容已冻结，可查询状态';
      if (S.sheet?.kind === 'draft') renderSheet();
      else openSheet({ kind: 'draft' });
    }
  }
  function resultHtml() {
    const d = S.draft.data;
    const r = S.draft.result;
    return frame(r.mock ? 'Mock 任务已生成' : '已创建飞书任务', `<div class="empty-state"><span>${icon('check-circle', 28)}</span><strong>${esc(d.what)}</strong>
        <p>${esc(d.who)} · ${esc(d.whenIso || '未设截止时间')}</p>${r.message ? `<p>${esc(r.message)}</p>` : ''}</div>
      <button class="button primary" type="button" data-act="open-task" ${r.taskUrl && !r.mock ? '' : 'disabled'}><span>在飞书中打开</span></button>
      <button class="button quiet" type="button" data-act="close"><span>完成</span></button>
      <p class="meta">操作编号 ${esc(String(r.opId).slice(0, 8))} · ${r.readBackConfirmed ? '已回读确认' : '尚未回读核验'}</p>`);
  }

  // ---- voiceprint ---------------------------------------------------------------------------

  const ENROLL_TEXT = '现在录入我的声纹，这段话只用于声音测试。早晨的阳光照进房间，窗外有人散步，远处传来汽车的声音。桌上放着一本书、一杯水和一支笔。我用平时说话的声音朗读，保持自然的语速和音量。读完后稍作停顿，再从头朗读一次。';
  const TEST_TEXT = '午后的公园很安静，树叶随着微风轻轻摇动。小路旁有几朵花，远处的孩子正在玩耍。我现在用另一段文字测试声音匹配。';

  function voiceprintHtml() {
    const v = S.state.voiceprint || { phase: 'idle' };
    const busy = ['loading', 'saving', 'testing', 'checking'].includes(v.phase);
    const recording = v.phase === 'recording';
    const name = ['recording', 'checking', 'review', 'saving', 'testing'].includes(v.phase) ? (v.name || S.voiceprintName) : (S.voiceprintName || v.name);
    const people = v.catalog?.people || [];
    const limit = v.mode === 'test' ? 10 : 30;
    const locked = busy || recording || v.phase === 'review';
    const message = busy ? ({ loading: '正在读取声纹列表…', saving: '正在检查并保存声纹…', testing: '正在比较四人的声纹…', checking: '正在检查录音质量…' })[v.phase] : v.message;
    const results = v.diagnosis ? `${sectionTitle('这段测试录音的结果')}
      <section class="card"><p class="meta">${esc(v.diagnosis.speaker ? '当前判断：' + v.diagnosis.speaker : '当前判断：未知')}<br>
      最接近：${esc(v.diagnosis.closest || '暂无声纹')} · 相似度 ${Number(v.diagnosis.score).toFixed(3)}<br>${esc(v.diagnosis.reason)}</p>
      ${(v.diagnosis.scores || []).map((x) => `<p class="meta">${esc(x.name)} · ${Number(x.score).toFixed(3)}</p>`).join('')}
      <p class="meta">当前门槛 ${v.diagnosis.accept}，领先幅度至少 ${v.diagnosis.required_margin}；相似度不是正确概率，请核对真实说话人。</p></section>` : '';
    return frame('声纹录入与测试', `<label class="form-field"><span>本人姓名</span><select id="vp-name" ${locked ? 'disabled' : ''}>
        <option value="">请选择正在朗读的人</option>${people.map((p) => `<option value="${esc(p.name)}" ${name === p.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
      <p class="meta">${recording ? '正在使用' : '录入音源'}：${esc(SOURCE_TEXT[v.source || (S.state.capturing ? S.state.source : 'mic')] || '手机麦克风')}。请仅由本人朗读，避免旁人讲话或播放录音。</p>
      ${sectionTitle(v.mode === 'test' ? '测试朗读文字（约 10 秒）' : '录入朗读文字（约 30 秒）')}
      <section class="card"><p class="read-aloud">${v.mode === 'test' ? TEST_TEXT : `我是${esc(name || '（请先选姓名）')}。${ENROLL_TEXT}`}</p></section>
      ${recording ? `${sectionTitle(`已录 ${clock(v.elapsedMs)} / 0:${limit}`)}<p class="meta">${S.state.sourceReady ? '正在收到音频' : '等待麦克风连接'}</p>
        <button class="button secondary" type="button" data-act="vp-stop"><span>结束这段录音</span></button>` : ''}
      ${v.phase === 'review' ? `<p class="meta">已录 ${clock(v.elapsedMs)}。${v.mode === 'test' ? '测试只比较声音，不会更新声纹。' : `保存后替换 ${esc(name)} 的现有声纹，其他成员保留。`}</p>
        <div class="action-grid"><button class="button secondary" type="button" data-act="vp-cancel"><span>放弃并重录</span></button>
          <button class="button primary" type="button" data-act="vp-save" ${(v.mode === 'test' ? v.elapsedMs < 1000 : !v.qualityPassed) ? 'disabled' : ''}><span>${v.mode === 'test' ? '重试匹配' : '保存声纹'}</span></button></div>` : ''}
      ${!recording && !busy && v.phase !== 'review' ? `<label class="toggle-row"><span><strong>由本人单独朗读</strong><small>并同意上传声音用于声纹匹配</small></span><input id="vp-consent" type="checkbox"></label>
        <div class="action-grid"><button class="button primary" type="button" data-act="vp-record" ${people.length ? '' : 'disabled'}><span>开始录入 · 30 秒</span></button>
          <button class="button secondary" type="button" data-act="vp-test" ${people.length ? '' : 'disabled'}><span>测试匹配 · 10 秒</span></button></div>` : ''}
      ${v.quality ? `<p class="meta">录音 ${v.quality.seconds} 秒 · 有效音量片段 ${v.quality.active_seconds} 秒</p>` : ''}
      ${message ? `<p class="meta" role="status">${esc(message)}</p>` : ''}
      ${results}
      ${sectionTitle('已注册成员')}
      ${people.map((p) => `<div class="list-row"><span class="avatar">${esc(initial(p.name))}</span><span class="row-copy"><strong>${esc(p.name)}</strong>
        <small>${p.registered ? `${esc(SOURCE_TEXT[p.source] || p.source || '')} · ${p.seconds} 秒` : '未注册'}</small></span>
        ${p.registered ? `<button class="chip" data-act="vp-delete-prompt" data-name="${esc(p.name)}" ${locked ? 'disabled' : ''}>删除</button>` : ''}</div>`).join('')}
      ${S.voiceprintDelete ? `<p class="meta">确认删除 ${esc(S.voiceprintDelete)} 的声纹？删除后需重新录入才能匹配此人。</p>
        <button class="button danger" type="button" data-act="vp-delete-confirm"><span>确认删除 ${esc(S.voiceprintDelete)}</span></button>` : ''}
      <button class="button quiet" type="button" data-act="vp-refresh" ${locked ? 'disabled' : ''}><span>刷新列表</span></button>
      <p class="meta">服务器只保存声纹特征，临时录音用于提取或测试后清除。音量、时长及片段一致性检查不能验证本人身份，也不能保证识别准确。关闭本页会放弃尚未保存的录入；日常收音不受影响。</p>`);
  }
  function recordVoiceprint(mode) {
    const name = $('vp-name').value;
    if (mode === 'enroll' && !name) return toast('请先选择本人姓名');
    if (!$('vp-consent').checked) return toast('请先确认本人同意上传声音');
    S.voiceprintName = name;
    S.voiceprintDelete = null;
    send('voiceprint.record', { name, consent: true, mode });
  }

  // ---- saved, ask, agenda, notices --------------------------------------------------------

  function savedHtml() {
    const items = S.sheet.items;
    return frame('已保存 · 长期记忆', items.length
      ? items.map((it) => `<section class="card"><p class="recall-quote">${esc((it.utterances || []).map((u) => u.text).join(' '))}</p>
          <p class="meta">${esc(formatDate(it.saved_at))} · ${it.audio_expired ? '原声已过期' : '含原声'}</p></section>`).join('')
      : `<div class="empty-state"><span>${icon('bookmark-simple', 28)}</span><strong>还没有保存的片段</strong><p>找回原话后点「保存」才会留在这里。</p></div>`);
  }
  function askHtml() {
    return frame('问已有记录', `<label class="form-field"><span>范围</span><select id="ask-scope"><option value="session">本场</option><option value="history" ${S.sheet.history ? 'selected' : ''}>往期会议</option></select></label>
      <label class="form-field"><span>想问什么？</span><textarea id="ask-query" rows="3" maxlength="500" placeholder="预算上限是多少？谁负责新方案？">${esc(S.sheet.query || '')}</textarea></label>
      <div class="inline-actions"><button class="chip" data-act="ask-example" data-query="预算上限是多少？">预算上限</button><button class="chip" data-act="ask-example" data-query="谁负责新方案？">谁来负责</button></div>
      <button class="button primary" type="button" data-act="ask-submit"><span>查找原话</span></button>
      <p class="meta">答案只引用已记录的原话。本场停止收音后仍可提问，下一场开始会清空本场索引。</p>`);
  }
  function agendaHtml() {
    const a = S.state.agenda || {};
    return frame('本场目标与议程', `<label class="form-field"><span>本场目标</span><input id="agenda-goal" maxlength="500" value="${esc(a.goal || '')}" placeholder="确认上线方案与负责人"></label>
      <label class="form-field"><span>议程 · 每行一项，可加分钟数</span><textarea id="agenda-items" rows="6" placeholder="方案评审 10&#10;负责人确认（5分钟）">${esc((a.items || []).map((i) => i.title + (i.plannedMs ? ` ${i.plannedMs / 60000}` : '')).join('\n'))}</textarea></label>
      <label class="form-field"><span>会议计划时长（分钟，可留空）</span><input id="agenda-minutes" type="number" min="1" max="480" value="${a.plannedMs ? a.plannedMs / 60000 : ''}"></label>
      <p class="meta">最多 20 项，每项标题 60 字，时长 1–480 分钟。收音中也可以修改；已讨论时长会保留。</p>
      <div class="action-grid"><button class="button secondary" type="button" data-act="agenda-clear"><span>清除议程</span></button><button class="button primary" type="button" data-act="agenda-save"><span>保存议程</span></button></div>`);
  }
  function saveAgenda() {
    const lines = $('agenda-items').value.split('\n').map((x) => x.trim()).filter(Boolean);
    if (lines.length > 20) return toast('最多 20 项议程', true);
    const items = lines.map((line) => {
      const m = line.match(/^(.*?)(?:[（(]\s*(\d+)\s*分钟\s*[）)]|\s+(\d+))\s*$/);
      return { title: m ? m[1].trim() : line, minutes: m ? Number(m[2] || m[3]) : null };
    });
    const minutes = $('agenda-minutes').value ? Number($('agenda-minutes').value) : null;
    if (items.some((i) => !i.title || i.title.length > 60) || [...items.map((i) => i.minutes), minutes].some((n) => n !== null && (!Number.isInteger(n) || n < 1 || n > 480))) return toast('请检查标题和分钟数', true);
    send('agenda.set', { goal: $('agenda-goal').value.trim(), items, minutes }); closeSheet();
  }
  let noticeTimer;
  function showNotice(n) {
    S.notices.set(n.id, n);
    S.activeNotices = [...S.activeNotices.filter((x) => x !== n.id), n.id].slice(-2);
    renderNoticeBanners();
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { S.activeNotices = []; renderNoticeBanners(); }, 12000);
  }
  function noticeButtons(n) {
    return `<div class="inline-actions">${n.evidenceIds?.length ? `<button class="chip selected" data-act="notice-open" data-id="${esc(n.id)}">查看原话</button>` : ''}
      <button class="chip" data-act="notice-useful" data-id="${esc(n.id)}" ${n.useful === true ? 'disabled' : ''}>有用</button><button class="chip" data-act="notice-wrong" data-id="${esc(n.id)}" ${n.useful === false ? 'disabled' : ''}>不准</button>
      <button class="chip" data-act="notice-dismiss" data-id="${esc(n.id)}" aria-label="关闭提醒">收起</button></div>`;
  }
  function renderNoticeBanners() {
    $('notices').innerHTML = S.activeNotices.map((id) => S.notices.get(id)).filter((n) => n && !n.dismissed).map((n) =>
      `<section class="card notice-banner"><span class="badge amber">${esc(n.kind === 'conflict' ? '可能冲突' : '会议提醒')}</span><h3>${esc(n.title)}</h3><p>${esc(n.detail)}</p>${noticeButtons(n)}</section>`).join('');
  }
  function noticeRecordsHtml(rows, readonly = false) {
    return rows.length ? rows.map((n) => `<section class="card"><h3>${esc(n.title)}</h3><p>${esc(n.detail)}</p>
      <p class="meta">${clock(n.atMs ?? n.at_ms)}${n.useful === false ? ' · 已反馈不准' : n.useful === true ? ' · 已反馈有用' : ''}${n.dismissed ? ' · 已收起' : ''}</p>${readonly ? '' : noticeButtons(n)}</section>`).join('')
      : `<div class="empty-state"><span>${icon('bell', 28)}</span><strong>暂无提醒</strong><p>议程超时、跑题、和之前的决定冲突时会提醒。</p></div>`;
  }

  // ---- past meetings ------------------------------------------------------------------------

  function historyOpen(p) {
    S.history = p;
    if (S.pendingReview === p.session_id) { S.pendingReview = null; return startReview(p); }
    if (S.sheet?.kind === 'pending') closeSheet();
    if (S.route.name !== 'meeting' || S.route.id !== p.session_id) { S.meetingTab = 'overview'; S.transcriptFilter = { speaker: null, query: '' }; go('meeting', { id: p.session_id }); }
    else render();
    if (S.pendingTools === p.session_id) { S.pendingTools = null; openSheet({ kind: 'tools', live: false }); }
  }
  function historySearch(p) {
    if (!p.items?.length) return openSheet({ kind: 'notFound', reason: '往期会议原话里没有找到答案' });
    const items = p.items.map((i) => ({ id: i.id, text: i.text, startMs: i.start_ms, endMs: i.end_ms, speaker: i.speaker, prob: i.prob, audioAvailable: false, startedAt: i.started_at }));
    openCard({ id: 'history-search', trigger: 'ask', query: p.query, nowMs: 0, items, pool: items, single: items.length === 1, engine: p.engine, latencyMs: 0, readOnly: true });
  }
  function onRenamed(p) {
    if (S.history?.session_id === p.session_id) { S.history.display_title = p.title; S.history.title_source = p.title_source; }
    const item = (S.historyItems || []).find((i) => i.session_id === p.session_id);
    if (item) { item.title = p.title; item.title_source = p.title_source; }
    closeSheet();
    render();
    toast(p.title_source === 'manual' ? '已改名' : '已恢复自动名称');
  }
  function renameHtml() {
    const h = S.history, source = { manual: '手动命名', agenda: '议程目标', generated: 'AI 概括', auto: '按原话自动取', short: '内容较少', none: '' }[h.title_source] || '';
    return frame('会议名称', `<label class="form-field"><span>会议名称（留空恢复自动名称）</span><input id="rename-input" maxlength="40" value="${esc(h.title_source === 'manual' ? h.display_title : '')}" placeholder="${esc(h.display_title || '例如：安卓版本排期')}"><small>现在的名称：${esc(meetingName(h))}${source ? ` · ${source}` : ''}</small></label>
      <button class="button primary" type="button" data-act="rename-save"><span>保存名称</span></button>`);
  }
  function toolsHtml() {
    const live = S.sheet.live;
    const rows = live ? [
      listRow({ act: 'commitments-open', ic: 'check-circle', title: '本场纪要', sub: '决定、待办、约束与想法' }),
      listRow({ act: 'agenda-open', ic: 'list-checks', title: '议程与时间' }),
      listRow({ act: 'live-project', ic: 'folder-simple', title: '所属项目', sub: S.liveProject || '未归项目' }),
      listRow({ act: 'notices-open', ic: 'bell', title: '全部会议提醒' }),
      listRow({ act: 'ask-open', ic: 'magnifying-glass', title: '问本场' }),
      listRow({ act: 'saved-list', ic: 'bookmark-simple', title: '已保存片段' }),
    ] : [
      listRow({ act: 'rename-open', ic: 'pencil-simple', title: '名称', sub: meetingName(S.history || {}) }),
      listRow({ act: 'history-project', ic: 'folder-simple', title: '所属项目', sub: S.history?.project || '未归项目' }),
      listRow({ act: 'review-start', data: `data-id="${esc(S.history?.session_id || '')}"`, ic: 'check-circle', title: '会后核对', sub: `${(S.history?.review_queue || []).length}项待核对` }),
      listRow({ act: 'meeting-notices', ic: 'bell', title: '会议提醒' }),
      listRow({ act: 'history-delete-prompt', ic: 'trash', title: '删除这场会议', sub: '团队成员将无法再查看，录音一并删除' }),
    ];
    return frame(live ? '本次会话工具' : '会议工具', `${rows.join('')}
      ${S.sheet.deleteConfirm ? `<p class="meta">团队成员将无法再查看这场会议，原始录音一并删除。确认删除？</p><button class="button danger" type="button" data-act="history-delete-confirm"><span>确认删除</span></button>` : ''}`);
  }
  function selfPickHtml() {
    return frame('选择本人', `<p class="meta">用于"我的任务"和首页的待处理。</p>
      <div class="inline-actions">${S.team.map((n) => `<button class="chip ${n === S.state.selfName ? 'selected' : ''}" data-act="self-set" data-name="${esc(n)}">${esc(n)}</button>`).join('')}</div>
      ${S.state.selfName ? '<button class="button quiet" type="button" data-act="self-set" data-name=""><span>不指定本人</span></button>' : ''}`);
  }

  // ---- who said it ------------------------------------------------------------------------

  function openSpeakerPick(pick) {
    openSheet({ kind: 'speaker', ...pick });
  }
  function speakerPickHtml() {
    const p = S.sheet;
    const options = [...S.team.map((n) => [n, n]), ...Object.entries(MANUAL_OTHER)];
    return frame('这句是谁说的', `<section class="card evidence-card"><button class="play-mini" data-act="speaker-play" data-key="spk-${esc(p.id)}" aria-label="回听原声">${icon('play', 16)}</button><p>“${esc(p.text)}”</p></section>
      <p class="meta">模型判断：${esc(speakerLabel(p.model) || '未识别')}${p.current ? ` · 已改为 ${esc(manualLabel(p.current))}` : ''}</p>
      <div class="inline-actions">${options.map(([v, label]) => `<button class="chip ${v === p.current ? 'selected' : ''}" data-act="speaker-set" data-name="${esc(v)}">${esc(label)}</button>`).join('')}</div>
      ${p.current ? '<button class="button quiet" type="button" data-act="speaker-undo"><span>撤销修改，恢复模型结果</span></button>' : ''}
      <p class="meta">改动存到团队服务器，任务草稿会改用你选的人来建议负责人（仍需确认）。</p>`);
  }
  function setSpeakerFromPick(value) {
    const p = S.sheet;
    send('speaker.correct', { sessionId: p.sessionId, utteranceId: p.id, speaker: value });
    closeSheet();
  }
  function onSpeakerCorrected(p) {
    if (p.sessionId === S.state.sessionId) {
      const u = S.records.get(p.utteranceId);
      if (u) { u.manualSpeaker = p.speaker; renderLine(p.utteranceId); renderLiveRecent(); }
    }
    if (p.status === 'saved' && S.history?.session_id === p.sessionId) {
      const u = (S.history.utterances || []).find((x) => x.id === p.utteranceId);
      if (u) {
        u.review = { ...(u.review || {}), speaker: p.speaker };
        if (p.speaker == null) delete u.review.speaker;
      }
      if (S.route.name === 'meeting' && S.meetingTab === 'transcript') render();
    }
    if (p.status === 'saved') toast(p.speaker == null ? '已恢复模型结果' : `已改为 ${manualLabel(p.speaker)}`);
  }

  // ---- after the meeting: one sentence at a time ----------------------------------------------

  const REVIEW_KIND = { commitment: '待办', decision: '决定', speaker: '说话人' };
  function startReview(row) {
    S.review = { sessionId: row.session_id, items: row.review_queue || [], i: 0, done: new Set(), ownerStep: false };
    openSheet({ kind: 'review' });
  }
  function reviewHtml() {
    const r = S.review, row = S.history;
    if (r.i >= r.items.length) {
      return frame('会后核对', `<div class="empty-state"><span>${icon('check-circle', 28)}</span><strong>${r.items.length ? '核对完了' : '没有需要核对的内容'}</strong>
        <p>已核对 ${r.done.size} 条${r.items.length - r.done.size ? `，跳过 ${r.items.length - r.done.size} 条（下次打开还会出现）` : ''}。</p></div>
        <button class="button primary" type="button" data-act="review-finish"><span>回到会议</span></button>`);
    }
    const item = r.items[r.i], at = row.utterances.findIndex((u) => u.id === item.id), u = row.utterances[at];
    const before = row.utterances.slice(Math.max(0, at - 2), at);
    const who = (x) => x.review?.speaker ? `${manualLabel(x.review.speaker)} · 已改` : speakerLabel(x.speaker) || '未识别';
    const live = r.sessionId === S.state.sessionId;
    let answer;
    if (item.kind === 'speaker') {
      answer = `${sectionTitle('这句是谁说的？')}<div class="inline-actions">${[...S.team.map((n) => [n, n]), ...Object.entries(MANUAL_OTHER)]
        .map(([v, label]) => `<button class="chip" data-act="review-speaker" data-name="${esc(v)}">${esc(label)}</button>`).join('')}</div>`;
    } else if (item.kind === 'commitment' && r.ownerStep) {
      const hint = u.owner_hint;
      answer = `${sectionTitle('谁负责？')}<div class="inline-actions">${S.team.map((n) => `<button class="chip ${n === hint ? 'selected' : ''}" data-act="review-owner" data-name="${esc(n)}">${esc(n)}</button>`).join('')}
        <button class="chip" data-act="review-owner" data-name="">不确定</button></div>${live ? '<p class="meta">选完负责人后可以在本场纪要里建飞书任务。</p>' : ''}`;
    } else {
      const [yes, no] = item.kind === 'commitment' ? ['是待办', '不是待办'] : ['是决定', '不是决定'];
      answer = `${item.kind === 'commitment' ? `<p class="meta">${esc(u.owner_hint ? '原话里的负责人：' + u.owner_hint : '负责人待定')} · ${esc(u.when_raw || '截止待定')}</p>` : ''}
        <div class="action-grid"><button class="button secondary" type="button" data-act="review-verdict" data-value="rejected"><span>${no}</span></button><button class="button primary" type="button" data-act="review-verdict" data-value="confirmed"><span>${yes}</span></button></div>`;
    }
    return frame(`会后核对 · ${r.i + 1} / ${r.items.length}`, `<div class="inline-actions"><span class="badge purple">${REVIEW_KIND[item.kind]}</span><span class="meta">${hms(u.start_ms)} · ${esc(who(u))}</span></div>
      ${before.map((x) => `<p class="meta review-context">${esc(who(x))}：${esc(x.text)}</p>`).join('')}
      <section class="card evidence-card"><button class="play-mini" data-act="review-play" data-key="rv-${esc(u.id)}" aria-label="回听原声">${icon('play', 16)}</button><p>“${esc(u.text)}”</p></section>
      ${answer}
      <div class="inline-actions review-nav"><button class="chip" data-act="review-prev" ${r.i ? '' : 'disabled'}>上一条</button><button class="chip" data-act="review-skip">跳过</button></div>
      <p class="meta">核对结果存到团队服务器；界面显示了模型的判断，所以这些结果只用于改进，不作为验收标准。</p>`);
  }
  function reviewCurrent() {
    const r = S.review, item = r.items[r.i];
    return { r, item, u: S.history.utterances.find((x) => x.id === item.id) };
  }
  function reviewNext(answered = true) {
    const r = S.review;
    if (answered) r.done.add(r.i);
    r.i += 1; r.ownerStep = false;
    renderSheet();
  }
  function reviewVerdict(verdict) {
    const { r, item, u } = reviewCurrent();
    if (item.kind === 'commitment' && verdict === 'confirmed') { r.ownerStep = true; return renderSheet(); }
    send('review.set', { sessionId: r.sessionId, utteranceId: u.id, fields: { verdict } });
    u.review = { ...(u.review || {}), verdict };
    reviewNext();
  }
  function reviewOwner(owner) {
    const { r, u } = reviewCurrent();
    send('review.set', { sessionId: r.sessionId, utteranceId: u.id, fields: { verdict: 'confirmed', owner: owner || null } });
    u.review = { ...(u.review || {}), verdict: 'confirmed', ...(owner ? { owner } : {}) };
    if (r.sessionId === S.state.sessionId) toast('已记下负责人；要建飞书任务，在本场纪要里点这条待办');
    reviewNext();
  }
  function onReviewSaved(p) {
    if (S.history?.session_id !== p.sessionId) return;
    const u = (S.history.utterances || []).find((x) => x.id === p.utteranceId);
    if (u) u.review = p.review || undefined;
  }

  // ---- projects -----------------------------------------------------------------------------

  function openProjectPick(sessionId, current) {
    openSheet({ kind: 'projectPick', sessionId, current });
    send('projects.list');
  }
  function projectPickHtml() {
    const p = S.sheet, names = (S.projects.items || []).map((x) => x.name);
    return frame('所属项目', `<p class="meta">同一项目里确认过的决定，会用来提醒这场会里"和之前定的不一样"。</p>
      <div class="inline-actions">${names.map((n) => `<button class="chip ${n === p.current ? 'selected' : ''}" data-act="project-set" data-name="${esc(n)}">${esc(n)}</button>`).join('') || '<span class="meta">还没有项目</span>'}</div>
      <label class="form-field"><span>新项目</span><input id="project-new" maxlength="30" placeholder="例如：安卓 App"></label>
      <button class="button primary" type="button" data-act="project-create"><span>用这个新项目</span></button>
      ${p.current ? '<button class="button quiet" type="button" data-act="project-set" data-name=""><span>移出项目</span></button>' : ''}`);
  }
  function onMeetingProject(p) {
    if (S.history?.session_id === p.session_id) S.history.project = p.project;
    if (p.session_id === S.state.sessionId) S.liveProject = p.project;
    const item = (S.historyItems || []).find((i) => i.session_id === p.session_id);
    if (item) item.project = p.project;
    S.projects.items = null;
    if (S.sheet?.kind === 'projectPick') closeSheet();
    render();
    toast(p.project ? `已归入「${p.project}」` : '已移出项目');
  }

  // ---- errors -------------------------------------------------------------------------------

  function onError(p) {
    toast(p.message, true);
    if (['history', 'projects', 'tasks'].includes(p.scope) && S.sheet?.kind === 'pending') closeSheet();
    if (p.scope === 'recall') {
      setBusy(false);
      if (S.sheet?.kind === 'pending') closeSheet();
    } else if (p.scope === 'action') {
      if (S.sheet?.kind === 'draftPending') closeSheet();
      if (S.draft && S.draft.status === 'sending') {
        S.draft.status = S.draft.submitted ? 'waiting' : 'editing';
        S.draft.error = p.message;
        if (S.sheet?.kind === 'draft') renderSheet();
      }
    } else if (p.scope === 'save' && S.sheet?.kind === 'card') {
      renderSheet();
    }
  }

  // ---- interactions -------------------------------------------------------------------------

  function back() {
    const r = S.route.name;
    if (r === 'project') return go('memory');
    if (r === 'settings') return go('me');
    go('sessions');
  }
  function noticeFeedback(id, useful) {
    send('notice.feedback', { id, useful }); const n = S.notices.get(id); if (n) n.useful = useful;
    renderNoticeBanners(); if (['notices', 'commitments'].includes(S.sheet?.kind)) renderSheet();
  }
  function askSubmit(query, history) {
    if (!query) return toast('请输入问题');
    openSheet({ kind: 'pending', title: '查找原话', sub: history ? '在往期会议里找' : '在本场里找' });
    send(history ? 'history.search' : 'ask.session', { query });
  }
  function openMeeting(id) {
    openSheet({ kind: 'pending', title: '打开会议', sub: '团队服务器' });
    send('history.open', { sessionId: id });
  }

  const ACTIONS = {
    go: (el) => go(el.dataset.route),
    back,
    tab: (el) => {
      if (S.route.name === 'live') S.liveTab = el.dataset.tab; else S.meetingTab = el.dataset.tab;
      S.transcriptFilter = { speaker: null, query: '' };
      render(); $('scroll').scrollTop = 0;
      if (S.route.name === 'live' && S.liveTab === 'transcript') { S.autoScroll = true; scrollToEnd(); }
    },
    close: closeSheet,
    'capture-start': () => send('capture.start'),
    'capture-stop': () => {
      const count = S.commitments.filter((u) => u.reviewStatus !== 'confirmed').length;
      if (count) toast(`${count} 条负责人待确认，可以在会后核对里处理`);
      send('capture.stop');
    },
    recall: () => send('recall.trigger'),
    'saved-list': () => send('saved.list'),
    'ask-open': () => openSheet({ kind: 'ask', history: S.route.name !== 'live' && !S.state.capturing }),
    'ask-example': (el) => { $('ask-query').value = el.dataset.query; },
    'ask-submit': () => askSubmit($('ask-query').value.trim(), $('ask-scope').value === 'history'),
    'home-ask-scope': () => { S.homeAskHistory = !S.homeAskHistory; render(); },
    'home-ask': () => askSubmit($('home-ask').value.trim(), !!S.homeAskHistory),
    'agenda-open': () => openSheet({ kind: 'agenda' }),
    'agenda-save': saveAgenda,
    'agenda-clear': () => { send('agenda.clear'); closeSheet(); },
    'commitments-open': () => openSheet({ kind: 'commitments' }),
    'ideas-all': () => { S.showAllIdeas = true; renderSheet(); },
    'notices-open': () => openSheet({ kind: 'notices' }),
    'meeting-notices': () => openSheet({ kind: 'notices', rows: S.history?.notices || [] }),
    'live-tools': () => openSheet({ kind: 'tools', live: true }),
    'meeting-tools': (el) => {
      if (el.dataset.id && S.history?.session_id !== el.dataset.id) { S.pendingTools = el.dataset.id; return openMeeting(el.dataset.id); }
      openSheet({ kind: 'tools', live: false });
    },
    'live-project': () => openProjectPick(S.state.sessionId, S.liveProject),
    'history-project': () => openProjectPick(S.history.session_id, S.history.project || null),
    'project-set': (el) => send('meeting.project', { sessionId: S.sheet.sessionId, project: el.dataset.name }),
    'project-create': () => { const name = $('project-new').value.trim(); if (!name) return toast('请输入项目名称'); send('meeting.project', { sessionId: S.sheet.sessionId, project: name }); },
    'project-open': (el) => { S.projects.open = null; S.projects.section = 'decisions'; go('project', { name_: el.dataset.name }); send('project.open', { name: el.dataset.name }); },
    'project-section': (el) => { S.projects.section = el.dataset.value; render(); },
    'memory-tab': (el) => { S.memoryTab = el.dataset.value; enter(); render(); },
    'meeting-open': (el) => openMeeting(el.dataset.id),
    'rename-open': () => openSheet({ kind: 'rename' }),
    'rename-save': () => send('history.rename', { sessionId: S.history.session_id, title: $('rename-input').value.trim() }),
    'history-delete-prompt': () => { S.sheet.deleteConfirm = true; renderSheet(); },
    'history-delete-confirm': () => send('history.delete', { sessionId: S.history.session_id, confirm: true }),
    'history-quote': (el) => {
      const u = S.history.utterances.find((x) => x.id === el.dataset.id);
      if (u) historySearch({ query: '', engine: 'archive', items: [{ ...u, prob: 1, started_at: S.history.started_at }] });
    },
    'history-speaker': (el) => {
      const u = S.history.utterances.find((x) => x.id === el.dataset.id);
      if (u) openSpeakerPick({ live: false, sessionId: S.history.session_id, id: u.id, text: u.text, model: u.speaker,
        current: u.review?.speaker || null, startMs: u.start_ms, endMs: u.end_ms });
    },
    'history-play': (el) => {
      const u = S.history.utterances.find((x) => x.id === el.dataset.id), key = `h-${el.dataset.id}`;
      if (S.playingKey === key) send('clip.stop');
      else if (u) send('clip.remote', { key, sessionId: S.history.session_id, startMs: u.start_ms, endMs: u.end_ms });
    },
    'line-speaker': (el) => {
      const u = S.records.get(el.dataset.id);
      if (u) openSpeakerPick({ live: true, sessionId: S.state.sessionId, id: u.id, text: u.display || u.text, model: u.speaker,
        current: u.manualSpeaker || null, startMs: u.startMs, endMs: u.endMs });
    },
    'filter-speaker': (el) => {
      S.transcriptFilter.speaker = el.dataset.name || null;
      if (S.route.name === 'live') { S.records.forEach((_, id) => renderLine(id)); document.querySelectorAll('[data-act="filter-speaker"]').forEach((b) => { b.classList.toggle('selected', (b.dataset.name || null) === S.transcriptFilter.speaker); b.setAttribute('aria-pressed', String((b.dataset.name || null) === S.transcriptFilter.speaker)); }); }
      else render();
    },
    'speaker-set': (el) => setSpeakerFromPick(el.dataset.name),
    'speaker-undo': () => setSpeakerFromPick(null),
    'speaker-play': () => {
      const p = S.sheet, key = `spk-${p.id}`;
      if (S.playingKey === key) send('clip.stop');
      else if (p.live) send('clip.play', { key, startMs: p.startMs, endMs: p.endMs });
      else send('clip.remote', { key, sessionId: p.sessionId, startMs: p.startMs, endMs: p.endMs });
    },
    'review-start': (el) => { S.pendingReview = el.dataset.id; openMeeting(el.dataset.id); },
    'review-verdict': (el) => reviewVerdict(el.dataset.value),
    'review-owner': (el) => reviewOwner(el.dataset.name),
    'review-speaker': (el) => {
      const { r, u } = reviewCurrent();
      send('speaker.correct', { sessionId: r.sessionId, utteranceId: u.id, speaker: el.dataset.name });
      u.review = { ...(u.review || {}), speaker: el.dataset.name };
      reviewNext();
    },
    'review-skip': () => reviewNext(false),
    'review-prev': () => { S.review.i = Math.max(0, S.review.i - 1); S.review.ownerStep = false; renderSheet(); },
    'review-play': () => {
      const { r, u } = reviewCurrent(), key = `rv-${u.id}`;
      if (S.playingKey === key) send('clip.stop');
      // This meeting's audio may still be on the phone; otherwise it comes from the server archive.
      else send('clip.play', { key, sessionId: r.sessionId, startMs: u.start_ms, endMs: u.end_ms });
    },
    'review-finish': () => openMeeting(S.review.sessionId),
    'tasks-scope': (el) => { S.tasks.mine = el.dataset.value === 'mine'; openTasks(); render(); },
    'tasks-status-cycle': () => { S.tasks.status = { open: 'done', done: 'all', all: 'open' }[S.tasks.status]; openTasks(); render(); },
    'tasks-self': (el) => { send('settings.set', { selfName: el.dataset.name }); S.state.selfName = el.dataset.name; openTasks(); render(); },
    'task-open': (el) => { if (el.dataset.url) send('open.url', { url: el.dataset.url }); },
    'self-pick': () => openSheet({ kind: 'selfPick' }),
    'self-set': (el) => { send('settings.set', { selfName: el.dataset.name }); S.state.selfName = el.dataset.name; closeSheet(); render(); },
    'notice-open': (el) => { const n = S.notices.get(el.dataset.id); if (n?.evidenceIds?.length) send('digest.openMany', { ids: n.evidenceIds }); },
    'notice-useful': (el) => noticeFeedback(el.dataset.id, true),
    'notice-wrong': (el) => noticeFeedback(el.dataset.id, false),
    'notice-dismiss': (el) => { send('notice.dismiss', { id: el.dataset.id }); const n = S.notices.get(el.dataset.id); if (n) n.dismissed = true; renderNoticeBanners(); },
    'commitment-open': (el) => send('digest.open', { utteranceId: el.dataset.id }),
    'retry-recall': () => { closeSheet(); send('recall.trigger'); },
    cand: (el) => { S.card.sel = Number(el.dataset.i); S.card.before = 0; S.card.after = 0; renderSheet(); },
    prev: () => { S.card.before += 1; renderSheet(); },
    next: () => { S.card.after += 1; renderSheet(); },
    reset: () => { S.card.before = 0; S.card.after = 0; renderSheet(); },
    play: () => toggleClip(cardClipKey(), selectedRange()),
    dismiss: () => { send('recall.dismiss', { cardId: S.card.data.id }); closeSheet(); },
    save: (el) => {
      el.disabled = true;
      const sel = selected();
      send('recall.save', { cardId: S.card.data.id, utteranceIds: sel.map((u) => u.id), ...selectedRange() });
    },
    draft: () => {
      const sel = selected();
      send('action.draft', { cardId: S.card.data.id, utteranceIds: sel.map((u) => u.id) });
      openSheet({ kind: 'draftPending' });
    },
    'play-evidence': () => {
      const ev = S.draft.data.evidence;
      toggleClip(`ev-${S.draft.data.draftId}`, { startMs: ev.startMs, endMs: ev.endMs });
    },
    confirm: confirmDraft,
    'query-action': () => {
      S.draft.status = 'sending'; renderSheet();
      send('action.status', { draftId: S.draft.data.draftId });
    },
    'open-task': () => send('open.url', { url: S.draft.result.taskUrl }),
    'choose-owner': (el) => { if (editDraftField('who', el.dataset.name)) renderSheet(); },
    'vp-open': () => { S.voiceprintDelete = null; openSheet({ kind: 'voiceprint' }); send('voiceprint.list'); },
    'vp-refresh': () => send('voiceprint.list'),
    'vp-record': () => recordVoiceprint('enroll'),
    'vp-test': () => recordVoiceprint('test'),
    'vp-stop': () => send('voiceprint.stop'),
    'vp-save': () => send('voiceprint.save'),
    'vp-cancel': () => send('voiceprint.cancel'),
    'vp-delete-prompt': (el) => { S.voiceprintDelete = el.dataset.name; renderSheet(); },
    'vp-delete-confirm': () => { send('voiceprint.delete', { name: S.voiceprintDelete, confirm: true }); S.voiceprintDelete = null; },
    'apply-settings': applySettings,
    'pick-audio': () => send('testAudio.pick'),
    'reset-audio': () => send('testAudio.reset'),
  };

  // The sheet has its own listeners (it is inside the page, so page clicks inside it are skipped).
  $('sheet').addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el || el.disabled) return;
    const act = ACTIONS[el.dataset.act];
    if (act) act(el);
  });
  $('sheet').addEventListener('change', (e) => {
    if (e.target.id === 'vp-name') { S.voiceprintName = e.target.value; renderSheet(); }
    if (e.target.id === 'f-who') editDraftField('who', e.target.value);
  });
  $('sheet').addEventListener('input', (e) => {
    const field = { 'f-who': 'who', 'f-what': 'what', 'f-when': 'whenIso', 'f-constraint': 'constraint' }[e.target.id];
    if (field) editDraftField(field, e.target.value || (field === 'whenIso' ? null : ''));
  });
  $('backdrop').addEventListener('click', closeSheet);
  // Swiping down from the handle or the title closes the sheet.
  let sheetDrag = null;
  $('sheet').addEventListener('touchstart', (e) => { sheetDrag = e.target.closest('.sheet-handle-zone, .sheet-header') ? e.touches[0].clientY : null; }, { passive: true });
  $('sheet').addEventListener('touchend', (e) => {
    if (sheetDrag !== null && e.changedTouches[0].clientY - sheetDrag > 60) closeSheet();
    sheetDrag = null;
  }, { passive: true });
  // The system back key/gesture: close the sheet, then step back through the pages. false = nothing left to go back to.
  window.OopsBack = () => {
    if (S.sheet) { closeSheet(); return true; }
    if (!$('sheet').hidden) { closeSheet(); return true; }
    const r = S.route.name;
    if (['live', 'meeting', 'project', 'settings'].includes(r)) { back(); return true; }
    if (r !== 'home') { go('home'); return true; }
    return false;
  };
  $('root').addEventListener('click', (e) => {
    if (e.target.closest('#sheet')) return;
    const el = e.target.closest('[data-act]');
    if (!el || el.disabled) return;
    const act = ACTIONS[el.dataset.act];
    if (act) { e.stopPropagation(); act(el); }
  });
  // A live transcript card opens "who said it"; it is built outside the page templates.
  $('lines').addEventListener('click', (e) => {
    const card = e.target.closest('[data-id]');
    if (card) ACTIONS['line-speaker'](card);
  });
  $('root').addEventListener('input', (e) => {
    if (e.target.id === 'sessions-search') { S.sessionsQuery = e.target.value; const pos = e.target.selectionStart; render(); const i = $('sessions-search'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }
    if (e.target.id === 'tasks-search') { S.tasks.query = e.target.value; const pos = e.target.selectionStart; render(); const i = $('tasks-search'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }
    if (e.target.id === 'meeting-search') { S.transcriptFilter.query = e.target.value; const pos = e.target.selectionStart; render(); const i = $('meeting-search'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }
  });
  $('root').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return;
    if (e.target.id === 'live-ask') { e.preventDefault(); askSubmit(e.target.value.trim(), false); }
    if (e.target.id === 'home-ask' && !e.shiftKey) { e.preventDefault(); askSubmit(e.target.value.trim(), !!S.homeAskHistory); }
  });

  render();
  send('hello');
})();
