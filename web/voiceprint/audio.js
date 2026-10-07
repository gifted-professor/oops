/* Pure resampling helper shared by the web recorder and its Node checks. */
((root) => {
  'use strict';
  function pcm16(chunks, rate, frames) {
    const input = new Float32Array(frames);
    let offset = 0;
    for (const c of chunks) { input.set(c, offset); offset += c.length; }
    const output = new Uint8Array(Math.floor(frames * 16000 / rate) * 2);
    const view = new DataView(output.buffer);
    for (let i = 0; i < output.length / 2; i++) {
      const pos = i * rate / 16000, a = Math.floor(pos), b = Math.min(a + 1, input.length - 1);
      const value = Math.max(-1, Math.min(1, input[a] + (input[b] - input[a]) * (pos - a)));
      view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    return output;
  }
  if (typeof module !== 'undefined') module.exports = {pcm16};
  else root.OopsAudio = {pcm16};
})(typeof window !== 'undefined' ? window : {});
