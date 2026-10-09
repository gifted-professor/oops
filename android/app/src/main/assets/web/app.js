/* Oops H5 UI. Talks to the native shell through window.OopsNative ({type, payload} JSON,
   see docs/bridge.md). In a desktop browser, bridge-mock.js provides a stand-in. */
(() => {
  'use strict';

  const forcedTheme = new URLSearchParams(location.search).get('theme');
  if (forcedTheme === 'light' || forcedTheme === 'dark') document.documentElement.dataset.theme = forcedTheme;

  const $ = (id) => document.getElementById(id);
  const native = window.OopsNative;
  const send = (type, payload = {}) => native.postMessage(JSON.stringify({ type, payload }));

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

  const S = {
    state: { capturing: false, sessionMs: 0, bufferedMs: 0, retentionMs: 300000, asr: 'off', level: 0, savedCount: 0, asrGaps: [] },
    lines: new Map(), // utterance id → <li>
    records: new Map(),
    commitments: [],
    ownerEdits: new Map(), // manual selections remain visible when model hints refresh
    summary: {}, history: null, historySummary: {}, notices: new Map(), activeNotices: [],
    sheet: null, // { kind, ... }
    card: null, // { data, sel, before, after }
    draft: null, // { data, status: editing|sending|done, result, error }
    playingKey: null,
    latency: null,
    autoScroll: true,
    voiceprintName: "",
    voiceprintDelete: null,
  };

  // ---- helpers --------------------------------------------------------------------------

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const clock = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
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
  const formatDate = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  let toastTimer = null;
  function toast(message, bad = false) {
    const el = $('toast');
    el.textContent = message;
    el.className = `toast${bad ? ' bad' : ''}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  function applyInsets(i) {
    if (!i) return;
    document.documentElement.style.setProperty('--safe-top', `${i.top || 0}px`);
    document.documentElement.style.setProperty('--safe-bottom', `${i.bottom || 0}px`);
  }

  // ---- messages from native -------------------------------------------------------------

  native.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    try { handle(msg.type, msg.payload || {}); } catch (e) { console.error(e); }
  };

  function handle(type, p) {
    switch (type) {
      case 'snapshot':
        applyInsets(p.insets);
        resetTranscript();
        S.state.sessionId = p.state.sessionId; // the snapshot already is this session's transcript
        (p.transcript || []).forEach(addLine);
        setState(p.state);
        if (p.summary) setSummary(p.summary);
        if (p.pendingActions?.length) {
          openDraft(p.pendingActions[0].draft);
          onActionResult(p.pendingActions[0].result);
        }
        break;
      case 'state': setState(p); break;
      case 'notice': showNotice(p); break;
      case 'history.list': openSheet({kind: 'history', items: p.items || []}); break;
      case 'history.open': historyOpen(p); break;
      case 'history.delete': toast('会议文字已删除'); send('history.list'); break;
      case 'history.search': historySearch(p); break;
      case 'voiceprint.open': openSheet({kind: 'voiceprint'}); break;
      case 'insets': applyInsets(p); break;
      case 'asr.partial':
        $('partial').textContent = p.text ? `… ${p.text}` : '';
        $('empty').hidden = true;
        scrollToEnd();
        break;
      case 'asr.final':
        $('partial').textContent = '';
        addLine(p);
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
        $('pruned-note').hidden = false;
        break;
      case 'recall.pending':
        setBusy(true);
        openSheet({ kind: 'pending' });
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

  // ---- status & transcript --------------------------------------------------------------

  function setState(s) {
    // A new capture session starts an empty timeline; old lines and cards no longer apply.
    if (s.sessionId && s.sessionId !== S.state.sessionId) resetTranscript();
    const previousVoiceprint = JSON.stringify(S.state.voiceprint);
    const previousPreview = S.state.speakerPreviewEnabled;
    S.state = { ...S.state, ...s };
    if (s.notices) { s.notices.forEach(n => S.notices.set(n.id, n)); renderNoticeBanners(); }
    if (previousPreview !== S.state.speakerPreviewEnabled) S.records.forEach((_, id) => renderLine(id));
    if (S.sheet?.kind === 'voiceprint' && previousVoiceprint !== JSON.stringify(S.state.voiceprint)) renderSheet();
    renderStatus();
  }

  function resetTranscript() {
    $('lines').innerHTML = '';
    S.lines.clear();
    S.records.clear();
    S.commitments = []; S.summary = {}; S.notices.clear(); S.activeNotices = []; renderNoticeBanners();
    S.ownerEdits.clear();
    $('partial').textContent = '';
    $('pruned-note').hidden = true;
    $('empty').hidden = false;
    S.autoScroll = true;
  }

  function renderStatus() {
    const s = S.state;
    $('pill').classList.toggle('on', !!s.capturing);
    $('capture-text').textContent = s.capturing ? (s.sourceReady ? '收音中' : '等待收音') : '未收音';
    const sourceLabel = SOURCE_TEXT[s.source] || s.source || '';
    // External sources report their link state (e.g. "Mac 收音：Wireless microphone").
    $('source-text').textContent = s.capturing && s.sourceDetail ? s.sourceDetail : sourceLabel;
    $('level').style.width = `${Math.round((s.capturing ? s.level : 0) * 100)}%`;
    $('buffer-text').textContent = `缓存 ${clock(s.bufferedMs)} / ${clock(s.retentionMs)}`;
    const [text, cls] = s.capturing && !s.sourceReady && !['error', 'unconfigured'].includes(s.asr)
      ? ['等待音源', ''] : (ASR_TEXT[s.asr] || [s.asr, '']);
    const asr = $('asr-text');
    asr.textContent = text;
    asr.className = `asr ${cls}`;
    const preview = $('speaker-preview-status');
    preview.hidden = !s.speakerPreviewEnabled || !s.capturing;
    preview.textContent = s.liveOverlap ? '疑似多人同时说话（试验检测）' :
      s.liveSpeakerStatus === 'unsupported' ? '讲话人预览：服务器尚未开启' :
      s.liveSpeakerStatus === 'error' ? '讲话人预览：请求失败' :
      s.liveSpeakers?.length ? `讲话人预览（试验）：${s.liveSpeakers.join('、')}` :
      s.liveSpeakerStatus === 'live' ? '讲话人预览：身份待确认' :
      s.liveSpeakerStatus === 'silence' ? '讲话人预览：等待讲话' : '讲话人预览：分析中…';

    const banner = $('gap-banner');
    const gaps = s.asrGaps || [];
    // Several conditions can hold at once; show every one rather than the most recent.
    const warnings = [];
    if (s.asr === 'unconfigured' || s.asr === 'error') {
      warnings.push(s.asrDetail || text);
    } else if (gaps.length) {
      const g = gaps[gaps.length - 1];
      warnings.push(`识别中断过 ${gaps.length} 次，最近一次 ${clock(g.fromMs)}–${clock(g.toMs)} 没有转写`);
    }
    if (s.capturing && s.micBusy) warnings.push('麦克风被其他应用占用，这段时间没有声音进来');
    if (s.capturing && s.recordingCapped) warnings.push('录音已达 3 小时上限，之后只保留文字');
    banner.hidden = !warnings.length;
    banner.textContent = warnings.join('；');

    const capture = $('btn-capture');
    capture.textContent = s.capturing ? '停止收音' : '开始收音';
    capture.classList.toggle('stop', !!s.capturing);
    $('saved-count').textContent = s.savedCount ?? 0;
    const commitments = $('btn-commitments');
    commitments.hidden = !s.sessionId;
    const counts = s.digestCounts || {};
    commitments.textContent = `本场纪要 · 决定 ${counts.decision || 0} · 待办 ${counts.commitment || 0} · 约束 ${counts.constraint || 0}`;
    commitments.disabled = false;
    const a = s.agenda || {};
    const current = (a.items || []).find(i => i.id === a.currentItemId);
    const covered = (a.items || []).filter(i => i.coveredMs > 0).length;
    $('btn-agenda').textContent = a.goal || a.items?.length ? `议程 ${covered}/${a.items?.length || 0} · ${current?.title || a.goal || '等待讨论'} · ${clock(s.sessionMs)}${a.plannedMs ? '/' + clock(a.plannedMs) : ''}` : '＋本场目标与议程';
    $('agenda-note').textContent = a.tracking === 'unsupported' ? '当前后端仅支持时间提醒' : '';
    $('sync-note').textContent = !s.memorySync ? '会议文字同步已关闭' : s.syncStatus === 'failed' ? '文字同步已停止，请检查服务器或删除状态' : s.syncStatus === 'unsupported' ? '当前后端不支持会议文字同步' : s.sessionId ? `文字同步到团队服务器 · ${s.syncStatus === 'retrying' ? '等待重试 · ' : ''}待同步 ${s.syncPending || 0}` : '';

    if (S.sheet?.kind === 'settings') {
      const diag = $('diag');
      if (diag) diag.textContent = diagText();
    }
    if (S.sheet?.kind === 'card') {
      const el = $('card-ago');
      if (el) el.textContent = ago(s.sessionMs - S.card.data.items[S.card.sel].endMs);
    }
  }

  function addLine(u) {
    if (S.lines.has(u.id)) return;
    const li = document.createElement('li');
    li.dataset.id = u.id;
    li.dataset.start = u.startMs;
    const speaker = speakerLabel(u.speaker);
    li.innerHTML = `<time>${clock(u.startMs)}</time><div>${speaker ? `<span class="spk">${esc(speaker)}</span>` : ''}${esc(u.text)}</div>`;
    const list = $('lines');
    // Keep time order: finals may interleave after an ASR reconnect.
    const next = [...list.children].find((el) => Number(el.dataset.start) > u.startMs) || null;
    list.insertBefore(li, next);
    S.lines.set(u.id, li);
    S.records.set(u.id, u);
    renderLine(u.id);
    $('empty').hidden = true;
    scrollToEnd();
  }

  /** The voiceprint answer arrives after the caption; add or replace the name chip in place. */
  function setLineSpeaker(id, speaker, segments = [], score = null, state = null, preview = null, candidates = [], trial = []) {
    const u = S.records.get(id);
    if (!u) return;
    u.speaker = speaker;
    u.speakerCandidates = candidates || [];
    u.trialSegments = trial || [];
    u.segments = segments;
    u.speakerScore = score;
    u.speakerState = state;
    u.speakerPreview = preview;
    renderLine(id);
  }

  const TAG_LABELS = { commitment: '承诺/指派', decision: '决定', idea: '想法', constraint: '约束', chitchat: '寒暄', uncertain: '待判断' };
  // Below-threshold voiceprint ranking, shown only as a grey guess; it never fills an owner.
  const HINT_FLOOR = 0.35, HINT_MARGIN = 0.05;
  function speakerHintHtml(u) {
    const c = (u.speakerCandidates || []).filter((x) => x && typeof x.score === 'number' && Number.isFinite(x.score));
    if (u.speakerState !== 'unconfirmed' || !c.length || c[0].score < HINT_FLOOR) return '';
    const text = c.length < 2 || c[0].score - c[1].score >= HINT_MARGIN
      ? `可能是 ${speakerLabel(c[0].name)}` : `${speakerLabel(c[0].name)} / ${speakerLabel(c[1].name)}？`;
    return `<span class="spk spk-status spk-hint">${esc(text)} · ${c[0].score.toFixed(2)}</span>`;
  }
  function trialGuess(s) {
    const c = (s.candidates || []).filter((x) => x && typeof x.score === 'number' && Number.isFinite(x.score));
    return c.length && c[0].score >= HINT_FLOOR && (c.length < 2 || c[0].score - c[1].score >= HINT_MARGIN)
      ? `可能是 ${speakerLabel(c[0].name)}` : '身份待确认';
  }
  function speakerStatusHtml(u) {
    const hint = speakerHintHtml(u);
    if (hint) return hint;
    const text = {pending:'声纹识别中', unconfirmed:'声纹未确认', too_short:'语音太短',
      busy:'声纹请求拥挤', audio_unavailable:'声纹音频不可用', unsupported:'声纹服务不可用', error:'声纹请求失败'}[u.speakerState];
    if (!text) return '';
    const score = u.speakerState === 'unconfirmed' && typeof u.speakerScore === 'number' && Number.isFinite(u.speakerScore)
      ? ` · ${u.speakerScore.toFixed(2)}` : '';
    return `<span class="spk spk-status">${text}${score}</span>`;
  }
  function renderLine(id) {
    const u = S.records.get(id), li = S.lines.get(id);
    if (!u || !li) return;
    const speaker = speakerLabel(u.speaker);
    const experiment = !u.segments?.length && S.state.speakerPreviewEnabled && u.speakerPreview?.experimental && u.speakerPreview.segments?.length;
    const words = experiment ? `<span class="preview-note">说话人拆分试验${u.speakerPreview.jevUsed ? ' · Jev 辅助' : ''}</span>` +
      u.speakerPreview.segments.map((s) => `<span class="preview-segment${s.kind === 'overlap' ? ' preview-overlap' : ''}"><span class="spk spk-status">${esc(s.kind === 'overlap' ? '疑似多人同时说话' : speakerLabel(s.speaker) || '身份待确认')}${s.source === 'voiceprint+context' ? ' · 待核对' : ''}</span>${esc(s.text)}</span>`).join('') :
      u.segments?.length ? u.segments.map((s) => {
        const status = s.overlap ? '多人同时说' : s.tier === 'A' ? '高可信' : '建议 · 待核对';
        const names = s.overlap ? (s.candidates || []).map(c => c.name).join(' / ') : speakerLabel(s.speaker) || '身份待确认';
        return `<span class="speaker-segment tier-${esc(s.tier || 'C')}${s.approx ? ' approx-segment' : ''}"><span class="spk">${esc(names)} · ${status}</span>${esc(s.text)}</span>`;
      }).join('') :
      u.trialSegments?.length > 1 ? `<span class="preview-note">按人拆分（试验 · 建议）</span>` + u.trialSegments.map((s) => {
        const names = s.overlap ? (s.candidates || []).map(c => speakerLabel(c.name)).join(' / ') + ' · 多人同时说'
          : s.speaker ? `${speakerLabel(s.speaker)} · 建议` : trialGuess(s);
        return `<span class="speaker-segment trial-segment${s.overlap ? ' preview-overlap' : ''}"><span class="spk spk-status">${esc(names)}</span>${esc(s.text)}</span>`;
      }).join('') :
      `${speaker ? `<span class="spk">${esc(speaker)} · 待核对</span>` : ''}${speakerStatusHtml(u)}${esc(u.text)}`;
    li.querySelector('div').innerHTML = `${words}${TAG_LABELS[u.tag] ? ` <span class="badge value-tag">${TAG_LABELS[u.tag]}</span>` : ''}`;
  }

  function setSummary(p) {
    if (p.sessionId !== S.state.sessionId) return;
    p = { ...p, commitments: (p.commitments || []).map(u => u.reviewStatus === 'confirmed' ? u : { ...u, ...(S.ownerEdits.get(u.id) || {}) }) };
    S.summary = p; S.commitments = p.commitments || [];
    renderStatus();
    if (S.sheet?.kind === 'commitments') renderSheet();
  }

  function removeLine(id) {
    const li = S.lines.get(id);
    if (li) li.remove();
    S.lines.delete(id);
    S.records.delete(id);
  }

  function highlight(ids) {
    ids.forEach((id, i) => {
      const li = S.lines.get(id);
      if (!li) return;
      li.classList.add('hit');
      if (i === 0) li.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(() => li.classList.remove('hit'), 2600);
    });
  }

  function scrollToEnd() {
    if (!S.autoScroll) return;
    const t = $('transcript');
    t.scrollTop = t.scrollHeight;
  }

  $('transcript').addEventListener('scroll', () => {
    const t = $('transcript');
    S.autoScroll = t.scrollHeight - t.scrollTop - t.clientHeight < 60;
  });

  function setBusy(busy) {
    $('btn-oops').classList.toggle('busy', busy);
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

  function renderSheet() {
    const k = S.sheet?.kind;
    $('sheet').dataset.kind = k || '';
    const html = {
      pending: () => spinnerHtml('正在回溯刚才的对话…', '按时间和语义定位原话'),
      draftPending: () => spinnerHtml('正在整理行动草稿…', '提取负责人、事项、截止时间和约束'),
      notFound: notFoundHtml,
      card: cardHtml,
      draft: draftHtml,
      result: resultHtml,
      settings: settingsHtml,
      voiceprint: voiceprintHtml,
      saved: savedHtml,
      commitments: commitmentsHtml,
      historyDigest: () => commitmentsHtml(true),
      ask: askHtml,
      agenda: agendaHtml,
      history: historyHtml,
      notices: () => sheetHead('提醒记录') + noticeRecordsHtml([...S.notices.values()]),
    }[k];
    $('sheet').innerHTML = html ? html() : '';
    syncPlayButtons();
  }

  const spinnerHtml = (title, sub) =>
    `<div class="grabber"></div><div class="center"><div class="spinner"></div><p style="margin:0">${title}</p><p class="fine">${sub}</p></div>`;

  function commitmentsHtml(readonly = false) {
    const summary = readonly ? S.historySummary : S.summary;
    function group(title, rows, todo = false, ideas = false) {
      return `<p class="section-title">${title} · ${rows.length}</p><div class="cands">${(ideas ? rows.slice(0, S.showAllIdeas ? rows.length : 5) : rows).map(u => {
        const hint = todo ? `${u.ownerHint || u.owner_hint || '负责人待定'}${u.suggestedOwner || u.suggested_owner ? ' · 建议 ' + (u.suggestedOwner || u.suggested_owner) : ''} · ${u.reviewStatus === 'confirmed' || u.review_status === 'confirmed' ? '人工已确认' : u.ownerReviewStatus === 'selected' ? '人工已选择，待确认任务' : '待核对'} · ${u.whenRaw || u.when_raw || '截止待定'}${u.taskStatus === 'created' || u.task_status === 'created' ? ' · 已建飞书任务' : ''}` : '';
        return `<button class="cand" data-act="${readonly ? 'history-quote' : 'commitment-open'}" data-id="${esc(u.id)}"><span><span class="t">${esc(u.text)}</span><span class="meta">${clock(u.startMs ?? u.start_ms)}${u.speaker ? ' · ' + esc(speakerLabel(u.speaker)) : ''}${hint ? '<br>' + esc(hint) : ''}${u.audioExpired || readonly ? ' · 原声未保留' : ''}</span></span></button>`;
      }).join('') || '<p class="fine">暂无</p>'}</div>${ideas && rows.length > 5 && !S.showAllIdeas ? '<button class="chip ghost" data-act="ideas-all">展开全部想法</button>' : ''}`;
    }
    return `${sheetHead(readonly ? '往期会议 · ' + formatDate(S.history.started_at) : '本场纪要')}
      ${!readonly && S.state.tagsSupported === false ? '<p class="fine">当前后端不支持标签分析。</p>' : ''}
      ${!readonly && S.state.sessionTruncated ? '<p class="fine">文字索引已达上限，较早条目已移出。</p>' : ''}
      ${group('决定', summary.decisions || [])}${group('待办', summary.commitments || [], true)}${group('约束', summary.constraints || [])}${group('想法', summary.ideas || [], false, true)}
      <p class="section-title">提醒记录</p>${noticeRecordsHtml(summary.notices || [], readonly)}
      ${readonly ? `<button class="btn ghost" data-act="history-delete-prompt">删除此会议文字</button>${S.sheet.deleteConfirm ? '<p class="fine">团队成员将无法再查看这场会议。确认删除？</p><button class="btn ghost" data-act="history-delete-confirm">确认删除</button>' : ''}` : '<p class="footnote">本场索引保留到下次收音。开启文字同步后可在往期会议查看；原声最多暂存 50 段。</p>'}`;
  }

  function notFoundHtml() {
    return `<div class="grabber"></div>
      <div class="center"><p style="font-size:17px;font-weight:600;margin:0 0 4px">没找到</p><p class="fine">${esc(S.sheet.reason)}</p></div>
      <div class="actions two">
        <button class="btn ghost" data-act="close">知道了</button>
        <button class="btn primary" data-act="retry-recall">再试一次</button>
      </div>`;
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
    const candidates = d.single ? '' : `
      <p class="fine" style="margin:0">${d.trigger === 'conflict' ? '对照这两句原话：' : `有 ${d.items.length} 条接近，选一条：`}</p>
      <div class="cands">${d.items.map((it, i) => `
        <button class="cand ${i === c.sel ? 'on' : ''}" data-act="cand" data-i="${i}">
          <span class="radio"></span>
          <span><span class="t">${esc(it.text)}</span>
            <span class="meta"><span>${it.startedAt ? esc(formatDate(it.startedAt)) + ' 会议 · ' : ''}${clock(it.startMs)}</span>${it.speaker != null ? `<span>${esc(speakerLabel(it.speaker))}</span>` : ''}</span>
            <span class="prob"><i style="width:${Math.round(it.prob * 100)}%"></i></span></span>
        </button>`).join('')}
      </div>`;
    return `<div class="grabber"></div>
      <div class="sheet-head">
        <span class="kicker">${d.query ? `问答 · 「${esc(d.query)}」` : d.trigger === 'digest' ? '本场原话' : d.readOnly && d.trigger !== 'conflict' ? '往期原话' : d.trigger === 'conflict' ? '冲突原话' : 'Recall'}${item.startedAt ? ' · ' + esc(formatDate(item.startedAt)) + ' 会议' : ` · <span id="card-ago">${ago(S.state.sessionMs - item.endMs)}</span>`}</span>
        <button class="close" data-act="close" aria-label="关闭">✕</button>
      </div>
      ${candidates}
      <div class="quote">“${quote}”</div>
      <div class="meta">${speaker ? `<span>${esc(speaker)}</span>` : ''}<span>${clock(range.startMs)}–${clock(range.endMs)}</span><span class="badge">语音转写</span></div>
      <div class="row">
        <button class="chip" data-act="play" data-key="${esc(cardClipKey())}" ${item.audioAvailable === false || d.readOnly ? 'disabled' : ''}>${item.audioAvailable === false || d.readOnly ? '原声未保留' : '▶ 回听原声'}</button>
        <button class="chip ghost" data-act="prev" ${canPrev ? '' : 'disabled'}>＋前一句</button>
        <button class="chip ghost" data-act="next" ${canNext ? '' : 'disabled'}>＋后一句</button>
        ${c.before || c.after ? '<button class="chip ghost" data-act="reset">还原</button>' : ''}
      </div>
      <div class="actions">
        <button class="btn ghost" data-act="dismiss">丢弃</button>
        <button class="btn secondary" data-act="save" ${d.readOnly ? 'disabled' : ''}>保存</button>
        <button class="btn primary" data-act="draft" ${d.readOnly ? 'disabled' : ''}>生成行动 →</button>
      </div>
      <p class="footnote">${d.readOnly ? '往期文字 · 只读' : '本场原话 · 可保存或起草行动'} · 召回 ${(d.latencyMs / 1000).toFixed(1)} 秒 · ${esc(d.engine)}</p>`;
  }

  // ---- clips --------------------------------------------------------------------------------

  function toggleClip(key, range) {
    if (S.playingKey === key) send('clip.stop');
    else send('clip.play', { key, startMs: range.startMs, endMs: range.endMs });
  }

  function onClip(p) {
    if (p.state === 'playing') S.playingKey = p.key;
    else if (S.playingKey === p.key) S.playingKey = null;
    if (p.state === 'expired') toast('原声已过期（只保留最近 5 分钟）', true);
    syncPlayButtons();
  }

  /** Updates play buttons in place so form edits in the sheet survive. */
  function syncPlayButtons() {
    document.querySelectorAll('#sheet [data-key]').forEach((btn) => {
      const playing = S.playingKey === btn.dataset.key;
      if (btn.disabled) return;
      if (btn.dataset.act === 'play') btn.textContent = playing ? '■ 停止' : '▶ 回听原声';
      else btn.textContent = playing ? '■' : '▶';
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
      send('action.ownerFeedback', {draftId: model.draftId, previousOwner, selectedOwner: value || '',
        modelSuggestion: model.suggestedOwner || model.who || '', modelTier: model.tier || 'C',
        modelConfidence: model.ownerConfidence ?? null, modelRevision: model.modelRevision || 0});
    }
    if (field === 'who' || field === 'whenIso') {
      (S.draft.data.evidence?.utteranceIds || []).forEach(id => {
        const edit = { ...(S.ownerEdits.get(id) || {}) };
        if (field === 'who') { edit.ownerHint = value; edit.ownerReviewStatus = value ? 'selected' : 'unreviewed'; }
        else edit.whenRaw = value;
        S.ownerEdits.set(id, edit);
      });
      setSummary(S.summary);
    }
    return true;
  }

  // Context resolution is a model proposal whose quotes the server verified; it never confirms anything.
  function taskContextHtml(c) {
    if (!c) return '';
    const role = { speaker: '说话人自己承诺', addressee: '说话人要求对方去做', named: '原话点名', unknown: '负责人未定' }[c.ownerRole] || '负责人未定';
    const who = c.clauseSpeaker ? `这句由 ${esc(c.clauseSpeaker)} 说（试验拆分，待核对）· ` : '';
    return `<div class="task-context">
      <p class="fine">任务原话：“${esc(c.quote)}” · ${who}${esc(role)}</p>
      ${(c.resolutions || []).map(r => `<p class="fine">“${esc(r.phrase)}”→ ${esc(r.meaning)}（依据：“${esc(r.evidence)}”）</p>`).join('')}
      ${(c.missing || []).length ? `<p class="warn-text">还缺：${c.missing.map(esc).join('；')}</p>` : ''}
      ${(c.otherTasks || []).length ? `<p class="fine">这句还提到：${c.otherTasks.map(esc).join('；')}</p>` : ''}
    </div>`;
  }

  function draftHtml() {
    const { data: d, status, error } = S.draft;
    const names = [...new Set([...(d.ownerCandidates || []).map(c => c.name), ...(d.assigneeCandidates || []), ...(d.who ? [d.who] : []), ...(d.suggestedOwner ? [d.suggestedOwner] : [])])];
    const options = ['<option value="">请选择负责人</option>']
      .concat(names.map((n) => `<option ${n === d.who ? 'selected' : ''}>${esc(n)}</option>`))
      .join('');
    const sending = status === 'sending';
    const submitted = status === 'waiting' || status === 'failed' || sending;
    return `<div class="grabber"></div>
      <div class="sheet-head"><span class="kicker">Action · 待你确认</span><button class="close" data-act="close" aria-label="关闭">✕</button></div>
      <p class="owner-tier tier-${esc(d.tier || 'C')}">${d.tier === 'A' ? 'A · 模型高可信' : d.tier === 'B' ? 'B · 建议，请核对' : 'C · 模型未确认负责人'}${S.draft.reviewStatus === 'confirmed' ? ' · 人工已确认' : ' · 待你确认'}</p>
      <p class="fine">${esc(d.reason || '请核对负责人')}${typeof d.ownerConfidence === 'number' ? ` · 模型建议可信度 ${(d.ownerConfidence * 100).toFixed(0)}%` : ''}</p>
      ${d.suggestedOwner || d.ownerCandidates?.length ? `<div class="owner-candidates">${[...new Set([d.suggestedOwner, ...(d.ownerCandidates || []).map(c => c.name)].filter(Boolean))].slice(0, 2).map(n => `<button class="btn ghost" data-act="choose-owner" data-name="${esc(n)}" ${submitted ? 'disabled' : ''}>建议 ${esc(n)}</button>`).join('')}</div>` : ''}
      <div class="evidence"><button data-act="play-evidence" data-key="ev-${esc(d.draftId)}" aria-label="回听原声">▶</button><span>“${esc(d.evidence.quote)}”</span></div>
      ${taskContextHtml(d.taskContext)}
      <label class="field"><span>负责人</span><select id="f-who" ${submitted ? 'disabled' : ''}>${options}</select></label>
      <label class="field"><span>事项</span><textarea id="f-what" ${submitted ? 'disabled' : ''}>${esc(d.what)}</textarea></label>
      <label class="field"><span>截止时间</span><input type="date" id="f-when" ${submitted ? 'disabled' : ''} value="${esc(d.whenIso || '')}">${d.whenRaw ? `<small>原话：${esc(d.whenRaw)}</small>` : ''}</label>
      <label class="field"><span>约束</span><input id="f-constraint" ${submitted ? 'disabled' : ''} value="${esc(d.constraint || '')}" placeholder="例如：预算不超过 10 万"></label>
      <p class="fine">只有你点确认后才会写入飞书；同一次确认只会建一条任务。</p>
      ${error ? `<p class="error-text">${esc(error)}</p>` : ''}
      <div class="actions two">
        <button class="btn ghost" data-act="close">取消</button>
        <button class="btn primary" data-act="${status === 'waiting' ? 'query-action' : 'confirm'}" ${sending ? 'disabled' : ''}>${sending ? '正在查询…' : status === 'waiting' ? '查询创建结果' : status === 'failed' ? '重试同一操作' : '确认创建飞书任务'}</button>
      </div>`;
  }

  function confirmDraft() {
    const d = S.draft.data;
    const who = $('f-who').value.trim();
    const what = $('f-what').value.trim();
    if (!who || !what) {
      toast('请先选负责人、填写事项', true);
      return;
    }
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
    return `<div class="grabber"></div>
      <div class="center">
        <div class="success">✓</div>
        <p style="font-size:18px;font-weight:700;margin:4px 0">${r.mock ? 'Mock 任务已生成' : '已创建飞书任务'}</p>
        <p class="meta" style="justify-content:center">${esc(d.who)} · ${esc(d.whenIso || '未设截止时间')}</p>
        <p style="margin:10px 0 0">${esc(d.what)}</p>
        ${r.message ? `<p class="fine">${esc(r.message)}</p>` : ''}
      </div>
      <div class="actions two">
        <button class="btn ghost" data-act="close">完成</button>
        <button class="btn primary" data-act="open-task" ${r.taskUrl && !r.mock ? '' : 'disabled'}>在飞书中打开</button>
      </div>
      <p class="footnote">操作编号 ${esc(String(r.opId).slice(0, 8))} · ${r.readBackConfirmed ? '已回读确认' : '尚未回读核验'}</p>`;
  }

  // ---- settings & saved -------------------------------------------------------------------

  function settingsHtml() {
    const s = S.state;
    const l = S.latency;
    const latency = l && l.recentMs && l.recentMs.length
      ? `中位数 ${(l.medianMs / 1000).toFixed(2)} 秒 · 最近 ${l.recentMs.map((ms) => (ms / 1000).toFixed(1)).join(' / ')} 秒`
      : '还没有召回记录';
    return `<div class="grabber"></div>
      <div class="sheet-head"><span class="kicker">调试设置</span><button class="close" data-act="close" aria-label="关闭">✕</button></div>
      <button class="btn primary" data-act="vp-open">声纹录入与匹配测试</button>
      <p class="fine">由本人按提示朗读，录入后可以重新测试谁在说话。</p>
      <label class="field"><span>音源（保存后立即切换，保留本场记录）</span>
        <select id="set-source">
          <option value="mic" ${s.source === 'mic' ? 'selected' : ''}>手机麦克风</option>
          <option value="wav" ${s.source === 'wav' ? 'selected' : ''}>测试 WAV（预录音频）</option>
          <option value="remote" ${s.source === 'remote' ? 'selected' : ''}>Mac 收音（经 USB 接收电脑上的麦克风）</option>
        </select></label>
      <label class="field"><span>Mac 收音地址（配合 adb reverse 用 127.0.0.1）</span><input id="set-remote" inputmode="url" placeholder="127.0.0.1:7790" value="${esc(s.remoteAddress || '127.0.0.1:7790')}"></label>
      <p class="fine">测试音频：${esc(s.testWavName || '内置合成对话')} · 需要 PCM 16kHz / 单声道 / 16bit WAV</p>
      <div class="row"><button class="chip" data-act="pick-audio">选择测试 WAV</button><button class="chip ghost" data-act="reset-audio">恢复内置音频</button></div>
      <label class="switch"><span>说「Oops」触发找回/提问</span><input type="checkbox" id="set-voice" ${s.voiceTrigger !== false ? 'checked' : ''}></label>
      <label class="switch"><span>提醒短震动</span><input type="checkbox" id="set-vibrate" ${s.noticeVibrate !== false ? 'checked' : ''}></label>
      <label class="switch"><span>会议文字自动同步</span><input type="checkbox" id="set-sync" ${s.memorySync !== false ? 'checked' : ''}></label>
      <p class="fine">会把转写文字、标签、说话人和提醒存到团队服务器，不含音频，可在往期会议里删除。</p>
      <label class="switch"><span>使用 Mock 后端</span><input type="checkbox" id="set-mock" ${s.backend === 'mock' ? 'checked' : ''}></label>
      <label class="field"><span>后端地址</span><input id="set-url" inputmode="url" placeholder="http://192.168.1.10:8000" value="${esc(s.backendUrl || '')}"></label>
      <label class="field"><span>服务访问凭据</span><input id="set-token" type="password" autocomplete="off" placeholder="${s.hasBackendToken ? '已配置；留空保留原凭据' : '远程服务提供的访问凭据'}"></label>
      <label class="switch"><span>清除当前地址的访问凭据</span><input type="checkbox" id="set-clear-token"></label>
      <label class="switch"><span>说话人区分（实验：测试中会丢句）</span><input type="checkbox" id="set-spk" ${s.speakerInfo ? 'checked' : ''}></label>
      <label class="switch"><span>实时换人拆分与重叠提示（试验）</span><input type="checkbox" id="set-preview" ${s.speakerPreviewEnabled ? 'checked' : ''}></label>
      <label class="switch"><span>保存原始录音与转写（会后自动上传团队服务器，约 1.9 MB/分钟）</span><input type="checkbox" id="set-record" ${s.recordSession ? 'checked' : ''}></label>
      <p class="fine">原始录音：${s.recordingsPending ? `${s.recordingsPending} 场待上传${s.recordingUpload === 'uploading' ? '（上传中）' : ''}` : '全部已上传'}；未上传成功的录音不会被自动清理。</p>
      <div class="actions two" style="margin-top:16px">
        <button class="btn ghost" data-act="close">取消</button>
        <button class="btn primary" data-act="apply-settings">保存</button>
      </div>
      <p class="section-title" style="margin-top:18px">召回耗时（验收：中位数 ≤ 5 秒）</p>
      <p class="diag">${esc(latency)}</p>
      <p class="section-title" style="margin-top:12px">诊断</p>
      <p class="diag" id="diag">${esc(diagText())}</p>`;
  }

  const ENROLL_TEXT = '现在录入我的声纹，这段话只用于声音测试。早晨的阳光照进房间，窗外有人散步，远处传来汽车的声音。桌上放着一本书、一杯水和一支笔。我用平时说话的声音朗读，保持自然的语速和音量。读完后稍作停顿，再从头朗读一次。';
  const TEST_TEXT = '午后的公园很安静，树叶随着微风轻轻摇动。小路旁有几朵花，远处的孩子正在玩耍。我现在用另一段文字测试声音匹配。';

  function voiceprintHtml() {
    const v = S.state.voiceprint || { phase: 'idle' };
    const busy = ['loading', 'saving', 'testing', 'checking'].includes(v.phase);
    const recording = v.phase === 'recording';
    const name = ['recording', 'checking', 'review', 'saving', 'testing'].includes(v.phase)
      ? (v.name || S.voiceprintName) : (S.voiceprintName || v.name);
    const people = v.catalog?.people || [];
    const limit = v.mode === 'test' ? 10 : 30;
    const message = busy ? ({loading: '正在读取声纹列表…', saving: '正在检查并保存声纹…', testing: '正在比较四人的声纹…', checking: '正在检查录音质量…'})[v.phase] : v.message;
    const results = v.diagnosis ? `<p class="section-title">这段测试录音的结果</p>
      <p class="diag">${esc(v.diagnosis.speaker ? '当前判断：' + v.diagnosis.speaker : '当前判断：未知')}<br>
      最接近：${esc(v.diagnosis.closest || '暂无声纹')} · 相似度 ${Number(v.diagnosis.score).toFixed(3)}<br>${esc(v.diagnosis.reason)}</p>
      <div class="cands">${(v.diagnosis.scores || []).map(x => `<div class="cand"><span>${esc(x.name)}</span><span>${Number(x.score).toFixed(3)}</span></div>`).join('')}</div>
      <p class="fine">当前门槛 ${v.diagnosis.accept}，领先幅度至少 ${v.diagnosis.required_margin}；相似度不是正确概率，请核对真实说话人。</p>` : '';
    return `<div class="grabber"></div><div class="sheet-head"><span class="kicker">声纹录入与测试</span>
      <button class="close" data-act="close" aria-label="关闭">✕</button></div>
      <label class="field"><span>本人姓名</span><select id="vp-name" ${busy || recording || v.phase === 'review' ? 'disabled' : ''}>
        <option value="">请选择正在朗读的人</option>${people.map(p => `<option value="${esc(p.name)}" ${name === p.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
      <p class="fine">${recording ? '正在使用' : '录入音源'}：${esc(SOURCE_TEXT[v.source || (S.state.capturing ? S.state.source : "mic")] || '手机麦克风')}。请仅由本人朗读，避免旁人讲话或播放录音。</p>
      <p class="section-title">${v.mode === 'test' ? '测试朗读文字（约 10 秒）' : '录入朗读文字（约 30 秒）'}</p>
      <p class="diag">${v.mode === 'test' ? TEST_TEXT : `我是${esc(name || '（请先选姓名）')}。${ENROLL_TEXT}`}</p>
      ${recording ? `<p class="section-title">已录 ${clock(v.elapsedMs)} / 0:${limit} · ${S.state.sourceReady ? '正在收到音频' : '等待麦克风连接'}</p>
        <button class="btn ghost" data-act="vp-stop">结束这段录音</button>` : ''}
      ${v.phase === 'review' ? `<p class="fine">已录 ${clock(v.elapsedMs)}。${v.mode === 'test' ? '测试只比较声音，不会更新声纹。' : `保存后替换 ${esc(name)} 的现有声纹，其他成员保留。`}</p>
        <div class="actions two"><button class="btn ghost" data-act="vp-cancel">放弃并重录</button><button class="btn primary" data-act="vp-save" ${(v.mode === 'test' ? v.elapsedMs < 1000 : !v.qualityPassed) ? 'disabled' : ''}>${v.mode === 'test' ? '重试匹配' : '保存声纹'}</button></div>` : ''}
      ${!recording && !busy && v.phase !== 'review' ? `<label class="switch"><span>由本人单独朗读，并同意上传声音用于声纹匹配</span><input id="vp-consent" type="checkbox"></label>
        <div class="actions two"><button class="btn primary" data-act="vp-record" ${people.length ? '' : 'disabled'}>开始录入 · 30 秒</button>
        <button class="btn ghost" data-act="vp-test" ${people.length ? '' : 'disabled'}>测试匹配 · 10 秒</button></div>` : ''}
      ${v.quality ? `<p class="fine">录音 ${v.quality.seconds} 秒 · 有效音量片段 ${v.quality.active_seconds} 秒</p>` : ''}
      ${message ? `<p class="diag" role="status">${esc(message)}</p>` : ''}
      ${results}
      <p class="section-title">已注册成员</p><div class="cands">${people.map(p => `<div class="cand"><span>${esc(p.name)}<span class="meta">${p.registered ? `${esc(SOURCE_TEXT[p.source] || p.source || '')} · ${p.seconds} 秒` : '未注册'}</span></span>
        ${p.registered ? `<button class="chip ghost" data-act="vp-delete-prompt" data-name="${esc(p.name)}" ${busy || recording || v.phase === 'review' ? 'disabled' : ''}>删除</button>` : ''}</div>`).join('')}</div>
      ${S.voiceprintDelete ? `<p class="fine">确认删除 ${esc(S.voiceprintDelete)} 的声纹？删除后需重新录入才能匹配此人。</p>
        <button class="btn ghost" data-act="vp-delete-confirm">确认删除 ${esc(S.voiceprintDelete)}</button>` : ''}
      <button class="chip ghost" data-act="vp-refresh" ${busy || recording || v.phase === 'review' ? 'disabled' : ''}>刷新列表</button>
      <p class="footnote">服务器只保存声纹特征，临时录音用于提取或测试后清除。音量、时长及片段一致性检查不能验证本人身份，也不能保证识别准确。关闭本页会放弃尚未保存的录入；日常收音不受影响。</p>`;
  }

  function recordVoiceprint(mode) {
    const name = $('vp-name').value;
    if (mode === 'enroll' && !name) return toast('请先选择本人姓名');
    if (!$('vp-consent').checked) return toast('请先确认本人同意上传声音');
    S.voiceprintName = name;
    S.voiceprintDelete = null;
    send('voiceprint.record', { name, consent: true, mode });
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
    closeSheet();
  }

  function savedHtml() {
    const items = S.sheet.items;
    const body = items.length
      ? items.map((it) => `<div class="saved-item">
          <div>${esc((it.utterances || []).map((u) => u.text).join(' '))}</div>
          <div class="meta"><span>${esc(formatDate(it.saved_at))}</span>${it.audio_expired ? '<span>原声已过期</span>' : '<span class="badge">含原声</span>'}</div>
        </div>`).join('')
      : '<p class="fine">还没有保存的片段。召回后点「保存」才会留在这里。</p>';
    return `<div class="grabber"></div>
      <div class="sheet-head"><span class="kicker">已保存 · 长期记忆</span><button class="close" data-act="close" aria-label="关闭">✕</button></div>
      ${historyTabs()}${body}`;
  }

  const sheetHead = (title) => `<div class="grabber"></div><div class="sheet-head"><span class="kicker">${esc(title)}</span><button class="close" data-act="close" aria-label="关闭">✕</button></div>`;
  function askHtml() {
    return `${sheetHead('问答 · 从原话找答案')}
      <label class="field"><span>范围</span><select id="ask-scope"><option value="session">本场</option><option value="history">往期会议</option></select></label>
      <label class="field"><span>想问什么？</span><textarea id="ask-query" maxlength="500" placeholder="预算上限是多少？谁负责新方案？"></textarea></label>
      <div class="row"><button class="chip ghost" data-act="ask-example" data-query="预算上限是多少？">预算上限</button><button class="chip ghost" data-act="ask-example" data-query="谁负责新方案？">谁来负责</button></div>
      <button class="btn primary" data-act="ask-submit">查找原话</button><p class="fine">答案只引用已记录的原话。本场停止收音后仍可提问，下一场开始会清空本场索引。</p>`;
  }
  function agendaHtml() {
    const a = S.state.agenda || {};
    return `${sheetHead('本场目标与议程')}
      <label class="field"><span>本场目标</span><input id="agenda-goal" maxlength="500" value="${esc(a.goal || '')}" placeholder="确认上线方案与负责人"></label>
      <label class="field"><span>议程 · 每行一项，可加分钟数</span><textarea id="agenda-items" rows="6" placeholder="方案评审 10&#10;负责人确认（5分钟）">${esc((a.items || []).map(i => i.title + (i.plannedMs ? ` ${i.plannedMs / 60000}` : '')).join('\n'))}</textarea></label>
      <label class="field"><span>会议计划时长（分钟，可留空）</span><input id="agenda-minutes" type="number" min="1" max="480" value="${a.plannedMs ? a.plannedMs / 60000 : ''}"></label>
      <p class="fine">最多 20 项，每项标题 60 字，时长 1–480 分钟。收音中也可以修改；已讨论时长会保留。</p>
      <div class="actions two"><button class="btn ghost" data-act="agenda-clear">清除议程</button><button class="btn primary" data-act="agenda-save">保存议程</button></div>`;
  }
  function saveAgenda() {
    const lines = $('agenda-items').value.split('\n').map(x => x.trim()).filter(Boolean);
    if (lines.length > 20) return toast('最多 20 项议程', true);
    const items = lines.map(line => {
      const m = line.match(/^(.*?)(?:[（(]\s*(\d+)\s*分钟\s*[）)]|\s+(\d+))\s*$/);
      return { title: m ? m[1].trim() : line, minutes: m ? Number(m[2] || m[3]) : null };
    });
    const minutes = $('agenda-minutes').value ? Number($('agenda-minutes').value) : null;
    if (items.some(i => !i.title || i.title.length > 60) || [...items.map(i => i.minutes), minutes].some(n => n !== null && (!Number.isInteger(n) || n < 1 || n > 480))) return toast('请检查标题和分钟数', true);
    send('agenda.set', {goal: $('agenda-goal').value.trim(), items, minutes}); closeSheet();
  }
  let noticeTimer;
  function showNotice(n) {
    S.notices.set(n.id, n);
    S.activeNotices = [...S.activeNotices.filter(x => x !== n.id), n.id].slice(-2);
    renderNoticeBanners();
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { S.activeNotices = []; renderNoticeBanners(); }, 12000);
  }
  function noticeButtons(n) {
    return `<div class="row">${n.evidenceIds?.length ? `<button class="chip" data-act="notice-open" data-id="${esc(n.id)}">查看原话</button>` : ''}
      <button class="chip ghost" data-act="notice-useful" data-id="${esc(n.id)}" ${n.useful === true ? 'disabled' : ''}>有用</button><button class="chip ghost" data-act="notice-wrong" data-id="${esc(n.id)}" ${n.useful === false ? 'disabled' : ''}>不准</button>
      <button class="chip ghost" data-act="notice-dismiss" data-id="${esc(n.id)}" aria-label="关闭提醒">✕</button></div>`;
  }
  function renderNoticeBanners() {
    $('notices').innerHTML = S.activeNotices.map(id => S.notices.get(id)).filter(n => n && !n.dismissed).map(n =>
      `<div class="notice-banner"><b>${esc(n.title)}</b><p>${esc(n.detail)}</p>${noticeButtons(n)}</div>`).join('');
    const count = [...S.notices.values()].filter(n => !n.dismissed).length;
    $('btn-notices').hidden = !count; $('btn-notices').textContent = `提醒 ${count}`;
  }
  function noticeRecordsHtml(rows, readonly = false) {
    return rows.length ? rows.map(n => `<div class="saved-item"><b>${esc(n.title)}</b><p class="fine">${esc(n.detail)}</p><span class="meta">${clock(n.atMs ?? n.at_ms)}${n.useful === false ? ' · 已反馈不准' : n.useful === true ? ' · 已反馈有用' : ''}${n.dismissed ? ' · 已收起' : ''}</span>${readonly ? '' : noticeButtons(n)}</div>`).join('') : '<p class="fine">暂无提醒</p>';
  }
  const historyTabs = () => `<div class="row"><button class="chip ghost" data-act="saved-list">已保存片段</button><button class="chip" data-act="history-list">往期会议</button></div>`;
  function historyHtml() {
    return `${sheetHead('往期会议 · 团队文字存档')}${historyTabs()}
      ${(S.sheet.items || []).map(i => `<button class="cand" data-act="history-open" data-id="${esc(i.session_id)}"><span class="t">${esc(i.goal || '会议')}</span><span class="meta">${esc(formatDate(i.started_at))} · ${i.utterance_count} 句</span></button>`).join('') || '<p class="fine">还没有同步的会议文字。</p>'}
      <p class="footnote">团队共享。会议文字与原始录音长期保留在团队服务器，删除会议时一并删除。</p>`;
  }
  function historyOpen(p) {
    S.history = p;
    S.historySummary = { commitments: p.digest?.commitment || [], decisions: p.digest?.decision || [], constraints: p.digest?.constraint || [], ideas: p.digest?.idea || [], notices: p.notices || [] };
    openSheet({kind: 'historyDigest'});
  }
  function historySearch(p) {
    if (!p.items?.length) return openSheet({kind: 'notFound', reason: '往期会议原话里没有找到答案'});
    const items = p.items.map(i => ({id: i.id, text: i.text, startMs: i.start_ms, endMs: i.end_ms, speaker: i.speaker,
      prob: i.prob, audioAvailable: false, startedAt: i.started_at}));
    openCard({id: 'history-search', trigger: 'ask', query: p.query, nowMs: 0, items, pool: items, single: items.length === 1,
      engine: p.engine, latencyMs: 0, readOnly: true});
  }

  // ---- errors -------------------------------------------------------------------------------

  function onError(p) {
    toast(p.message, true);
    if (p.scope === 'history' && S.sheet?.kind === 'pending') closeSheet();
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

  const ACTIONS = {
    'ask-example': el => { $('ask-query').value = el.dataset.query; },
    'ask-submit': () => { const query = $('ask-query').value.trim(); if (!query) return toast('请输入问题'); const history = $('ask-scope').value === 'history'; openSheet({kind: 'pending'}); send(history ? 'history.search' : 'ask.session', {query}); },
    'agenda-save': saveAgenda,
    'agenda-clear': () => {send('agenda.clear'); closeSheet();},
    'ideas-all': () => {S.showAllIdeas = true; renderSheet();},
    'history-list': () => {openSheet({kind: 'pending'}); send('history.list');},
    'saved-list': () => send('saved.list'),
    'history-open': el => {openSheet({kind: 'pending'}); send('history.open', {sessionId: el.dataset.id});},
    'history-delete-prompt': () => {S.sheet.deleteConfirm = true; renderSheet();},
    'history-delete-confirm': () => send('history.delete', {sessionId: S.history.session_id, confirm: true}),
    'history-quote': el => { const u = S.history.utterances.find(x => x.id === el.dataset.id); if (u) historySearch({query: '', engine: 'archive', items: [{...u, prob: 1, started_at: S.history.started_at}]}); },
    'notice-open': el => { const n = S.notices.get(el.dataset.id); if (n?.evidenceIds?.length) send('digest.openMany', {ids: n.evidenceIds}); },
    'notice-useful': el => noticeFeedback(el.dataset.id, true),
    'notice-wrong': el => noticeFeedback(el.dataset.id, false),
    'notice-dismiss': el => {send('notice.dismiss', {id: el.dataset.id}); const n = S.notices.get(el.dataset.id); if (n) n.dismissed = true; renderNoticeBanners();},
    close: closeSheet,
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
    'choose-owner': (el) => {
      if (editDraftField('who', el.dataset.name)) renderSheet();
    },
    'vp-open': () => { S.voiceprintDelete = null; openSheet({ kind: 'voiceprint' }); send('voiceprint.list'); },
    'vp-refresh': () => send('voiceprint.list'),
    'vp-record': () => recordVoiceprint('enroll'),
    'vp-test': () => recordVoiceprint('test'),
    'vp-stop': () => send('voiceprint.stop'),
    'vp-save': () => send('voiceprint.save'),
    'vp-cancel': () => send('voiceprint.cancel'),
    'vp-delete-prompt': (el) => { S.voiceprintDelete = el.dataset.name; renderSheet(); },
    'vp-delete-confirm': () => { send('voiceprint.delete', {name: S.voiceprintDelete, confirm: true}); S.voiceprintDelete = null; },
    'apply-settings': applySettings,
    'pick-audio': () => send('testAudio.pick'),
    'reset-audio': () => send('testAudio.reset'),
  };

  function noticeFeedback(id, useful) {
    send('notice.feedback', {id, useful}); const n = S.notices.get(id); if (n) n.useful = useful;
    renderNoticeBanners(); if (['notices', 'commitments'].includes(S.sheet?.kind)) renderSheet();
  }
  $('notices').addEventListener('click', e => {const el = e.target.closest('[data-act]'); if (el && !el.disabled) ACTIONS[el.dataset.act]?.(el);});
  $('btn-ask').addEventListener('click', () => openSheet({kind: 'ask'}));
  $('btn-agenda').addEventListener('click', () => openSheet({kind: 'agenda'}));
  $('btn-notices').addEventListener('click', () => openSheet({kind: 'notices'}));
  $('sheet').addEventListener('change' , (e) => {
    if (e.target.id === 'vp-name') { S.voiceprintName = e.target.value; renderSheet(); }
  });

  $('sheet').addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el || el.disabled) return;
    const act = ACTIONS[el.dataset.act];
    if (act) act(el);
  });
  $('backdrop').addEventListener('click', closeSheet);
  $('sheet').addEventListener('input', (e) => {
    const field = { 'f-who': 'who', 'f-what': 'what', 'f-when': 'whenIso', 'f-constraint': 'constraint' }[e.target.id];
    if (field) editDraftField(field, e.target.value || (field === 'whenIso' ? null : ''));
  });
  $('sheet').addEventListener('change', (e) => {
    if (e.target.id === 'f-who') editDraftField('who', e.target.value);
  });
  $('btn-capture').addEventListener('click', () => {
    if (S.state.capturing) {
      const count = S.commitments.filter(u => u.reviewStatus !== 'confirmed' && u.review_status !== 'confirmed').length;
      if (count) toast(`${count} 条负责人待确认`);
    }
    send(S.state.capturing ? 'capture.stop' : 'capture.start');
  });
  $('btn-oops').addEventListener('click', () => send('recall.trigger'));
  $('btn-saved').addEventListener('click', () => send('saved.list'));
  $('btn-commitments').addEventListener('click', () => openSheet({ kind: 'commitments' }));
  $('btn-settings').addEventListener('click', () => openSheet({ kind: 'settings' }));

  send('hello');
})();
