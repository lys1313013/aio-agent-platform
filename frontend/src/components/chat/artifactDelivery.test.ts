import { afterEach, describe, expect, it } from 'vitest';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import ArtifactLink, { parseArtifactLink, workspaceRelativePath } from './ArtifactLink';
import { useWebpagePreviewStore } from '@/stores/webpagePreviewStore';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  useWebpagePreviewStore.setState({ preview: null, panelAvailable: false });
});

describe('产出物交付链接', () => {
  it('沙箱绝对路径去掉当前工作区挂载目录，只处理匹配的目录边界', () => {
    expect(workspaceRelativePath('default/hello.py', 'default')).toBe('hello.py');
    expect(workspaceRelativePath('default/reports/hello.py', 'default')).toBe('reports/hello.py');
    expect(workspaceRelativePath('hello.py', 'default')).toBe('hello.py');
    expect(workspaceRelativePath('default-old/hello.py', 'default')).toBe('default-old/hello.py');
  });
  it('持久化的 Markdown 回答重新渲染后仍可点击打开网页预览', async () => {
    const id = '6dd91c495f084d708d1fa1da2a4cf53e';
    const content = `已生成：[报告](/artifacts/webpages/${id})`;
    const host = document.createElement('div');
    document.body.append(host);
    for (let visit = 0; visit < 2; visit++) {
      const root = createRoot(host);
      useWebpagePreviewStore.setState({ panelAvailable: true, preview: null });
      await act(async () => {
        root.render(createElement(ReactMarkdown, { components: { a: ArtifactLink }, children: content }));
      });
      expect(host.querySelector('a')?.getAttribute('href')).toBe(`/artifacts/webpages/${id}`);
      await act(async () => { host.querySelector('a')!.click(); });
      expect(useWebpagePreviewStore.getState().preview).toEqual({ pageId: id, title: '报告' });
      await act(async () => { root.unmount(); });
    }
    host.remove();
  });

  it('工作区链接支持编码文件名，但拒绝路径穿越及无效编码', () => {
    expect(parseArtifactLink('/workspace/reports/%E6%8A%A5%E5%91%8A%20(1).md')).toEqual({
      kind: 'file', path: 'reports/报告 (1).md', filename: '报告 (1).md',
    });
    for (const href of ['/workspace/../secret', '/workspace/%2e%2e/secret', '/workspace/%ZZ', '/workspace/a%5Cb', '/workspace/%00']) {
      expect(parseArtifactLink(href)).toBeNull();
    }
    expect(parseArtifactLink('https://example.com/report')).toBeNull();
  });
});
