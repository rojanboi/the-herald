// Smoke test of the built bundle in jsdom: `npm run build && npm run smoke`.
// jsdom has no layout, so sizes are faked; this checks behaviour and the API, not how it looks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const dist = path.resolve(__dirname, '../../ui/flow');
const js = fs.readFileSync(path.join(dist, 'herald-flow.js'), 'utf8');
assert.ok(fs.statSync(path.join(dist, 'herald-flow.css')).size > 1000, 'herald-flow.css exists');

const errors = [];
const dom = new JSDOM('<!doctype html><html><body><div id="a" style="height:560px"></div><div id="b"></div></body></html>', {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;
window.console.error = (...a) => errors.push(a.map(String).join(' '));
window.console.warn = (...a) => errors.push(a.map(String).join(' '));

// What jsdom lacks and React Flow needs.
window.eval(`
  window.ResizeObserver = class {
    constructor(cb) { this.cb = cb; }
    observe(target) { setTimeout(() => this.cb([{ target, contentRect: target.getBoundingClientRect() }], this), 0); }
    unobserve() {} disconnect() {}
  };
  window.DOMMatrixReadOnly = class { constructor(t) { const m = /scale\\(([\\d.]+)\\)/.exec(t || ''); this.m22 = m ? +m[1] : 1; } };
  Object.defineProperties(HTMLElement.prototype, {
    offsetWidth: { get() { return this.classList.contains('react-flow__node') ? 220 : 1000; } },
    offsetHeight: { get() { return this.classList.contains('react-flow__node') ? 110 : 560; } },
  });
  Element.prototype.getBoundingClientRect = function () {
    const w = this.offsetWidth || 10, h = this.offsetHeight || 10;
    return { x: 0, y: 0, left: 0, top: 0, width: w, height: h, right: w, bottom: h, toJSON() {} };
  };
  SVGElement.prototype.getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 });
`);
window.eval(js);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HeraldFlow = window.HeraldFlow;

const sample = {
  nodes: [
    { id: 'start', kind: 'trigger', position: { x: 0, y: 34 }, data: { title: 'Critical bug', problem: 'bug', severity: 'critical' } },
    { id: 'l2', kind: 'notify', position: { x: 380, y: 20 }, data: { level: 'L2', who: 'Software engineer on call', channels: ['call', 'teams'], respondMinutes: 5 } },
    { id: 'l3', kind: 'notify', position: { x: 760, y: 20 }, data: { level: 'L3', who: 'Team lead', channels: ['call'], respondMinutes: 10 } },
    { id: 'l4', kind: 'notify', position: { x: 1140, y: 31 }, data: { level: 'L4', who: 'Head of engineering', channels: ['call', 'sms'] } },
    { id: 'mgmt', kind: 'inform', position: { x: 380, y: 260 }, data: { level: 'L4', who: 'Management', note: 'Sees everything. Is not called.' } },
  ],
  edges: [
    { id: 'e-start-l2', source: 'start', target: 'l2', data: { minutes: null, always: false } },
    { id: 'e-l2-l3', source: 'l2', target: 'l3', data: { minutes: 5, always: false } },
    { id: 'e-l3-l4', source: 'l3', target: 'l4', data: { minutes: 10, always: false } },
    { id: 'e-start-mgmt', source: 'start', target: 'mgmt', data: { minutes: null, always: true } },
  ],
};
const plain = (v) => JSON.parse(JSON.stringify(v));
const texts = (root, sel) => [...root.querySelectorAll(sel)].map((el) => el.textContent);
const results = [];
const step = (name) => results.push(name);

(async () => {
  assert.equal(typeof HeraldFlow.mount, 'function');
  const a = document.getElementById('a');
  const b = document.getElementById('b');
  const changes = [];
  const selections = [];

  // ---- mount ----
  const editor = HeraldFlow.mount(a, { flow: sample, onChange: (f) => changes.push(plain(f)), onSelect: (s) => selections.push(plain(s)) });
  assert.ok(a.querySelector('.hf-root .react-flow'), 'canvas is in the DOM straight after mount()');
  assert.deepEqual(plain(editor.getFlow()), sample, 'getFlow() returns what was passed in');
  await sleep(250);
  assert.equal(a.querySelectorAll('.react-flow__node').length, 5);
  assert.equal(a.querySelectorAll('.react-flow__edge').length, 4);
  assert.deepEqual(texts(a, '.hf-edge-label').sort(), ['after 10 min without a response', 'after 5 min without a response', 'always, at the same time', 'at once']);
  assert.equal(a.querySelectorAll('.react-flow__edge.hf-edge--inform').length, 1, 'edge to the inform node is dashed');
  const l2 = a.querySelector('.react-flow__node[data-id="l2"]').textContent;
  for (const part of ['L2 Engineering team', 'Software engineer on call', 'Call', 'Teams', 'Has 5 min to take the ticket']) assert.ok(l2.includes(part), `L2 card shows "${part}"`);
  assert.ok(!a.querySelector('.react-flow__node[data-id="l4"]').textContent.includes('to take the ticket'));
  assert.ok(a.querySelector('.react-flow__node[data-id="l4"]').textContent.includes('Text message'));
  assert.equal(a.querySelectorAll('.react-flow__node[data-id="start"] .react-flow__handle').length, 1);
  assert.equal(a.querySelectorAll('.react-flow__node[data-id="mgmt"] .react-flow__handle.target').length, 1);
  assert.equal(a.querySelectorAll('.react-flow__node[data-id="mgmt"] .react-flow__handle.source').length, 0);
  assert.ok(a.querySelector('.react-flow__controls') && a.querySelector('.react-flow__background') && !a.querySelector('.react-flow__minimap'));
  assert.equal(changes.length, 0, 'no onChange for mounting, measuring or fitting');
  assert.equal(selections.length, 0);
  step('mount: 5 nodes, 4 edges, labels, handles, controls, background; no callbacks fired');

  // ---- addNode ----
  const id = editor.addNode('notify', { who: 'Database engineer' });
  assert.equal(typeof id, 'string');
  assert.equal(editor.getFlow().nodes.length, 6);
  const added = editor.getFlow().nodes.find((n) => n.id === id);
  assert.deepEqual(plain(added.data), { level: 'L2', who: 'Database engineer', channels: ['call'], respondMinutes: 5 });
  await sleep(250);
  assert.equal(a.querySelectorAll('.react-flow__node').length, 6);
  assert.equal(changes.length, 1, 'one debounced onChange');
  assert.equal(changes[0].nodes.length, 6);
  assert.deepEqual(selections.at(-1), { type: 'node', id, data: plain(added.data) });
  assert.throws(() => editor.addNode('nope'));
  step(`addNode: returned "${id}" at ${JSON.stringify(added.position)}, selected it, onChange once, onSelect once`);

  // ---- updateNode / updateEdge ----
  editor.updateNode('l3', { who: 'Principal engineer', channels: ['email', 'ui'] });
  editor.updateEdge('e-l2-l3', { minutes: 7 });
  editor.updateEdge('e-start-l2', { always: true });
  await sleep(250);
  const l3 = a.querySelector('.react-flow__node[data-id="l3"]').textContent;
  assert.ok(l3.includes('Principal engineer') && l3.includes('Email') && l3.includes('In Herald') && !l3.includes('Call'));
  assert.ok(texts(a, '.hf-edge-label').includes('after 7 min without a response'));
  assert.equal(texts(a, '.hf-edge-label').filter((t) => t === 'always, at the same time').length, 2);
  assert.equal(changes.length, 2, 'three quick updates give one onChange');
  assert.equal(changes[1].edges.find((e) => e.id === 'e-l2-l3').data.minutes, 7);
  assert.equal(editor.updateNode('missing', {}), false);
  step('updateNode/updateEdge: cards and labels redraw, one debounced onChange');

  // ---- select + label click ----
  editor.select('e-l3-l4');
  await sleep(30);
  assert.deepEqual(selections.at(-1), { type: 'edge', id: 'e-l3-l4', data: { minutes: 10, always: false } });
  [...a.querySelectorAll('.hf-edge-label')].find((el) => el.textContent.startsWith('after 7')).dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await sleep(30);
  assert.equal(selections.at(-1).id, 'e-l2-l3', 'clicking a label selects its edge');
  step('select(): onSelect gets {type:"edge", id, data}; clicking an edge label selects the edge');

  // ---- Backspace removes the selection; removeSelected ----
  const press = async (key, target = document) => {
    target.dispatchEvent(new window.KeyboardEvent('keydown', { key, code: key, bubbles: true }));
    await sleep(40);
    target.dispatchEvent(new window.KeyboardEvent('keyup', { key, code: key, bubbles: true }));
    await sleep(250);
  };
  // focus on a host button outside the editor: the key must not remove anything
  const hostButton = document.body.appendChild(document.createElement('button'));
  hostButton.focus();
  await press('Backspace', hostButton);
  assert.ok(editor.getFlow().edges.some((e) => e.id === 'e-l2-l3'), 'Backspace on a host button removes nothing');
  hostButton.blur();
  hostButton.remove();
  await press('Backspace');
  const keyOk = !editor.getFlow().edges.some((e) => e.id === 'e-l2-l3');
  if (keyOk) assert.equal(selections.at(-1), null, 'onSelect(null) after the selected edge is removed');
  step(keyOk ? 'Backspace: removed the selected edge, onSelect(null); ignored while focus is on a host button' : 'Backspace: NOT verified in jsdom (key event did not reach React Flow)');
  editor.select('l4');
  await press('Delete');
  assert.ok(!editor.getFlow().nodes.some((n) => n.id === 'l4'), 'Delete removes the selected node');
  assert.ok(!editor.getFlow().edges.some((e) => e.target === 'l4'), 'and its edges');
  step('Delete: removed the selected node and its edges');

  editor.select(id);
  assert.equal(editor.removeSelected(), true);
  assert.equal(editor.getFlow().nodes.length, 4);
  editor.select('l2');
  editor.removeSelected();
  assert.ok(!editor.getFlow().edges.some((e) => e.source === 'l2' || e.target === 'l2'), 'edges of a removed node go too');
  await sleep(250);
  assert.equal(a.querySelectorAll('.react-flow__node').length, 3);
  step('removeSelected: removes the node and its edges');

  // ---- readOnly ----
  editor.setReadOnly(true);
  editor.select('l3');
  assert.equal(editor.removeSelected(), false);
  await press('Delete');
  assert.ok(editor.getFlow().nodes.some((n) => n.id === 'l3'), 'read-only: nothing is removed');
  assert.ok(a.querySelector('.hf-root').classList.contains('hf-readonly'));
  assert.equal(a.querySelectorAll('.react-flow__node.draggable').length, 0);
  // in read-only mode a plain click still selects (so the host can show details)
  a.querySelector('.react-flow__node[data-id="mgmt"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await sleep(30);
  const clickOk = selections.at(-1) && selections.at(-1).id === 'mgmt';
  editor.setReadOnly(false);
  step('readOnly: no removal by key or API, nodes not draggable' + (clickOk ? ', click still selects' : ' (click-to-select not verified in jsdom)'));

  // ---- setFlow ----
  const before = changes.length;
  editor.setFlow(sample);
  assert.deepEqual(plain(editor.getFlow()), sample);
  await sleep(250);
  assert.equal(a.querySelectorAll('.react-flow__node').length, 5);
  assert.equal(changes.length, before, 'setFlow does not call onChange');
  step('setFlow: replaces everything, does not call onChange');

  // ---- destroy, pending change is flushed, then nothing more ----
  editor.updateNode('l2', { respondMinutes: 9 });
  const n = changes.length;
  editor.destroy();
  assert.equal(changes.length, n + 1, 'the pending change is delivered by destroy()');
  assert.equal(a.childNodes.length, 0, 'host element is empty again');
  editor.destroy();
  editor.updateNode('l2', { respondMinutes: 1 });
  await sleep(250);
  assert.equal(changes.length, n + 1, 'nothing after destroy');
  step('destroy: element emptied, pending onChange flushed once, silent afterwards');

  // ---- mount again elsewhere, twice on one element, read-only from the start ----
  const second = HeraldFlow.mount(b, { flow: changes.at(-1), readOnly: true, selectedId: 'l3' });
  await sleep(100);
  assert.equal(b.querySelectorAll('.react-flow__node').length, 5);
  assert.equal(b.querySelectorAll('.react-flow__node.selected').length, 1);
  const third = HeraldFlow.mount(b, { flow: sample });
  await sleep(100);
  assert.equal(b.querySelectorAll('.hf-root').length, 1, 'mounting on the same element replaces the old editor');
  assert.equal(second.getFlow().nodes.length, 5);
  third.fitView();
  assert.ok(third.getViewport() && typeof third.getViewport().zoom === 'number');
  await sleep(50);
  third.destroy();
  assert.equal(b.childNodes.length, 0);
  step('re-mount on another element after destroy(), and twice on the same element');

  // ---- an editor whose element the host threw away never calls back ----
  const c = document.createElement('div');
  document.body.appendChild(c);
  let zombieCalls = 0;
  const zombie = HeraldFlow.mount(c, { flow: sample, onChange: () => zombieCalls++, onSelect: () => zombieCalls++ });
  await sleep(50);
  c.remove();
  zombie.updateNode('l2', { who: 'Ghost' });
  zombie.select('l2');
  await sleep(250);
  assert.equal(zombieCalls, 0);
  step('element removed without destroy(): editor shuts itself down, no callbacks');

  const noise = errors.filter((e) => !/Not implemented/.test(e));
  for (const r of results) console.log('ok  ' + r);
  console.log(noise.length ? `console noise (${noise.length}):\n  ` + [...new Set(noise)].join('\n  ').slice(0, 1500) : 'no console errors or warnings');
  window.close();
})().catch((error) => {
  for (const r of results) console.log('ok  ' + r);
  console.error('FAILED after the steps above:\n', error);
  if (errors.length) console.error('console output:\n  ' + [...new Set(errors)].join('\n  ').slice(0, 3000));
  process.exit(1);
});
