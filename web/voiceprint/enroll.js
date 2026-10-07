/* HTTPS microphone -> bounded in-memory PCM -> authenticated, person-scoped backend. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const token = new URLSearchParams(location.hash.slice(1)).get('invite') || '';
  const base = location.pathname.replace(/\/voiceprint\/?$/, '');
  const reading = '现在录入我的声纹，这段话只用于声音测试。早晨的阳光照进房间，窗外有人散步，远处传来汽车的声音。桌上放着一本书、一杯水和一支笔。我用平时说话的声音朗读，保持自然的语速和音量。读完后稍作停顿，再从头朗读一次。';
  const testReading = '午后的公园很安静，树叶随着微风轻轻摇动。小路旁有几朵花，远处的孩子正在玩耍。我现在用另一段文字测试声音匹配。';
  let catalog, name, phase = 'loading', mode = 'enroll', recording, sample, lastSaved = false;
  const busy = () => ['loading', 'requesting', 'recording', 'checking', 'saving', 'testing'].includes(phase);

  function status(message, error = false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }
  function render() {
    $('flow').hidden = !catalog;
    $('script-title').textContent = mode === 'test' ? '第二步 · 用另一段话测试' : '第一步 · 朗读录入';
    $('script').textContent = mode === 'test' ? testReading : `我是${name}。${reading}`;
    $('duration').textContent = mode === 'test' ? '10 秒' : '30 秒';
    $('consent-wrap').hidden = busy() || phase === 'review';
    $('record').hidden = busy() || phase === 'review';
    $('record').disabled = !catalog || !$('consent').checked;
    $('record').textContent = lastSaved ? '重新录入 · 30 秒' : '开始朗读 · 30 秒';
    $('stop').hidden = phase !== 'recording';
    $('meter-wrap').hidden = !['recording', 'checking', 'review', 'saving', 'testing'].includes(phase);
    $('save').hidden = phase !== 'review' || mode !== 'enroll' || !sample?.qualityPassed;
    $('discard').hidden = phase !== 'review';
    $('test').hidden = busy() || phase === 'review' || !catalog?.people[0]?.registered;
    $('test').disabled = !$('consent').checked;
    const p = catalog?.people[0];
    $('profile').textContent = p ? (p.registered ? `当前声纹：${p.source === 'mic' ? '现场录入' : p.source || '历史录音'} · ${p.seconds} 秒${p.updated_at ? ' · ' + new Date(p.updated_at).toLocaleString() : ''}` : '尚未录入声纹') : '';
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 40000);
    try {
      const response = await fetch(base + '/v1/voiceprints' + path, {
        method: body ? 'POST' : 'GET', headers: {Authorization: 'Bearer ' + token, ...(body ? {'Content-Type': 'application/json'} : {})},
        body: body ? JSON.stringify(body) : undefined, cache: 'no-store', credentials: 'omit', signal: controller.signal
      });
      const data = await response.json();
      if (!response.ok) throw new Error(response.status === 401 ? '录入链接无效或已过期，请向团队获取新的个人链接。' : data.error?.message || '服务器暂时不可用');
      return data;
    } catch (e) {
      if (e.name === 'AbortError' || e instanceof TypeError) throw new Error('网络请求未完成。保存时请保留本页并重试，重复提交不会保存两次。');
      throw e;
    } finally { clearTimeout(timeout); }
  }
  const clock = seconds => `00:${String(Math.floor(seconds)).padStart(2, '0')}`;
  function updateMeter(seconds, rms) {
    const limit = mode === 'test' ? 10 : 30;
    $('timer').textContent = `${clock(seconds)} / ${clock(limit)}`;
    $('progress').style.width = Math.min(100, seconds / limit * 100) + '%';
    $('level').textContent = rms >= .006 ? '收到声音 · 请继续朗读' : '声音偏小 · 请靠近麦克风';
  }
  function dispose(r) {
    if (!r) return;
    r.processor.onaudioprocess = null;
    clearInterval(r.watch);
    r.stream.getTracks().forEach(t => { t.onended = null; });
    r.processor.disconnect(); r.source.disconnect(); r.sink.disconnect();
    r.stream.getTracks().forEach(t => t.stop());
    r.context.close().catch(() => {});
  }
  function pcm16(chunks, rate, frames) {
    const output = window.OopsAudio.pcm16(chunks, rate, frames);
    let binary = '';
    for (let i = 0; i < output.length; i += 16384) binary += String.fromCharCode(...output.subarray(i, i + 16384));
    return btoa(binary);
  }
  async function start(which) {
    if (busy() || !catalog || !$('consent').checked) return;
    if (!navigator.mediaDevices?.getUserMedia || !(window.AudioContext || window.webkitAudioContext)) {
      status('请在手机 Safari 或 Chrome 中打开此 HTTPS 链接，并允许麦克风权限。', true); return;
    }
    mode = which; sample = null; phase = 'requesting';
    $('quality').hidden = true; $('result').hidden = true;
    status('请允许麦克风权限，随后开始朗读。'); render();
    let stream, context;
    try {
      // Created during the click gesture so mobile Safari can resume its audio context.
      context = new (window.AudioContext || window.webkitAudioContext)();
      await context.resume();
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false}});
      if (phase !== 'requesting') { stream.getTracks().forEach(t => t.stop()); await context.close(); return; }
      await context.resume();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const sink = context.createGain(); sink.gain.value = 0;
      const r = {stream, context, source, processor, sink, chunks: [], frames: 0, rate: context.sampleRate, limit: which === 'test' ? 10 : 30, finishing: false, lastFrame: performance.now()};
      recording = r;
      processor.onaudioprocess = event => {
        if (recording !== r || r.finishing) return;
        r.lastFrame = performance.now();
        const input = event.inputBuffer.getChannelData(0);
        const count = Math.min(input.length, Math.floor(r.limit * r.rate) - r.frames);
        if (count > 0) {
          const chunk = input.slice(0, count); r.chunks.push(chunk); r.frames += count;
          updateMeter(r.frames / r.rate, Math.sqrt(chunk.reduce((s, v) => s + v * v, 0) / chunk.length));
        }
        if (r.frames >= r.limit * r.rate) { r.finishing = true; finish(); }
      };
      source.connect(processor); processor.connect(sink); sink.connect(context.destination);
      stream.getTracks().forEach(t => { t.onended = () => cancelHiddenRecording('麦克风连接中断，请重新录音。'); });
      r.watch = setInterval(() => {
        if (recording === r && performance.now() - r.lastFrame > 5000) cancelHiddenRecording('没有收到麦克风音频，请检查权限后重新录音。');
      }, 1000);
      phase = 'recording'; updateMeter(0, 0);
      status(which === 'test' ? '正在测试录音，10 秒后自动结束。' : '开始朗读。保持自然音量，30 秒后自动结束。'); render();
    } catch (e) {
      stream?.getTracks().forEach(t => t.stop()); context?.close().catch(() => {});
      phase = 'idle'; status(e.name === 'NotAllowedError' ? '麦克风未获授权。请在浏览器设置中允许麦克风后重试。' : '无法启动麦克风，请用手机浏览器打开链接后重试。', true); render();
    }
  }
  async function finish() {
    const r = recording;
    if (!r) return;
    recording = null; dispose(r);
    const seconds = r.frames / r.rate;
    if (seconds < (mode === 'test' ? 1 : 15)) {
      phase = 'idle'; status(mode === 'test' ? '测试录音太短，请重新录满 10 秒。' : '录音不足 15 秒，请重新朗读，建议录满 30 秒。', true); render(); return;
    }
    const p = catalog.people[0];
    sample = {sample_rate: 16000, pcm_base64: pcm16(r.chunks, r.rate, r.frames), name, source: 'mic',
      consent: true, single_speaker: true, sample_id: crypto.randomUUID ? crypto.randomUUID() : 'web-' + Date.now() + '-' + Math.random().toString(36).slice(2),
      expected_revision: catalog.revision, expected_profile_revision: p.profile_revision};
    r.chunks.length = 0;
    phase = mode === 'test' ? 'testing' : 'checking'; status(mode === 'test' ? '正在匹配声纹…' : '正在检查时长与音量…'); render();
    try {
      const data = await api('/test', sample);
      if (mode === 'test') {
        showResult(data); sample = null; phase = 'idle'; $('consent').checked = false;
        status('测试完成，请核对当前判断是否是本人。');
      } else {
        const q = data.quality;
        sample.qualityPassed = q.seconds >= 15 && q.active_seconds >= 8 && q.clipped_ratio <= .01;
        $('quality').hidden = false;
        $('quality').textContent = `已录 ${q.seconds} 秒 · 有效音量片段 ${q.active_seconds} 秒`;
        phase = 'review';
        status(sample.qualityPassed ? '时长与音量检查通过。点击保存，服务器还会检查片段一致性。' : '声音偏小、朗读不足或音量过大，请靠近麦克风并重新朗读。', !sample.qualityPassed);
      }
    } catch (e) { phase = 'review'; status(e.message + ' 请放弃并重新录音。', true); }
    render();
  }
  async function save() {
    if (phase !== 'review' || !sample?.qualityPassed) return;
    phase = 'saving'; status('正在检查片段一致性并保存你的声纹…'); render();
    try {
      catalog = await api('/enroll', sample); sample = null; phase = 'idle'; lastSaved = true; $('consent').checked = false;
      status('声纹已保存。再读一段不同的文字，测试能否认出你。');
    } catch (e) {
      phase = 'review'; status(e.message, true);
    }
    render();
  }
  function showResult(data) {
    $('result').hidden = false;
    $('result-title').textContent = data.speaker ? `当前判断：${data.speaker}${data.speaker === name ? ' ✓' : '（请核对）'}` : '当前判断：未知';
    $('result-reason').textContent = `${data.reason}。最接近：${data.closest || '暂无'}，相似度 ${data.score.toFixed(3)}；门槛 ${data.accept}，领先幅度至少 ${data.required_margin}。`;
    $('scores').replaceChildren(...data.scores.map(p => {
      const row = document.createElement('div'); row.className = 'score-row';
      const label = document.createElement('span'), score = document.createElement('span');
      label.textContent = p.name; score.textContent = p.score.toFixed(3); row.append(label, score); return row;
    }));
  }
  $('record').onclick = () => start('enroll');
  $('test').onclick = () => start('test');
  $('stop').onclick = finish;
  $('save').onclick = save;
  $('discard').onclick = async () => {
    sample = null; phase = 'loading'; mode = 'enroll'; $('consent').checked = false; $('quality').hidden = true;
    status('正在刷新你的声纹状态…'); render();
    try { catalog = await api(''); phase = 'idle'; status('已放弃临时录音，可以重新朗读。'); }
    catch (e) { phase = 'idle'; status(e.message, true); }
    render();
  };
  $('consent').onchange = render;
  function cancelHiddenRecording(message = '录音因页面离开前台而中止，请保持本页打开后重新朗读。') {
    if (phase === 'requesting' || recording) {
      dispose(recording); recording = null; sample = null; phase = 'idle';
      status(message, true); render();
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancelHiddenRecording(); });
  window.addEventListener('pagehide', () => { cancelHiddenRecording(); sample = null; });
  (async () => {
    if (!token) { phase = 'idle'; status('请使用团队发给你的个人录入链接。无需安装 App。', true); return; }
    try {
      catalog = await api(''); name = catalog.person;
      if (!name || catalog.people.length !== 1 || catalog.people[0].name !== name) throw new Error('请使用个人声纹录入链接。');
      $('name').textContent = name; phase = 'idle'; status('准备好了。勾选同意后，允许麦克风并开始朗读。'); render();
    } catch (e) { catalog = null; phase = 'idle'; status(e.message, true); render(); }
  })();
})();
