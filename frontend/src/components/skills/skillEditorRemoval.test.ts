import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Skill } from '@/lib/types';

vi.mock('@/lib/api', () => ({
  skillsApi: {
    downloadFile: async () => new Blob(['print(1)']),
    update: async () => {},
    create: async () => {},
  },
}));

import SkillEditorDrawer from './SkillEditorDrawer';

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  document.querySelectorAll('.ant-modal-root, .ant-message').forEach((n) => n.remove());
});

const skill = {
  id: 'skill-1', name: '对账', description: '', content: '内容', tags: [], category: 'general',
  trigger_condition: '', use_count: 0, success_count: 0, is_public: false, is_active: true, version: 1,
  provenance: {}, verification: {}, last_used_at: null, created_at: '', updated_at: '',
  files: [{ path: 'scripts/check.py', type: 'script', size: 8, description: '' }],
} as unknown as Skill;

async function render(mode: 'view' | 'edit') {
  await act(async () => root.render(createElement(SkillEditorDrawer, {
    open: true, mode, skill, onClose: () => {}, onSaved: () => {},
  })));
}

const clickText = async (text: string) => {
  const nodes = [...document.querySelectorAll('div')].filter((el) => el.textContent?.trim() === text);
  expect(nodes.length, `点击目标 ${text} 未渲染`).toBeGreaterThan(0);
  await act(async () => nodes[nodes.length - 1].dispatchEvent(new MouseEvent('click', { bubbles: true })));
};

describe('技能附件移除', () => {
  it('编辑态选中附件后可标记移除，并显示待移除清单', async () => {
    await render('edit');
    await clickText('check.py');
    const removeButton = document.querySelector('.anticon-delete')?.closest('button');
    expect(removeButton, '编辑态必须能标记附件移除').toBeTruthy();
    await act(async () => {
      removeButton!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      removeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    // Popconfirm mounts in its own portal one tick later.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    const confirm = [...document.querySelectorAll('.ant-popconfirm button')]
      .find((b) => b.textContent?.replace(/\s/g, '') === '删除');
    expect(confirm, 'Popconfirm 确认按钮未渲染').toBeTruthy();
    await act(async () => confirm!.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(document.body.textContent).toContain('待移除 (1)');
    expect(document.body.textContent).toContain('scripts/check.py');
  });

  it('创建态不提供附件移除入口', async () => {
    await act(async () => root.render(createElement(SkillEditorDrawer, {
      open: true, mode: 'create', skill: null, onClose: () => {}, onSaved: () => {},
    })));
    expect(document.querySelector('.anticon-delete')).toBeNull();
  });
});