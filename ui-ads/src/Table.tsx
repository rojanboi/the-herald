import type { ComponentProps } from 'react';

import DynamicTable from '@atlaskit/dynamic-table';

/* The design system's table, inside a strip that scrolls sideways on a phone instead of pushing the page wide. */
export function Table(props: ComponentProps<typeof DynamicTable>) {
  return <div className="scroll-x"><DynamicTable {...props} /></div>;
}
