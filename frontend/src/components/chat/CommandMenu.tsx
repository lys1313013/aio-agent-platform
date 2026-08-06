import { useEffect, useMemo, useRef } from 'react';
import { useCommandStore } from '@/stores/commandStore';
import type { CommandMeta } from '@/lib/types';
import { cn } from '@/lib/utils';

interface CommandMenuProps {
  query: string;
  activeIndex: number;
  onSelect: (cmd: CommandMeta) => void;
  onClose: () => void;
}

/** Flattened, grouped display rows for the command palette. */
interface Row {
  kind: 'header' | 'item';
  group: string;
  cmd?: CommandMeta;
}

function buildRows(items: CommandMeta[]): Row[] {
  const rows: Row[] = [];
  let lastGroup = '';
  for (const cmd of items) {
    if (cmd.group !== lastGroup) {
      rows.push({ kind: 'header', group: cmd.group });
      lastGroup = cmd.group;
    }
    rows.push({ kind: 'item', group: cmd.group, cmd });
  }
  return rows;
}

export default function CommandMenu({ query, activeIndex, onSelect, onClose }: CommandMenuProps) {
  const search = useCommandStore((s) => s.search);
  const items = useMemo(() => search(query), [search, query]);
  const rows = useMemo(() => buildRows(items), [items]);
  const listRef = useRef<HTMLDivElement>(null);

  // Map the item index (parent keyboard nav) to the flattened row index.
  const activeRowIndex = useMemo(() => {
    let itemCounter = -1;
    return rows.findIndex((r) => {
      if (r.kind === 'item') itemCounter += 1;
      return r.kind === 'item' && itemCounter === activeIndex;
    });
  }, [rows, activeIndex]);

  // Keep the active item in view while navigating.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-row-index="${activeRowIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeRowIndex]);

  // Close the palette when nothing matches.
  useEffect(() => {
    if (query && items.length === 0) onClose();
  }, [query, items.length, onClose]);

  if (items.length === 0) return null;

  return (
    <div
      ref={listRef}
      className="absolute bottom-full left-0 right-0 z-50 mb-2 max-h-64 overflow-y-auto rounded-xl border border-border bg-card shadow-xl"
      role="listbox"
    >
      {rows.map((row, i) =>
        row.kind === 'header' ? (
          <div
            key={`h-${row.group}`}
            className="sticky top-0 z-10 border-b border-border bg-card px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
          >
            {row.group}
          </div>
        ) : (
          <button
            key={`c-${row.cmd!.name}`}
            data-row-index={i}
            role="option"
            aria-selected={i === activeIndex}
            onClick={() => onSelect(row.cmd!)}
            onMouseEnter={() => {}} // activeIndex is managed by the parent keyboard nav
            className={cn(
              'flex w-full items-center gap-2 px-3 py-2 text-left transition-colors',
              i === activeRowIndex ? 'bg-primary/10' : 'hover:bg-muted',
            )}
          >
            <span className="flex-shrink-0 rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs text-primary">
              /{row.cmd!.name}
            </span>
            <span className="min-w-0 flex-1 truncate text-sm text-foreground">
              {row.cmd!.desc}
            </span>
            {row.cmd!.args.length > 0 && (
              <span className="flex-shrink-0 text-[11px] text-muted-foreground">
                {row.cmd!.args
                  .filter((a) => a.required)
                  .map((a) => `⟨${a.name}⟩`)
                  .join(' ')}
              </span>
            )}
          </button>
        ),
      )}
    </div>
  );
}
