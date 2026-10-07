const assert = require('node:assert/strict');
const {pcm16} = require('../web/voiceprint/audio.js');
// Phones often capture at 44.1 or 48 kHz; retain duration and speech-band signal at 16 kHz.
for (const rate of [16000, 44100, 48000]) {
  const input = Float32Array.from({length: rate * 30}, (_, i) => .25 * Math.sin(2 * Math.PI * 440 * i / rate));
  const bytes = pcm16([input.subarray(0, 4096), input.subarray(4096)], rate, input.length);
  assert.equal(bytes.length, 30 * 32000);
  const view = new DataView(bytes.buffer);
  let squareError = 0;
  for (let i = 0; i < 16000; i++) squareError += (view.getInt16(i * 2, true) / 32768 - .25 * Math.sin(2 * Math.PI * 440 * i / 16000)) ** 2;
  assert.ok(Math.sqrt(squareError / 16000) < .001, 'resampling changed the speech-band waveform');
}
const clipped = pcm16([Float32Array.of(2, -2, 0)], 16000, 3);
const view = new DataView(clipped.buffer);
assert.equal(view.getInt16(0, true), 32767);
assert.equal(view.getInt16(2, true), -32768);
assert.equal(view.getInt16(4, true), 0);
console.log('voiceprint web audio: 4 checks passed');
