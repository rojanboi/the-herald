import {
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  MarkerType,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import { useCallback, useMemo, useSyncExternalStore } from 'react';
import { SelectEdgeContext, edgeTypes } from './FlowEdge.jsx';
import { EDGE_DEFAULTS, edgeLabel } from './model.js';
import { nodeTypes } from './nodes.jsx';

const EDGE_COLOUR = '#626f86';
const NAVY = '#161B5E';
const SEP = '\u0000';

export const FIT_OPTIONS = { padding: 0.12, minZoom: 0.25, maxZoom: 1 };

const DELETE_KEYS = ['Backspace', 'Delete'];

// React Flow listens for Backspace/Delete on the whole document. Only let it remove things when the
// keyboard focus is in the editor or nowhere in particular, not on some button elsewhere in the host page.
async function focusIsOurs() {
  const active = document.activeElement;
  return !active || active === document.body || !!active.closest('.hf-root');
}

// Sentence case for the tooltips of the zoom buttons.
const ARIA_LABELS = {
  'controls.ariaLabel': 'View controls',
  'controls.zoomIn.ariaLabel': 'Zoom in',
  'controls.zoomOut.ariaLabel': 'Zoom out',
  'controls.fitView.ariaLabel': 'Fit everything in view',
};

function unselect(items) {
  return items.map((item) => (item.selected ? { ...item, selected: false } : item));
}

/**
 * The canvas. All state lives in `store` (see index.jsx) so the host can read and change the flow
 * from outside React at any moment; this component only draws it and feeds user edits back.
 */
export function Editor({ store, viewport, onInit }) {
  const state = useSyncExternalStore(store.subscribe, store.get);
  const { nodes, readOnly } = state;

  const onNodesChange = useCallback(
    (changes) => {
      const s = store.get();
      const allowed = s.readOnly ? changes.filter((c) => c.type !== 'remove' && c.type !== 'position') : changes;
      if (allowed.length) store.set({ nodes: applyNodeChanges(allowed, s.nodes) });
    },
    [store],
  );

  const onEdgesChange = useCallback(
    (changes) => {
      const s = store.get();
      const allowed = s.readOnly ? changes.filter((c) => c.type !== 'remove') : changes;
      if (allowed.length) store.set({ edges: applyEdgeChanges(allowed, s.edges) });
    },
    [store],
  );

  const isValidConnection = useCallback(
    (c) => {
      const s = store.get();
      if (!c.source || !c.target || c.source === c.target) return false;
      const source = s.nodes.find((n) => n.id === c.source);
      const target = s.nodes.find((n) => n.id === c.target);
      if (!source || !target || source.type === 'inform' || target.type === 'trigger') return false;
      return !s.edges.some((e) => e.source === c.source && e.target === c.target);
    },
    [store],
  );

  const onConnect = useCallback(
    (c) => {
      const s = store.get();
      if (s.readOnly || !isValidConnection(c)) return;
      let id = `e-${c.source}-${c.target}`;
      while (s.edges.some((e) => e.id === id)) id += '-2';
      store.set({
        nodes: unselect(s.nodes),
        edges: [
          ...unselect(s.edges),
          { id, source: c.source, target: c.target, data: { ...EDGE_DEFAULTS }, selected: true },
        ],
      });
    },
    [store, isValidConnection],
  );

  const selectEdge = useCallback(
    (id) => {
      const s = store.get();
      store.set({
        nodes: unselect(s.nodes),
        edges: s.edges.map((e) => (e.selected === (e.id === id) ? e : { ...e, selected: e.id === id })),
      });
    },
    [store],
  );

  // Edges to "inform" nodes are dashed; that depends on the nodes' kinds, not on where they are.
  const informKey = nodes
    .filter((n) => n.type === 'inform')
    .map((n) => n.id)
    .join(SEP);

  const edges = useMemo(() => {
    const inform = new Set(informKey ? informKey.split(SEP) : []);
    return state.edges.map((e) => ({
      ...e,
      type: 'herald',
      className: inform.has(e.target) ? 'hf-edge--inform' : undefined,
      ariaLabel: edgeLabel(e.data),
      interactionWidth: 24,
      reconnectable: false,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        markerUnits: 'userSpaceOnUse',
        width: 24,
        height: 24,
        color: e.selected ? NAVY : EDGE_COLOUR,
      },
    }));
  }, [state.edges, informKey]);

  return (
    <SelectEdgeContext.Provider value={selectEdge}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onBeforeDelete={focusIsOurs}
        onInit={onInit}
        colorMode="light"
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        edgesReconnectable={false}
        elementsSelectable
        deleteKeyCode={readOnly ? null : DELETE_KEYS}
        multiSelectionKeyCode={null}
        selectionKeyCode={null}
        connectionLineType={ConnectionLineType.Bezier}
        fitView={!viewport}
        fitViewOptions={FIT_OPTIONS}
        defaultViewport={viewport || undefined}
        minZoom={0.25}
        maxZoom={1.5}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        ariaLabelConfig={ARIA_LABELS}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1.5} color="#c7ccd4" />
        <Controls showInteractive={false} fitViewOptions={FIT_OPTIONS} />
      </ReactFlow>
    </SelectEdgeContext.Provider>
  );
}
