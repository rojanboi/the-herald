// The flow as the host sees it (plain JSON) and its translation to and from React Flow's shape.
//   node: { id, kind, position: {x, y}, data }      kind: trigger | notify | inform
//   edge: { id, source, target, data: { minutes, always } }

export const KINDS = ['trigger', 'notify', 'inform'];

export const LEVELS = {
  L1: 'Support team',
  L2: 'Engineering team',
  L3: 'Senior engineers',
  L4: 'Management',
};

export const CHANNELS = [
  ['call', 'Call'],
  ['sms', 'Text message'],
  ['email', 'Email'],
  ['teams', 'Teams'],
  ['ui', 'In Herald'],
];

export const PROBLEMS = { bug: 'Bug', vulnerability: 'Vulnerability' };

export const SEVERITIES = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };

export const NODE_DEFAULTS = {
  trigger: { title: 'New trigger', problem: 'bug', severity: 'critical' },
  notify: { level: 'L2', who: 'Software engineer on call', channels: ['call'], respondMinutes: 5 },
  inform: { level: 'L4', who: 'Management', note: 'Sees everything. Is not called.' },
};

export const EDGE_DEFAULTS = { minutes: 5, always: false };

export function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

export function edgeLabel(data) {
  if (data && data.always) return 'always, at the same time';
  const minutes = data ? Number(data.minutes) : 0;
  if (!minutes || minutes <= 0) return 'at once';
  return `after ${minutes} min without a response`;
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** Host flow -> React Flow nodes and edges. `selectedId` marks one node or edge as selected. */
export function toInternal(flow, selectedId) {
  const seen = new Set();
  const nodes = [];
  for (const n of (flow && flow.nodes) || []) {
    if (!n || n.id == null) continue;
    const id = String(n.id);
    if (seen.has(id)) {
      console.warn(`[HeraldFlow] node id "${id}" is used twice; the second one is ignored`);
      continue;
    }
    seen.add(id);
    let kind = n.kind;
    if (!KINDS.includes(kind)) {
      console.warn(`[HeraldFlow] node "${id}" has unknown kind "${kind}"; shown as "notify"`);
      kind = 'notify';
    }
    nodes.push({
      id,
      type: kind,
      position: { x: num(n.position && n.position.x), y: num(n.position && n.position.y) },
      data: clone(n.data) || {},
      selected: selectedId != null && id === String(selectedId),
    });
  }

  const edgeIds = new Set();
  const edges = [];
  for (const e of (flow && flow.edges) || []) {
    if (!e || e.source == null || e.target == null) continue;
    const source = String(e.source);
    const target = String(e.target);
    if (!seen.has(source) || !seen.has(target)) {
      console.warn(`[HeraldFlow] edge "${e.id}" points to a node that does not exist; it is ignored`);
      continue;
    }
    let id = e.id == null ? `e-${source}-${target}` : String(e.id);
    while (edgeIds.has(id)) id += '-2';
    edgeIds.add(id);
    edges.push({
      id,
      source,
      target,
      data: { minutes: null, always: false, ...(clone(e.data) || {}) },
      selected: selectedId != null && id === String(selectedId),
    });
  }
  return { nodes, edges };
}

/** React Flow nodes and edges -> host flow (plain JSON, whole-pixel positions). */
export function toFlow(nodes, edges) {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      kind: n.type,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: clone(n.data) || {},
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      data: clone(e.data) || {},
    })),
  };
}
