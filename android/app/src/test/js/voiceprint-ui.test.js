'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, { id, dataset: {}, style: { setProperty() {} }, classList: { toggle() {}, add() {}, remove() {} },
    setAttribute() {}, appendChild() {}, querySelectorAll: () => [],
    listeners: {}, addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); } });
  return elements.get(id);
};
const messages = [];
const native = { postMessage(message) { messages.push(JSON.parse(message)); } };
const context = { window: { OopsNative: native }, location: { search: '' }, URLSearchParams,
  document: { getElementById: element, querySelectorAll: () => [], documentElement: { dataset: {} } },
  console, setTimeout, clearTimeout };
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../main/assets/web/app.js'), 'utf8'), context);
const receive = (type, payload) => native.onmessage({ data: JSON.stringify({ type, payload }) });
const state = (phase, name) => receive('state', { capturing: true, source: 'remote', sourceReady: true,
  voiceprint: { phase, name, mode: 'enroll', catalog: { people: [{ name: '洪玉麟' }, { name: '林春源' }] } } });
const selected = () => element('sheet').innerHTML.match(/<option[^>]* selected>(.*?)<\/option>/)?.[1];

// After saving one person, changing the idle selection must select and record the next person.
state('idle', '洪玉麟');
receive('voiceprint.open', {});
assert.equal(selected(), '洪玉麟');
element('vp-name').value = '林春源';
for (const fn of element('sheet').listeners.change) fn({ target: element('vp-name') });
assert.equal(selected(), '林春源');
element('vp-consent').checked = true;
const record = { disabled: false, dataset: { act: 'vp-record' } };
for (const fn of element('sheet').listeners.click) fn({ target: { closest: () => record } });
assert.equal(messages.at(-1).type, 'voiceprint.record');
assert.equal(messages.at(-1).payload.name, '林春源');

// A native recording, quality check, review or save continues to show its locked identity.
for (const phase of ['recording', 'checking', 'review', 'saving', 'testing']) {
  state(phase, '洪玉麟');
  assert.equal(selected(), '洪玉麟');
  assert.match(element('sheet').innerHTML, /id="vp-name" disabled/);
}
state('idle', '洪玉麟');
assert.equal(selected(), '林春源');
console.log('voiceprint UI identity regression passed');
