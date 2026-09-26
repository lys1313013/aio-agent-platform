import React, { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { memoriesApi } from '@/lib/api';
import type { MemoryHistory, MemoryOrganizePreview } from '@/lib/types';
import MemoryOrganizationModal from './MemoryOrganizationModal';
import MemoryHistoryDrawer from './MemoryHistoryDrawer';
import { sourceIds } from './MemorySources';

vi.mock('@/lib/api', () => ({ memoriesApi: {
  previewOrganization: vi.fn(), applyOrganization: vi.fn(), versions: vi.fn(), restoreVersion: vi.fn(),
  changes: vi.fn(), undoChange: vi.fn(),
} }));
// Native controls keep the tests focused on confirmation and API behavior.
vi.mock('antd', () => ({
  App: { useApp: () => ({ message: { success: vi.fn() } }) },
  Modal: ({ children, footer }: { children: React.ReactNode; footer: React.ReactNode }) => createElement('div', {}, children, footer),
  Drawer: ({ children }: { children: React.ReactNode }) => createElement('div', {}, children),
  Spin: ({ children }: { children: React.ReactNode }) => createElement('div', {}, children),
  Tag: ({ children }: { children: React.ReactNode }) => createElement('span', {}, children),
  Alert: ({ message }: { message: string }) => createElement('p', { role: 'alert' }, message),
  Empty: ({ description }: { description: string }) => createElement('p', {}, description),
  Button: ({ children, disabled, onClick }: React.ButtonHTMLAttributes<HTMLButtonElement>) => createElement('button', { disabled, onClick }, children),
  Checkbox: ({ children, ...props }: React.InputHTMLAttributes<HTMLInputElement>) => createElement('label', {}, createElement('input', { ...props, type: 'checkbox' }), children),
  Input: { TextArea: ({ value, onChange, id, disabled }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => createElement('textarea', { value, onChange, id, disabled }) },
  Popconfirm: ({ children, onConfirm }: { children: React.ReactNode; onConfirm: () => void }) => createElement('div', {}, children, createElement('button', { onClick: onConfirm }, 'confirm')),
  Collapse: ({ items }: { items: Array<{ key: string; children: React.ReactNode }> }) => createElement('div', {}, ...items.map((i) => createElement('div', { key: i.key }, i.children))),
}));

let root: Root;
let host: HTMLDivElement;
const close = vi.fn();
const changed = vi.fn();
const state = { id: 'm1', content: '原始内容', layer: 'L2' as const, agent_id: null,
  metadata: { source_session: 'source' }, version: 1, exists: true, created_at: '', updated_at: '' };
const preview: MemoryOrganizePreview = { id: 'plan', scanned: 2, total: 2, warnings: [], next_offset: null,
  originals: { m1: state, m2: { ...state, id: 'm2' } },
  groups: [{ id: 'g1', ids: ['m1', 'm2'], target_id: 'm1', content: '合并建议', reason: '重复' }],
};

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true, React });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  vi.mocked(memoriesApi.previewOrganization).mockResolvedValue(preview);
  vi.mocked(memoriesApi.applyOrganization).mockResolvedValue({ change_id: 'change', kind: 'merge' });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

async function mount(element: React.ReactNode) {
  await act(async () => root.render(createElement(MemoryRouter, {}, element)));
}
function button(text: string) { return [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(text))!; }
async function click(element: HTMLElement) { await act(async () => element.click()); }

async function open() {
  await mount(createElement(MemoryOrganizationModal, { layer: 'L2', agentId: '', onClose: close, onChanged: changed }));
}

describe('memory organization confirmation', () => {
  it('loads preview only, requires selection, and cancellation makes no changes', async () => {
    await open();
    expect(memoriesApi.previewOrganization).toHaveBeenCalledWith({ layer: 'L2', agent_id: null, ids: undefined, offset: 0 });
    expect(button('确认合并').disabled).toBe(true);
    expect(host.textContent).toContain('source');
    await click(button('取消'));
    expect(close).toHaveBeenCalledOnce();
    expect(memoriesApi.applyOrganization).not.toHaveBeenCalled();
  });

  it('sends only explicitly selected and corrected proposal on confirmation', async () => {
    await open();
    await click(host.querySelector('input')!);
    const textarea = host.querySelector('textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '人工纠正');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(memoriesApi.applyOrganization).not.toHaveBeenCalled();
    await click(button('确认合并'));
    expect(memoriesApi.applyOrganization).toHaveBeenCalledWith('plan', [{ id: 'g1', content: '人工纠正' }]);
    expect(changed).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });

  it('keeps a stale preview open and displays server conflict without claiming success', async () => {
    vi.mocked(memoriesApi.applyOrganization).mockRejectedValue(new Error('记忆在预览后已发生变化'));
    await open(); await click(host.querySelector('input')!); await click(button('确认合并'));
    expect(host.querySelector('[role=alert]')?.textContent).toContain('已发生变化');
    expect(close).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled();
    await click(button('重新预览'));
    expect(button('确认合并').disabled).toBe(true);
  });
});

describe('memory full version restoration', () => {
  const data: MemoryHistory = { current_version: 2, versions: [
    { version: 2, kind: 'edit', change_id: 'c2', snapshot: { ...state, version: 2 }, created_at: '' },
    { version: 1, kind: 'create', change_id: 'c1', snapshot: state, created_at: '' },
  ], sources: [{ id: 'source', title: '原始会话' }], legacy_revisions: [], has_more: false };

  it('requires confirmation and supplies the current revision to prevent lost updates', async () => {
    vi.mocked(memoriesApi.versions).mockResolvedValue(data);
    vi.mocked(memoriesApi.restoreVersion).mockResolvedValue({ change_id: 'c3', kind: 'restore' });
    await mount(createElement(MemoryHistoryDrawer, { memoryId: 'm1', agentId: '', onClose: close, onChanged: changed }));
    expect(host.querySelector('a')?.getAttribute('href')).toBe('/chat/source');
    expect(memoriesApi.restoreVersion).not.toHaveBeenCalled();
    await click(button('confirm'));
    expect(memoriesApi.restoreVersion).toHaveBeenCalledWith('m1', 1, 2);
    expect(changed).toHaveBeenCalledOnce();
  });

  it('does not automatically retry or overwrite on version conflict', async () => {
    vi.mocked(memoriesApi.versions).mockResolvedValue(data);
    vi.mocked(memoriesApi.restoreVersion).mockRejectedValue(new Error('记忆已更新，请刷新版本记录后再恢复'));
    await mount(createElement(MemoryHistoryDrawer, { memoryId: 'm1', agentId: '', onClose: close, onChanged: changed }));
    await click(button('confirm'));
    expect(memoriesApi.restoreVersion).toHaveBeenCalledOnce();
    expect(changed).not.toHaveBeenCalled();
    expect(host.querySelector('[role=alert]')?.textContent).toContain('记忆已更新');
  });

  it('normalizes old and new provenance formats', () => {
    expect(sourceIds({ source_session: 'a' })).toEqual(['a']);
    expect(sourceIds({ source_session: ['a', 'a', 'b', null, ['bad']] })).toEqual(['a', 'b']);
    expect(sourceIds({})).toEqual([]);
  });
});
