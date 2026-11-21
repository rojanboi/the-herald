import '@xyflow/react/dist/style.css';
import './herald-flow.css';

import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { Editor, FIT_OPTIONS } from './Editor.jsx';
import { KINDS, NODE_DEFAULTS, clone, toFlow, toInternal } from './model.js';

const CHANGE_DELAY_MS = 150;
const NODE_WIDTH = 220;
const NODE_HEIGHT = 110;

/** element -> editor, so mounting twice on one element replaces the first editor instead of stacking. */
const mounted = new WeakMap();

function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...patch };
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

function call(fn, arg) {
  if (typeof fn !== 'function') return;
  try {
    fn(arg);
  } catch (error) {
    console.error('[HeraldFlow] the host callback threw', error);
  }
}

function unselect(items) {
  return items.map((item) => (item.selected ? { ...item, selected: false } : item));
}

export function mount(element, options = {}) {
  if (!element || element.nodeType !== 1) throw new Error('HeraldFlow.mount: the first argument must be an element');
  const previous = mounted.get(element);
  if (previous) previous.destroy();

  const { onChange, onSelect } = options;
  const store = createStore({ ...toInternal(options.flow, options.selectedId), readOnly: !!options.readOnly });

  const wrapper = document.createElement('div');
  wrapper.className = 'hf-root' + (options.readOnly ? ' hf-readonly' : '');
  element.appendChild(wrapper);
  const root = createRoot(wrapper);

  let instance = null; // React Flow's own handle, set once the canvas is ready
  let destroyed = false;
  let wasConnected = element.isConnected;
  let fitPending = false;
  let frame = 0;
  let changeTimer = 0;
  let selectTimer = 0;

  const getFlow = () => {
    const s = store.get();
    return toFlow(s.nodes, s.edges);
  };

  const currentSelection = () => {
    const s = store.get();
    const node = s.nodes.find((n) => n.selected);
    if (node) return { type: 'node', id: node.id, data: clone(node.data) };
    const edge = s.edges.find((e) => e.selected);
    if (edge) return { type: 'edge', id: edge.id, data: clone(edge.data) };
    return null;
  };
  const selectionKey = (sel) => (sel ? `${sel.type}:${sel.id}` : '');

  let lastJson = JSON.stringify(getFlow());
  let lastSelection = selectionKey(currentSelection());

  // An editor whose element was thrown away by the host without destroy() must never call back.
  const orphaned = () => {
    if (element.isConnected) {
      wasConnected = true;
      return false;
    }
    return wasConnected;
  };

  function emitChange() {
    changeTimer = 0;
    if (destroyed) return;
    if (orphaned()) return teardown();
    const flow = getFlow();
    const json = JSON.stringify(flow);
    if (json === lastJson) return;
    lastJson = json;
    call(onChange, flow);
  }

  function emitSelect() {
    selectTimer = 0;
    if (destroyed) return;
    if (orphaned()) return teardown();
    const sel = currentSelection();
    const key = selectionKey(sel);
    if (key === lastSelection) return;
    lastSelection = key;
    call(onSelect, sel);
  }

  // Callbacks always run from a timer, never from inside React, so the host is free to call
  // destroy() or mount() from them.
  const unsubscribe = store.subscribe(() => {
    if (destroyed) return;
    clearTimeout(changeTimer);
    changeTimer = setTimeout(emitChange, CHANGE_DELAY_MS);
    if (!selectTimer) selectTimer = setTimeout(emitSelect, 0);
  });

  function tryFit() {
    if (!fitPending || !instance || destroyed) return;
    const box = wrapper.getBoundingClientRect();
    if (!box.width || !box.height) return; // hidden for now; the resize observer tries again
    fitPending = false;
    instance.fitView(FIT_OPTIONS);
  }

  function requestFit() {
    fitPending = true;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(tryFit);
  }

  // If the editor is mounted while its tab is hidden (zero size), fit once it becomes visible.
  let hadSize = false;
  const observer =
    typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => {
          const box = wrapper.getBoundingClientRect();
          const hasSize = box.width > 0 && box.height > 0;
          if (hasSize && !hadSize && !options.viewport) requestFit();
          else if (hasSize && fitPending) requestFit();
          hadSize = hasSize || hadSize;
        });
  if (observer) {
    const box = wrapper.getBoundingClientRect();
    hadSize = box.width > 0 && box.height > 0;
    observer.observe(wrapper);
  }

  function teardown() {
    if (destroyed) return false;
    destroyed = true;
    clearTimeout(changeTimer);
    clearTimeout(selectTimer);
    cancelAnimationFrame(frame);
    unsubscribe();
    if (observer) observer.disconnect();
    if (mounted.get(element) === editor) mounted.delete(element);
    instance = null;
    root.unmount();
    wrapper.remove();
    return true;
  }

  const editor = {
    getFlow,

    /** Replace everything. Does not call onChange. Pass { fit: false } to keep the current view. */
    setFlow(flow, { fit = true } = {}) {
      if (destroyed) return;
      const selected = currentSelection();
      store.set(toInternal(flow, selected ? selected.id : undefined));
      lastJson = JSON.stringify(getFlow());
      if (fit) requestFit();
    },

    updateNode(id, patch) {
      const s = store.get();
      const key = String(id);
      if (destroyed || !s.nodes.some((n) => n.id === key)) return false;
      store.set({
        nodes: s.nodes.map((n) => (n.id === key ? { ...n, data: { ...n.data, ...clone(patch) } } : n)),
      });
      return true;
    },

    updateEdge(id, patch) {
      const s = store.get();
      const key = String(id);
      if (destroyed || !s.edges.some((e) => e.id === key)) return false;
      store.set({
        edges: s.edges.map((e) => (e.id === key ? { ...e, data: { ...e.data, ...clone(patch) } } : e)),
      });
      return true;
    },

    /** Adds a node near the centre of what is on screen, selects it and returns its id. */
    addNode(kind, data) {
      if (!KINDS.includes(kind)) throw new Error(`HeraldFlow.addNode: unknown kind "${kind}"`);
      if (destroyed) return null;
      const s = store.get();

      let n = s.nodes.length + 1;
      let id = `${kind}-${n}`;
      while (s.nodes.some((node) => node.id === id)) id = `${kind}-${++n}`;

      const box = wrapper.getBoundingClientRect();
      let position;
      if (instance && box.width && box.height) {
        const centre = instance.screenToFlowPosition({ x: box.left + box.width / 2, y: box.top + box.height / 2 });
        position = { x: centre.x - NODE_WIDTH / 2, y: centre.y - NODE_HEIGHT / 2 };
      } else if (s.nodes.length) {
        // no measurable canvas yet: put it to the right of everything
        position = {
          x: Math.max(...s.nodes.map((node) => node.position.x)) + NODE_WIDTH + 160,
          y: Math.min(...s.nodes.map((node) => node.position.y)),
        };
      } else {
        position = { x: 0, y: 0 };
      }
      const taken = (p) =>
        s.nodes.some((node) => Math.abs(node.position.x - p.x) < 16 && Math.abs(node.position.y - p.y) < 16);
      while (taken(position)) position = { x: position.x + 24, y: position.y + 24 };
      position = { x: Math.round(position.x), y: Math.round(position.y) };

      store.set({
        nodes: [
          ...unselect(s.nodes),
          { id, type: kind, position, data: { ...clone(NODE_DEFAULTS[kind]), ...(clone(data) || {}) }, selected: true },
        ],
        edges: unselect(s.edges),
      });
      return id;
    },

    /** Removes the selected node (with its edges) or edge. Does nothing when read-only. */
    removeSelected() {
      const s = store.get();
      if (destroyed || s.readOnly) return false;
      const gone = new Set(s.nodes.filter((n) => n.selected).map((n) => n.id));
      const nodes = s.nodes.filter((n) => !gone.has(n.id));
      const edges = s.edges.filter((e) => !e.selected && !gone.has(e.source) && !gone.has(e.target));
      if (nodes.length === s.nodes.length && edges.length === s.edges.length) return false;
      store.set({ nodes, edges });
      return true;
    },

    /** Selects the node or edge with this id; null clears the selection. Calls onSelect. */
    select(id) {
      if (destroyed) return;
      const s = store.get();
      const key = id == null ? null : String(id);
      const isNode = key != null && s.nodes.some((n) => n.id === key);
      const mark = (items, on) =>
        items.map((item) => {
          const selected = on && item.id === key;
          return !!item.selected === selected ? item : { ...item, selected };
        });
      store.set({ nodes: mark(s.nodes, isNode), edges: mark(s.edges, !isNode) });
    },

    setReadOnly(value) {
      if (destroyed) return;
      wrapper.classList.toggle('hf-readonly', !!value);
      store.set({ readOnly: !!value });
    },

    fitView() {
      if (!destroyed) requestFit();
    },

    /** { x, y, zoom } of the current view; pass it back as mount(..., { viewport }) to restore it. */
    getViewport() {
      return instance ? instance.getViewport() : options.viewport || null;
    },

    /**
     * Unmounts React and removes every listener. If the user changed something less than 150 ms ago
     * and onChange has not been told yet, it is told now (after the editor is fully gone).
     */
    destroy() {
      if (destroyed) return;
      const flow = getFlow();
      const pending = JSON.stringify(flow) !== lastJson && !orphaned();
      teardown();
      if (pending) call(onChange, flow);
    },
  };

  mounted.set(element, editor);

  const tree = (
    <Editor
      store={store}
      viewport={options.viewport}
      onInit={(rf) => {
        instance = rf;
        tryFit();
      }}
    />
  );
  flushSync(() => root.render(tree));

  return editor;
}

export const version = '0.1.0';
