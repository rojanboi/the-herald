import { Handle, Position } from '@xyflow/react';
import { memo } from 'react';
import { CHANNELS, LEVELS, PROBLEMS, SEVERITIES } from './model.js';

function LevelChip({ level, quiet }) {
  if (!level) return null;
  return (
    <span className={'hf-chip hf-chip--level' + (quiet ? ' hf-chip--quiet' : '')}>
      <strong>{level}</strong>
      {LEVELS[level] ? ' ' + LEVELS[level] : ''}
    </span>
  );
}

const TriggerNode = memo(function TriggerNode({ data }) {
  const severity = SEVERITIES[data.severity] ? data.severity : null;
  return (
    <div className="hf-node hf-node--trigger">
      <div className="hf-muted hf-small">Start</div>
      <div className="hf-title">{data.title || 'Untitled trigger'}</div>
      <div className="hf-chips">
        {PROBLEMS[data.problem] && <span className="hf-chip">{PROBLEMS[data.problem]}</span>}
        {severity && <span className={'hf-chip hf-chip--' + severity}>{SEVERITIES[severity]}</span>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

const NotifyNode = memo(function NotifyNode({ data }) {
  const chosen = Array.isArray(data.channels) ? data.channels : [];
  const channels = CHANNELS.filter(([key]) => chosen.includes(key));
  const minutes = Number(data.respondMinutes);
  return (
    <div className="hf-node hf-node--notify">
      <Handle type="target" position={Position.Left} />
      <div className="hf-chips">
        <LevelChip level={data.level} />
      </div>
      <div className="hf-title">{data.who || 'Nobody chosen'}</div>
      {channels.length > 0 ? (
        <div className="hf-chips">
          {channels.map(([key, label]) => (
            <span key={key} className="hf-chip">
              {label}
            </span>
          ))}
        </div>
      ) : (
        <div className="hf-muted hf-small">No way to reach them chosen</div>
      )}
      {minutes > 0 && <div className="hf-muted hf-small">Has {minutes} min to take the ticket</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

const InformNode = memo(function InformNode({ data }) {
  return (
    <div className="hf-node hf-node--inform">
      <Handle type="target" position={Position.Left} />
      <div className="hf-chips">
        <LevelChip level={data.level} quiet />
      </div>
      <div className="hf-title">{data.who || 'Nobody chosen'}</div>
      {data.note && <div className="hf-muted hf-small">{data.note}</div>}
    </div>
  );
});

export const nodeTypes = { trigger: TriggerNode, notify: NotifyNode, inform: InformNode };
