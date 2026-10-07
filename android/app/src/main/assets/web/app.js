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
    sheet: null, // { kind, ... }
    card: null, // { data, sel, before, after }
    draft: null, // { data, status: editing|sending|done, result, error }
    playingKey: null,
    latency: null,
    autoScroll: true,
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
      case 'asr.speaker': setLineSpeaker(p.id, p.speaker, p.segments); break;
      case 'asr.tag':
        if (S.records.has(p.id)) {
          S.records.set(p.id, { ...S.records.get(p.id), ...p });
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
    S.state = { ...S.state, ...s };
    renderStatus();
  }

  function resetTranscript() {
    $('lines').innerHTML = '';
    S.lines.clear();
    S.records.clear();
    S.commitments = [];
    $('partial').textContent = '';
    $('pruned-note').hidden = true;
    $('empty').hidden = false;
    S.autoScroll = true;
  }

  function renderStatus() {
    const s = S.state;
    $('pill').classList.toggle('on', !!s.capturing);
    $('capture-text').textContent = s.capturing ? '收音中' : '未收音';
    const sourceLabel = SOURCE_TEXT[s.source] || s.source || '';
    // External sources report their link state (e.g. "Mac 收音：Wireless microphone").
    $('source-text').textContent = s.capturing && s.sourceDetail ? s.sourceDetail : sourceLabel;
    $('level').style.width = `${Math.round((s.capturing ? s.level : 0) * 100)}%`;
    $('buffer-text').textContent = `缓存 ${clock(s.bufferedMs)} / ${clock(s.retentionMs)}`;
    const [text, cls] = ASR_TEXT[s.asr] || [s.asr, ''];
    const asr = $('asr-text');
    asr.textContent = text;
    asr.className = `asr ${cls}`;

    const banner = $('gap-banner');
    const gaps = s.asrGaps || [];
    if (s.asr === 'unconfigured' || s.asr === 'error') {
      banner.hidden = false;
      banner.textContent = s.asrDetail || text;
    } else if (gaps.length) {
      const g = gaps[gaps.length - 1];
      banner.hidden = false;
      banner.textContent = `识别中断过 ${gaps.length} 次，最近一次 ${clock(g.fromMs)}–${clock(g.toMs)} 没有转写`;
    } else {
      banner.hidden = true;
    }

    const capture = $('btn-capture');
    capture.textContent = s.capturing ? '停止收音' : '开始收音';
    capture.classList.toggle('stop', !!s.capturing);
    $('saved-count').textContent = s.savedCount ?? 0;
    const commitments = $('btn-commitments');
    commitments.hidden = !!s.capturing || !s.sessionId ||
      (!(s.analysisPending || s.analysisFailed || s.commitmentCount) && s.tagsSupported == null);
    commitments.textContent = s.tagsSupported === false ? '当前后端不支持承诺分析' :
      `本场发现 ${s.commitmentCount || 0} 个承诺${s.analysisPending ? ` · 另有 ${s.analysisPending} 句还在分析` : ''}${s.analysisFailed ? ` · ${s.analysisFailed} 句分析失败` : ''}`;
    commitments.disabled = !S.commitments.length;

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
  function setLineSpeaker(id, speaker, segments = []) {
    const u = S.records.get(id);
    if (!u) return;
    u.speaker = speaker;
    u.segments = segments;
    renderLine(id);
  }

  const TAG_LABELS = { commitment: '承诺/指派', decision: '决定', idea: '想法', constraint: '约束', chitchat: '寒暄', uncertain: '待判断' };
  function renderLine(id) {
    const u = S.records.get(id), li = S.lines.get(id);
    if (!u || !li) return;
    const speaker = speakerLabel(u.speaker);
    const words = u.segments?.length ? u.segments.map((s) => `<span class="${s.approx ? 'approx-segment' : ''}"><span class="spk">${esc(speakerLabel(s.speaker) || '未知')}</span>${esc(s.text)}</span>`).join('') :
      `${speaker ? `<span class="spk">${esc(speaker)}</span>` : ''}${esc(u.text)}`;
    li.querySelector('div').innerHTML = `${words}${TAG_LABELS[u.tag] ? ` <span class="badge value-tag">${TAG_LABELS[u.tag]}</span>` : ''}`;
  }

  function setSummary(p) {
    if (p.sessionId !== S.state.sessionId) return;
    S.commitments = p.commitments || [];
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
    S.sheet = sheet;
    renderSheet();
    $('sheet').hidden = false;
    $('backdrop').hidden = false;
  }

  function closeSheet() {
    if (S.playingKey) send('clip.stop');
    S.sheet = null;
    $('sheet').hidden = true;
    $('backdrop').hidden = true;
  }

  function renderSheet() {
    const k = S.sheet?.kind;
    const html = {
      pending: () => spinnerHtml('正在回溯刚才的对话…', '按时间和语义定位原话'),
      draftPending: () => spinnerHtml('正在整理行动草稿…', '提取负责人、事项、截止时间和约束'),
      notFound: notFoundHtml,
      card: cardHtml,
      draft: draftHtml,
      result: resultHtml,
      settings: settingsHtml,
      saved: savedHtml,
      commitments: commitmentsHtml,
    }[k];
    $('sheet').innerHTML = html ? html() : '';
    syncPlayButtons();
  }

  const spinnerHtml = (title, sub) =>
    `<div class="grabber"></div><div class="center"><div class="spinner"></div><p style="margin:0">${title}</p><p class="fine">${sub}</p></div>`;

  function commitmentsHtml() {
    return `<div class="grabber"></div><div class="sheet-head"><span class="kicker">本场承诺与指派</span><button class="close" data-act="close" aria-label="关闭">✕</button></div>
      ${S.state.sessionTruncated ? '<p class="fine">本场文字索引已达上限，较早条目已移出列表。</p>' : ''}
      <div class="cands">${S.commitments.map((u) => `<button class="cand" data-act="commitment-open" data-id="${esc(u.id)}"><span><span class="t">${esc(u.text)}</span><span class="meta">${clock(u.startMs)}${u.speaker ? ` · ${esc(speakerLabel(u.speaker))}` : ''}${u.audioExpired ? ' · 原声未保留' : ''}</span></span></button>`).join('')}</div>
      <p class="footnote">本场内容保存在内存中，下次收音或退出进程后清除。原声最多暂存 50 段。</p>`;
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
    const canPrev = idx - c.before > 0;
    const canNext = idx + c.after < d.pool.length - 1;
    const quote = selected().map((u) => (u.id === item.id ? esc(u.text) : `<span class="ctx">${esc(u.text)}</span>`)).join(' ');
    const range = selectedRange();
    const speaker = speakerLabel(item.speaker);
    const candidates = d.single ? '' : `
      <p class="fine" style="margin:0">有 ${d.items.length} 条接近，选一条：</p>
      <div class="cands">${d.items.map((it, i) => `
        <button class="cand ${i === c.sel ? 'on' : ''}" data-act="cand" data-i="${i}">
          <span class="radio"></span>
          <span><span class="t">${esc(it.text)}</span>
            <span class="meta"><span>${clock(it.startMs)}</span>${it.speaker != null ? `<span>${esc(speakerLabel(it.speaker))}</span>` : ''}</span>
            <span class="prob"><i style="width:${Math.round(it.prob * 100)}%"></i></span></span>
        </button>`).join('')}
      </div>`;
    return `<div class="grabber"></div>
      <div class="sheet-head">
        <span class="kicker">Recall · <span id="card-ago">${ago(S.state.sessionMs - item.endMs)}</span></span>
        <button class="close" data-act="close" aria-label="关闭">✕</button>
      </div>
      ${candidates}
      <div class="quote">“${quote}”</div>
      <div class="meta">${speaker ? `<span>${esc(speaker)}</span>` : ''}<span>${clock(range.startMs)}–${clock(range.endMs)}</span><span class="badge">语音转写</span></div>
      <div class="row">
        <button class="chip" data-act="play" data-key="${esc(cardClipKey())}">▶ 回听原声</button>
        <button class="chip ghost" data-act="prev" ${canPrev ? '' : 'disabled'}>＋前一句</button>
        <button class="chip ghost" data-act="next" ${canNext ? '' : 'disabled'}>＋后一句</button>
        ${c.before || c.after ? '<button class="chip ghost" data-act="reset">还原</button>' : ''}
      </div>
      <div class="actions">
        <button class="btn ghost" data-act="dismiss">丢弃</button>
        <button class="btn secondary" data-act="save">保存</button>
        <button class="btn primary" data-act="draft">生成行动 →</button>
      </div>
      <p class="footnote">不点保存就不会留下 · 召回 ${(d.latencyMs / 1000).toFixed(1)} 秒 · ${esc(d.engine)}</p>`;
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
      if (btn.dataset.act === 'play') btn.textContent = playing ? '■ 停止' : '▶ 回听原声';
      else btn.textContent = playing ? '■' : '▶';
    });
  }

  // ---- action draft -----------------------------------------------------------------------

  function openDraft(d) {
    S.draft = { data: d, status: 'editing', result: null, error: null, submitted: false };
    openSheet({ kind: 'draft' });
  }

  function draftHtml() {
    const { data: d, status, error } = S.draft;
    const names = [...new Set([...(d.assigneeCandidates || []), ...(d.who ? [d.who] : [])])];
    const options = ['<option value="">请选择负责人</option>']
      .concat(names.map((n) => `<option ${n === d.who ? 'selected' : ''}>${esc(n)}</option>`))
      .join('');
    const sending = status === 'sending';
    const submitted = status === 'waiting' || status === 'failed' || sending;
    return `<div class="grabber"></div>
      <div class="sheet-head"><span class="kicker">Action · 待你确认</span><button class="close" data-act="close" aria-label="关闭">✕</button></div>
      <div class="evidence"><button data-act="play-evidence" data-key="ev-${esc(d.draftId)}" aria-label="回听原声">▶</button><span>“${esc(d.evidence.quote)}”</span></div>
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
    S.draft.status = 'sending';
    S.draft.error = null;
    renderSheet();
    send('action.confirm', {
      draftId: d.draftId, who, what, whenIso: d.whenIso || '', whenRaw: d.whenRaw || '', constraint: d.constraint || '',
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
      <label class="field"><span>音源（下次开始收音时生效）</span>
        <select id="set-source">
          <option value="mic" ${s.source === 'mic' ? 'selected' : ''}>手机麦克风</option>
          <option value="wav" ${s.source === 'wav' ? 'selected' : ''}>测试 WAV（预录音频）</option>
          <option value="remote" ${s.source === 'remote' ? 'selected' : ''}>Mac 收音（经 USB 接收电脑上的麦克风）</option>
        </select></label>
      <label class="field"><span>Mac 收音地址（配合 adb reverse 用 127.0.0.1）</span><input id="set-remote" inputmode="url" placeholder="127.0.0.1:7790" value="${esc(s.remoteAddress || '127.0.0.1:7790')}"></label>
      <p class="fine">测试音频：${esc(s.testWavName || '内置合成对话')} · 需要 PCM 16kHz / 单声道 / 16bit WAV</p>
      <div class="row"><button class="chip" data-act="pick-audio">选择测试 WAV</button><button class="chip ghost" data-act="reset-audio">恢复内置音频</button></div>
      <label class="switch"><span>使用 Mock 后端</span><input type="checkbox" id="set-mock" ${s.backend === 'mock' ? 'checked' : ''}></label>
      <label class="field"><span>后端地址</span><input id="set-url" inputmode="url" placeholder="http://192.168.1.10:8000" value="${esc(s.backendUrl || '')}"></label>
      <label class="switch"><span>说话人区分（实验：测试中会丢句）</span><input type="checkbox" id="set-spk" ${s.speakerInfo ? 'checked' : ''}></label>
      <div class="actions two" style="margin-top:16px">
        <button class="btn ghost" data-act="close">取消</button>
        <button class="btn primary" data-act="apply-settings">保存</button>
      </div>
      <p class="section-title" style="margin-top:18px">召回耗时（验收：中位数 ≤ 5 秒）</p>
      <p class="diag">${esc(latency)}</p>
      <p class="section-title" style="margin-top:12px">诊断</p>
      <p class="diag" id="diag">${esc(diagText())}</p>`;
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
    send('settings.set', {
      source: $('set-source').value,
      remoteAddress: $('set-remote').value.trim(),
      mockBackend: $('set-mock').checked,
      backendBaseUrl: $('set-url').value.trim(),
      speakerInfo: $('set-spk').checked,
    });
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
      ${body}`;
  }

  // ---- errors -------------------------------------------------------------------------------

  function onError(p) {
    toast(p.message, true);
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
    close: closeSheet,
    'commitment-open': (el) => send('commitment.open', { utteranceId: el.dataset.id }),
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
    'apply-settings': applySettings,
    'pick-audio': () => send('testAudio.pick'),
    'reset-audio': () => send('testAudio.reset'),
  };

  $('sheet').addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el || el.disabled) return;
    const act = ACTIONS[el.dataset.act];
    if (act) act(el);
  });
  $('backdrop').addEventListener('click', closeSheet);
  $('btn-capture').addEventListener('click', () => send(S.state.capturing ? 'capture.stop' : 'capture.start'));
  $('btn-oops').addEventListener('click', () => send('recall.trigger'));
  $('btn-saved').addEventListener('click', () => send('saved.list'));
  $('btn-commitments').addEventListener('click', () => openSheet({ kind: 'commitments' }));
  $('btn-settings').addEventListener('click', () => openSheet({ kind: 'settings' }));

  send('hello');
})();
