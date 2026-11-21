import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@xyflow/react';
import { createContext, memo, useContext } from 'react';
import { edgeLabel } from './model.js';

/** Lets the label (which React Flow draws outside the edge itself) select its edge when clicked. */
export const SelectEdgeContext = createContext(() => {});

const FlowEdge = memo(function FlowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
  style,
  selected,
  interactionWidth,
}) {
  const selectEdge = useContext(SelectEdgeContext);
  // React Flow ends the edge at the outer rim of the target's handle dot, so the arrowhead stays visible.
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={interactionWidth} />
      <EdgeLabelRenderer>
        <div
          className={'hf-edge-label nodrag nopan' + (selected ? ' is-selected' : '')}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          onClick={(event) => {
            event.stopPropagation();
            selectEdge(id);
          }}
        >
          {edgeLabel(data)}
        </div>
      </EdgeLabelRenderer>
    </>
  );
});

export const edgeTypes = { herald: FlowEdge };
