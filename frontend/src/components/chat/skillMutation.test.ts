import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import ToolCallCard from './ToolCallCard';

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
});

async function render(payload: unknown, status = 'ok') {
  await act(async () => root.render(createElement(MemoryRouter, {}, createElement(ToolCallCard, {
    toolCall: { id: 'call-1', name: 'update_skill', arguments: {}, result: { status, preview: JSON.stringify(payload) } },
  }))));
}

describe('技能保存结果', () => {
  it('业务失败不能因执行状态 ok 显示已保存', async () => {
    await render({ success: false, code: 'version_conflict', message: '技能已被修改，请重新读取' });
    expect(host.textContent).toContain('技能保存失败');
    expect(host.textContent).toContain('请重新读取');
    expect(host.querySelector('a')).toBeNull();
  });
  it('执行器失败不能因 payload 成功显示已保存', async () => {
    await render({ success: true, status: 'updated', name: '对账' }, 'error');
    expect(host.textContent).toContain('技能保存失败');
  });
  it('展示真实版本变化、附件变更及固定版本链接', async () => {
    await render({ success: true, status: 'updated', skill_id: 'skill-1', name: '对账', version: 2, previous_version: 1,
      files: [{ path: 'scripts/check.py' }], verification: { status: 'unverified' },
      changes: { summary: '增加重复检查', files_modified: ['scripts/check.py'] } });
    expect(host.textContent).toContain('已更新技能');
    expect(host.textContent).toContain('v1 → v2');
    expect(host.textContent).toContain('未验证');
    expect(host.textContent).toContain('修改附件：scripts/check.py');
    expect(host.querySelector('a')?.getAttribute('href')).toBe('/skills/skill-1?version=2');
  });
  it('旧版非结构化结果仍可渲染', async () => {
    await render(null);
    expect(host.textContent).toContain('修改技能');
    expect(host.textContent).not.toContain('已保存技能');
  });
});
