/* Desktop-only simulator. Native Android always owns the real bridge. */
(() => {
  'use strict';
  if (window.OopsNative) return;
  const team = ['Jason', '黄丽萍', '江古月', '洪玉麟', '林春源'];
  // Real device ASR observations for assets/test/dialog.wav (2026-10-06).
  const sample = [
    { startMs: 0, endMs: 4482, text: '我们这次要在两周内上线，预算不能超过10万。' },
    { startMs: 4852, endMs: 11112, text: '我觉得注册前应该先让用户体验一下产品不然转化率会很低' },
    { startMs: 11442, endMs: 16841, text: '这个想法不错，不过方案A要时上线，大概要13万。' },
    { startMs: 17162, endMs: 20181, text: '那我周五前把新方案发你' },
    { startMs: 20521, endMs: 24802, text: '把刚刚那个想法留下来' },
  ];
  const key = 'oops-browser-saved-v1';
  let saved;
  try { saved = JSON.parse(localStorage.getItem(key) || '[]'); } catch { saved = []; }
  const state = { capturing: false, sessionId: '', source: 'wav', sessionMs: 0, bufferedMs: 0,
    retentionMs: 300000, asr: 'off', asrDetail: null, level: 0, playing: false,
    savedCount: saved.length, backend: 'mock', backendUrl: '', speakerInfo: false,
    testWavName: '内置合成对话（浏览器模拟）', asrGaps: [], sourceDetail: null, sourceReady: false, remoteAddress: '127.0.0.1:7790' };
  const bridge = { onmessage: null, postMessage: (raw) => {
    try { const { type, payload = {} } = JSON.parse(raw); handle(type, payload); }
    catch (e) { emit('error', { scope: 'mock', message: e.message }); }
  } };
  window.OopsNative = bridge;
  const emit = (type, payload) => bridge.onmessage?.({ data: JSON.stringify({ type, payload }) });
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const id = (prefix) => `${prefix}-${crypto.randomUUID()}`;
  let startedAt = 0, timer = null, transcript = [], cards = new Map(), drafts = new Map();
  let sessionTranscript = [];
  let actions = new Map(), draftOps = new Map(), recallCount = 0, playingKey = null;
  const audio = new Audio('../test/dialog.wav');
  let clipTimer = null;
  function stopClip() {
    audio.pause(); clearTimeout(clipTimer);
    if (playingKey) emit('clip.state', { key: playingKey, state: 'stopped' });
    playingKey = null; state.playing = false;
  }
  function tick() {
    if (!state.capturing) return;
    state.sessionMs = Math.floor(performance.now() - startedAt);
    state.bufferedMs = Math.min(state.sessionMs, state.retentionMs);
    state.level = state.sessionMs < 25000 ? (Math.sin(state.sessionMs / 90) + 1) * 0.2 : 0;
    sample.forEach((u, i) => {
      const uid = `${state.sessionId}-u${i + 1}`;
      if (state.sessionMs >= u.endMs + 300 && !transcript.some((x) => x.id === uid)) {
        const final = { ...u, id: uid, speaker: null };
        transcript.push(final); sessionTranscript.push(final); emit('asr.final', final);
        final.tag = i === 0 ? 'constraint' : i === 3 ? 'commitment' : 'uncertain';
        final.tagEngine = 'rules-mock';
        emit('asr.tag', { ...final });
        state.commitmentCount = sessionTranscript.filter((u) => u.tag === 'commitment').length;
        state.tagsSupported = true; state.analysisPending = 0; state.analysisFailed = 0;
        emit('session.summary', summary());
      } else if (state.sessionMs >= u.startMs && state.sessionMs < u.endMs) {
        const chars = Math.ceil(u.text.length * (state.sessionMs - u.startMs) / (u.endMs - u.startMs));
        emit('asr.partial', { ...u, text: u.text.slice(0, chars) });
      }
    });
    const expired = transcript.filter((u) => u.endMs < state.sessionMs - state.retentionMs);
    if (expired.length) { transcript = transcript.filter((u) => !expired.includes(u));emit('transcript.pruned', { ids: expired.map((u) => u.id) }); }
    emit('state', { ...state });
  }
  function summary() {
    return { sessionId: state.sessionId, commitments: sessionTranscript.filter((u) => u.tag === 'commitment').map((u) => ({ ...u, audioExpired: false })) };
  }
  async function recall(p) {
    emit('recall.pending', { trigger: 'button' }); await sleep(350);
    const pool = transcript.filter((u) => !/oops|欧普斯|哦普斯|奥普斯|把刚刚那个想法留下来/i.test(u.text));
    if (!pool.length) return emit('recall.notFound', { reason: '最近 5 分钟内还没有可召回的原话' });
    const ideas = pool.filter((u) => /想法|觉得|方案|周五/.test(u.text));
    const ranked = (ideas.length ? ideas : pool).slice().sort((a,b) => (/觉得/.test(b.text) ? 1 : 0) - (/觉得/.test(a.text) ? 1 : 0));
    recallCount++;
    const single = recallCount % 2 === 1 || ranked.length < 2;
    const items = ranked.slice(0, single ? 1 : 2).map((u,i) => ({ ...u, prob: single ? 0.82 : i ? 0.4 : 0.48 }));
    const card = { id: id('c'), trigger: 'button', query: p.query || null, nowMs: state.sessionMs,
      single, engine: 'browser-mock', windowMs: 120000, latencyMs: 350, items, pool };
    cards.set(card.id, card);emit('recall.card', card);emit('latency', { recentMs: [350], medianMs: 350 });
  }
  async function draft(p) {
    const card = cards.get(p.cardId);
    if (!card) throw new Error('卡片已失效');
    const chosen = card.pool.filter((u) => p.utteranceIds.includes(u.id)).map((u) => sessionTranscript.find((x) => x.id === u.id) || u);
    if (!chosen.length) throw new Error('没有选中原话');
    await sleep(250);
    const d = { draftId: id('d'), cardId: card.id, who: '', what: chosen.map((u) => u.text).join(' '),
      whenIso: null, whenRaw: null, constraint: '预算不能超过10万', assigneeCandidates: team,
      evidence: { utteranceIds: chosen.map((u) => u.id), quote: chosen.map((u) => u.text).join(' '),
        startMs: chosen[0].startMs, endMs: chosen.at(-1).endMs } };
    drafts.set(d.draftId, d);emit('action.draft', d);
  }
  async function confirm(p) {
    const d = drafts.get(p.draftId);
    if (!d) throw new Error('草稿已失效');
    const opId = draftOps.get(d.draftId) || id('op');draftOps.set(d.draftId, opId);
    const previous = actions.get(opId);
    if (previous) return emit('action.result', previous);
    Object.assign(d, { who: p.who, what: p.what, whenIso: p.whenIso, constraint: p.constraint });
    emit('action.result', { draftId: d.draftId, opId, status: 'pending', polling: true, mock: true });
    await sleep(600);
    const r = { draftId: d.draftId, opId, status: 'created', taskGuid: `mock-${opId}`, taskUrl: null,
      message: '浏览器模拟：没有实际写入飞书', readBackConfirmed: true, mock: true, polling: false };
    actions.set(opId, r);emit('action.result', r);
  }
  function handle(type, p) {
    switch (type) {
      case 'hello': queueMicrotask(() => emit('snapshot', { state, transcript, summary: summary(), team, insets: { top: 0, bottom: 0 } }));break;
      case 'capture.start':
        if (state.capturing) break;
        stopClip();transcript = [];sessionTranscript = [];cards.clear();state.sessionId = id('session');
        Object.assign(state, { capturing: true, sourceReady: true, sessionMs: 0, bufferedMs: 0, asr: 'simulated', commitmentCount: 0, analysisPending: 0, analysisFailed: 0, tagsSupported: null });
        startedAt = performance.now();emit('state', { ...state });clearInterval(timer);timer = setInterval(tick, 100);break;
      case 'capture.stop': state.capturing = false;state.sourceReady = false;state.asr = 'off';state.level = 0;clearInterval(timer);stopClip();emit('state', { ...state });break;
      case 'recall.trigger': void recall(p);break;
      case 'commitment.open': {
        const u = sessionTranscript.find((u) => u.id === p.utteranceId && u.tag === 'commitment');
        if (!u) break;
        const at = sessionTranscript.indexOf(u);
        const card = { id: id('c'), trigger: 'commitment', query: null, nowMs: state.sessionMs,
          single: true, engine: 'rules-mock', windowMs: 0, latencyMs: 0, items: [{ ...u, prob: 1 }],
          pool: sessionTranscript.slice(Math.max(0, at - 2), at + 3) };
        cards.set(card.id, card); emit('recall.card', card); break;
      }
      case 'clip.play':
        stopClip();
        if (p.startMs < state.sessionMs - state.retentionMs && !sessionTranscript.some((u) => u.tag === 'commitment' && u.startMs <= p.startMs && u.endMs >= p.endMs)) { emit('clip.state', { key: p.key, state: 'expired' });break; }
        playingKey = p.key;audio.currentTime = Math.max(0, p.startMs / 1000);state.playing = true;
        emit('clip.state', { key: p.key, state: 'playing' });
        audio.play().catch(() => { emit('toast', { message: '浏览器未允许音频播放，请再次点击回听' });stopClip(); });
        clipTimer = setTimeout(stopClip, Math.max(100, p.endMs - p.startMs));break;
      case 'clip.stop': stopClip();break;
      case 'recall.dismiss': cards.delete(p.cardId);break;
      case 'recall.save': {
        const c = cards.get(p.cardId);if (!c) throw new Error('卡片已失效');
        const item = { id: c.id, saved_at: new Date().toISOString(), utterances: c.pool.filter((u) => p.utteranceIds.includes(u.id)), audio_expired: false };
        saved = saved.filter((x) => x.id !== c.id);saved.unshift(item);localStorage.setItem(key, JSON.stringify(saved));
        state.savedCount = saved.length;emit('recall.saved', { cardId: c.id, savedCount: saved.length });break;
      }
      case 'saved.list': emit('saved.items', { items: saved });break;
      case 'action.draft': void draft(p).catch((e) => emit('error', { scope: 'action', message: e.message }));break;
      case 'action.confirm': void confirm(p).catch((e) => emit('error', { scope: 'action', message: e.message }));break;
      case 'action.status': {
        const r = actions.get(draftOps.get(p.draftId));
        if (r) emit('action.result', r);else emit('action.result', { draftId: p.draftId, opId: draftOps.get(p.draftId), status: 'pending', polling: false, mock: true, message: '模拟操作仍在处理，请继续查询' });break;
      }
      case 'voiceprint.list':
      case 'voiceprint.record':
      case 'voiceprint.save':
      case 'voiceprint.delete':
        state.voiceprint = {phase: 'idle', message: '浏览器不录入真实声纹，请在已连接服务器的 Android App 中使用'};
        emit('state', {...state});break;
      case 'voiceprint.cancel': break;
      case 'settings.set':
        state.source = p.source || state.source;state.backendUrl = p.backendBaseUrl || '';state.speakerInfo = !!p.speakerInfo;
        state.remoteAddress = p.remoteAddress || state.remoteAddress;
        emit('state', { ...state });emit('toast', { message: '浏览器只模拟后端；真实配置请在 Android App 中设置' });break;
      case 'testAudio.pick': emit('toast', { message: '外部 WAV 导入请在 Android App 中使用' });break;
      case 'testAudio.reset': state.testWavName = '内置合成对话（浏览器模拟）';emit('state', { ...state });break;
      case 'open.url': if (/^https?:\/\//.test(p.url)) window.open(p.url, '_blank', 'noopener');break;
      default: break;
    }
  }
})();
