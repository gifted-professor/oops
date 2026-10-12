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
    voiceTrigger: true, noticeVibrate: true, memorySync: true, syncStatus: 'synced', syncPending: 0, digestCounts: {}, notices: [], agenda: {goal: '', items: [], tracking: 'waiting'},
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
  const day = 86400000;
  const mockTasks = [
    { op_id: 'op-mock-1', what: '周五前把新方案发出来', who: '洪玉麟', quote: '那我周五前把新方案发你', url: 'https://example.com/task/1',
      meeting: { started_at: '2026-10-06T09:00:00+08:00', goal: '上线方案' }, overdue: true, feishu: { state: 'open', due_ms: Date.now() - day } },
    { op_id: 'op-mock-2', what: '整理预算上限', who: '林春源', quote: '预算不能超过10万', url: 'https://example.com/task/2',
      meeting: { started_at: '2026-10-06T09:00:00+08:00', goal: '上线方案' }, overdue: false, feishu: { state: 'done', due_ms: Date.now() - 2 * day } },
    { op_id: 'op-mock-3', what: '确认体验版注册流程', who: '江古月', quote: '注册前应该先让用户体验一下产品', url: 'https://example.com/task/3',
      meeting: null, overdue: false, feishu: { state: 'unknown', stale: true } },
  ];
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
        final.tag = ['constraint', 'idea', 'decision', 'commitment', 'uncertain'][i];
        final.tagEngine = 'rules-mock';
        emit('asr.tag', { ...final });
        state.commitmentCount = sessionTranscript.filter((u) => u.tag === 'commitment').length;
        state.tagsSupported = true; state.analysisPending = 0; state.analysisFailed = 0;
        state.digestCounts = Object.fromEntries(['decision', 'commitment', 'constraint', 'idea'].map(t => [t, sessionTranscript.filter(u => u.tag === t).length]));
        if (i === 2) {
          const n = {id: id('notice'), kind: 'conflict', title: '可能与之前的约束冲突', detail: `之前：「${sessionTranscript[0].text}」\n本场：「${final.text}」`, atMs: state.sessionMs, evidenceIds: [sessionTranscript[0].id, final.id]};
          state.notices.push(n); emit('notice', n);
        }
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
    return { sessionId: state.sessionId, ...Object.fromEntries([['commitments','commitment'],['decisions','decision'],['constraints','constraint'],['ideas','idea']].map(([key, tag]) => [key, sessionTranscript.filter(u => u.tag === tag).map(u => ({...u, audioExpired: false, whenRaw: tag === 'commitment' ? '周五前' : null}))])), notices: state.notices };
  }
  async function recall(p) {
    emit('recall.pending', { trigger: 'button' }); await sleep(350);
    const pool = (p.ask ? sessionTranscript : transcript).filter((u) => !/oops|欧普斯|哦普斯|奥普斯|把刚刚那个想法留下来/i.test(u.text));
    if (!pool.length) return emit('recall.notFound', { reason: '最近 5 分钟内还没有可召回的原话' });
    const ideas = pool.filter((u) => /想法|觉得|方案|周五/.test(u.text));
    let ranked = (ideas.length ? ideas : pool).slice().sort((a,b) => (/觉得/.test(b.text) ? 1 : 0) - (/觉得/.test(a.text) ? 1 : 0));
    if (p.ask && p.query) {
      const grams = s => new Set(s.replace(/[^\p{L}\p{N}]/gu, '').split('').slice(0,-1).map((c,i) => c+s.replace(/[^\p{L}\p{N}]/gu, '')[i+1]));
      const q = grams(p.query); const score = u => [...q].filter(x => grams(u.text).has(x)).length;
      ranked = pool.slice().sort((a,b) => score(b)-score(a));
      if (!score(ranked[0])) return emit('recall.notFound', {reason:'本场原话里没有找到答案'});
    }
    recallCount++;
    const single = recallCount % 2 === 1 || ranked.length < 2;
    const items = ranked.slice(0, single ? 1 : 2).map((u,i) => ({ ...u, prob: single ? 0.82 : i ? 0.4 : 0.48 }));
    const card = { id: id('c'), trigger: p.ask ? 'ask' : 'button', query: p.query || null, nowMs: state.sessionMs,
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
  let mockMeetings = [{session_id: 'demo', started_at: '2026-10-06T09:00:00+08:00', goal: '上线方案评审（模拟）', utterance_count: 1}];
  const historicalRows = [{id: 'u1', text: '预算不能超过10万。', start_ms: 0, end_ms: 4482, tag: 'constraint', speaker: null},
    {id: 'u2', text: '那我周五前把新方案发你。', start_ms: 17162, end_ms: 20181, tag: 'commitment', speaker: null, owner_hint: null, when_raw: '周五前'},
    {id: 'u3', text: '这个想法不错，不过方案A要时上线，大概要13万。', start_ms: 11442, end_ms: 16841, tag: 'decision', speaker: '林春源'}]
    .sort((a, b) => a.start_ms - b.start_ms);
  // Same order as the server's review_queue: tasks, decisions, then unknown speakers of long enough lines.
  const reviewQueue = (rows) => [
    ...rows.filter((u) => u.tag === 'commitment' && !u.review?.verdict).map((u) => ({kind: 'commitment', id: u.id})),
    ...rows.filter((u) => u.tag === 'decision' && !u.review?.verdict).map((u) => ({kind: 'decision', id: u.id})),
    ...rows.filter((u) => !u.speaker && !u.review?.speaker && u.text.replace(/[^\p{L}\p{N}]/gu, '').length >= 4).map((u) => ({kind: 'speaker', id: u.id}))];
  function handle(type, p) {
    switch (type) {
      case 'hello': queueMicrotask(() => emit('snapshot', { state, transcript, summary: summary(), team, insets: { top: 0, bottom: 0 } }));break;
      case 'capture.start':
        if (state.capturing) break;
        stopClip();transcript = [];sessionTranscript = [];cards.clear();state.sessionId = id('session');
        state.notices = []; state.digestCounts = {};
        Object.assign(state, { capturing: true, sourceReady: true, sessionMs: 0, bufferedMs: 0, asr: 'simulated', commitmentCount: 0, analysisPending: 0, analysisFailed: 0, tagsSupported: null });
        startedAt = performance.now();emit('state', { ...state });clearInterval(timer);timer = setInterval(tick, 100);break;
      case 'capture.stop': state.capturing = false;state.sourceReady = false;state.asr = 'off';state.level = 0;clearInterval(timer);stopClip();emit('state', { ...state });break;
      case 'ask.session': void recall({...p, ask: true}); break;
      case 'agenda.set':
        state.agenda = {goal: p.goal, items: p.items.map((i,n) => ({id: 'a'+(n+1), title: i.title, plannedMs: i.minutes ? i.minutes * 60000 : null, coveredMs: 0})), plannedMs: p.minutes ? p.minutes * 60000 : null, tracking: 'supported'}; emit('state', {...state}); break;
      case 'agenda.clear': state.agenda = {goal: '', items: [], tracking: 'waiting'}; emit('state', {...state}); break;
      case 'notice.dismiss': {const n = state.notices.find(x => x.id === p.id); if (n) n.dismissed = true; emit('state', {...state}); emit('session.summary', summary()); break;}
      case 'notice.feedback': {const n = state.notices.find(x => x.id === p.id); if (n) n.useful = p.useful; emit('state', {...state}); emit('session.summary', summary()); break;}
      case 'digest.openMany': {
        const rows = sessionTranscript.filter(u => p.ids.includes(u.id));
        const card = {id: id('c'), trigger: 'conflict', single: rows.length === 1, engine: 'browser-mock', latencyMs: 0, items: rows.map(u => ({...u, prob: 1})), pool: rows}; cards.set(card.id, card); emit('recall.card', card); break;
      }
      case 'history.list': emit('history.list', {count: mockMeetings.length, items: mockMeetings.map((m) => ({...m, title: m.manualTitle || m.goal, review_pending: {all: reviewQueue(historicalRows).length}}))}); break;
      case 'history.open': {
        if (p.sessionId === state.sessionId) {
          const rows = sessionTranscript.map((u) => ({id: u.id, text: u.text, start_ms: u.startMs, end_ms: u.endMs, tag: u.tag, speaker: u.speaker || null}));
          return emit('history.open', {session_id: state.sessionId, started_at: new Date().toISOString(), utterances: rows, review_queue: reviewQueue(rows),
            digest: {constraint: [], commitment: [], decision: [], idea: []}, notices: []});
        }
        const meeting = mockMeetings.find(i => i.session_id === p.sessionId); if (!meeting) return emit('error', {scope: 'history', message: '会议已删除'});
        emit('history.open', {...meeting, project: meeting.project || null, display_title: meeting.manualTitle || meeting.goal, title_source: meeting.manualTitle ? 'manual' : 'generated', utterances: historicalRows, review_queue: reviewQueue(historicalRows),
          digest: {constraint: historicalRows.filter(u => u.tag === 'constraint'), commitment: historicalRows.filter(u => u.tag === 'commitment' && u.review?.verdict !== 'rejected'),
            decision: historicalRows.filter(u => u.tag === 'decision' && u.review?.verdict !== 'rejected'), idea: []}, notices: []}); break;
      }
      case 'history.delete': if (p.confirm) {mockMeetings = mockMeetings.filter(i => i.session_id !== p.sessionId); emit('history.delete', {deleted: true});} break;
      case 'projects.list': {
        const groups = {};
        mockMeetings.forEach((m) => { if (m.project) (groups[m.project] ||= []).push(m); });
        setTimeout(() => emit('projects.list', {items: Object.entries(groups).map(([name, ms]) => ({name, meeting_count: ms.length,
          confirmed_decisions: historicalRows.filter((u) => u.review?.verdict === 'confirmed' && ['decision', 'constraint'].includes(u.tag)).length,
          last_started_at: ms[0].started_at}))}), 200); break;
      }
      case 'project.open': {
        const ms = mockMeetings.filter((m) => m.project === p.name).map((m) => ({...m, title: m.manualTitle || m.goal}));
        const decisions = ms.length ? historicalRows.filter((u) => u.review?.verdict === 'confirmed' && ['decision', 'constraint'].includes(u.tag))
          .map((u) => ({...u, session_id: 'demo', started_at: ms[0].started_at, meeting_title: ms[0].title})) : [];
        setTimeout(() => emit('project.open', {name: p.name, meetings: ms, decisions, tasks: ms.length ? mockTasks.slice(0, 1) : [],
          unreviewed_decisions: historicalRows.filter((u) => u.tag === 'decision' && !u.review?.verdict).length}), 200); break;
      }
      case 'meeting.project': {
        const meeting = mockMeetings.find((m) => m.session_id === p.sessionId);
        if (meeting) meeting.project = p.project || null;
        setTimeout(() => emit('meeting.project', {session_id: p.sessionId, project: p.project || null}), 200); break;
      }
      case 'history.rename': {
        const meeting = mockMeetings.find((m) => m.session_id === p.sessionId);
        if (meeting) { meeting.manualTitle = p.title || null; }
        const title = p.title || '上线方案评审（模拟）';
        setTimeout(() => emit('history.renamed', { session_id: p.sessionId, title, title_source: p.title ? 'manual' : 'generated' }), 200); break;
      }
      case 'history.search': emit('history.search', {query: p.query, engine: 'browser-mock', items: mockMeetings.length ? historicalRows.map(u => ({...u, id: 'h:demo:'+u.id, session_id: 'demo', started_at: '2026-10-06T09:00:00+08:00', prob: .9})) : []}); break;
      case 'recall.trigger': void recall(p);break;
      case 'digest.open':
      case 'commitment.open': {
        const u = sessionTranscript.find((u) => u.id === p.utteranceId);
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
      case 'clip.remote': emit('clip.state', { key: p.key, state: 'unavailable' }); emit('toast', { message: '浏览器没有服务器录音，请在 Android App 中回听' }); break;
      case 'speaker.correct': {
        const u = sessionTranscript.find((x) => x.id === p.utteranceId);
        if (p.sessionId === state.sessionId && u) { u.manualSpeaker = p.speaker; emit('speaker.corrected', { ...p, status: 'local' }); }
        const h = historicalRows.find((x) => x.id === p.utteranceId);
        if (h) { h.review = {...(h.review || {}), speaker: p.speaker}; if (p.speaker == null) delete h.review.speaker; }
        setTimeout(() => emit('speaker.corrected', { ...p, status: 'saved' }), 300); break;
      }
      case 'review.set': {
        const u = historicalRows.find((x) => x.id === p.utteranceId);
        if (u) u.review = {...(u.review || {}), ...p.fields};
        setTimeout(() => emit('review.saved', {sessionId: p.sessionId, utteranceId: p.utteranceId, review: u?.review || p.fields}), 200); break;
      }
      case 'tasks.list': {
        if (p.mine && !state.selfName) throw new Error('请先在设置里选择本人');
        const all = mockTasks.filter((t) => (!p.mine || t.who === state.selfName) && (p.status === 'all' || t.feishu.state === p.status || (p.status === 'open' && t.feishu.state === 'unknown')));
        setTimeout(() => emit('tasks.list', { items: all, mine: p.mine, status: p.status }), 300); break;
      }
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
        ['voiceTrigger','noticeVibrate','memorySync','selfName'].forEach(k => { if (k in p) state[k] = p[k]; });
        emit('state', { ...state });emit('toast', { message: '浏览器只模拟后端；真实配置请在 Android App 中设置' });break;
      case 'testAudio.pick': emit('toast', { message: '外部 WAV 导入请在 Android App 中使用' });break;
      case 'testAudio.reset': state.testWavName = '内置合成对话（浏览器模拟）';emit('state', { ...state });break;
      case 'open.url': if (/^https?:\/\//.test(p.url)) window.open(p.url, '_blank', 'noopener');break;
      default: break;
    }
  }
})();
