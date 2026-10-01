import { useState } from 'react';

export interface ReasoningVisibility {
  values: Record<string, boolean>;
  onChange: (id: string, open: boolean) => void;
}

/** Only explicit choices override the streaming/completed defaults. */
export function useReasoningVisibility(controlled?: ReasoningVisibility): ReasoningVisibility {
  const [values, setValues] = useState<Record<string, boolean>>({});
  return controlled ?? {
    values,
    onChange: (id, open) => setValues(previous => ({ ...previous, [id]: open })),
  };
}
