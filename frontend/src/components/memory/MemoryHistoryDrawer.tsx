import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Collapse, Drawer, Empty, Popconfirm, Spin, Tag } from 'antd';
import { memoriesApi } from '@/lib/api';
import type { MemoryChange, MemoryHistory, MemorySnapshot } from '@/lib/types';
import MemorySources from './MemorySources';

const LABELS: Record<string, string> = {
  baseline: '功能启用时的快照', create: '创建', edit: '人工纠错', delete: '删除', merge: '确认合并',
  restore: '恢复版本', undo: '撤销操作', automatic_sources: '补充来源', automatic_skip: '补充来源',
  automatic_merge: '自动合并', automatic_update: '自动更新',
};

function Snapshot({ value, sessions }: { value?: MemorySnapshot; sessions?: MemoryHistory['sources'] }) {
  if (!value?.exists) return <span className="text-muted-foreground">不存在／已删除</span>;
  return <div className="min-w-0">
    <Tag>{value.layer} · v{value.version}</Tag>
    <span className="text-xs text-muted-foreground break-all">{value.agent_id ? `智能体专属：${value.agent_id}` : '用户共享'}</span>
    <p className="mt-2 whitespace-pre-wrap break-words">{value.content}</p>
    <MemorySources metadata={value.metadata} sessions={sessions} />
    <details className="mt-2 text-xs text-muted-foreground"><summary>完整元数据</summary>
      <pre className="whitespace-pre-wrap break-all">{JSON.stringify(value.metadata, null, 2)}</pre>
    </details>
  </div>;
}

export default function MemoryHistoryDrawer({ memoryId, agentId, layer, onClose, onChanged }: {
  memoryId?: string; agentId: string; layer?: string; onClose: () => void; onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [history, setHistory] = useState<MemoryHistory | null>(null);
  const [changes, setChanges] = useState<MemoryChange[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (offset = 0) => {
    setLoading(true); setError(null);
    try {
      if (memoryId) {
        const result = await memoriesApi.versions(memoryId, offset);
        setHistory((current) => offset && current ? { ...current,
          versions: [...current.versions, ...result.versions], sources: [...current.sources, ...result.sources],
        } : result);
        setHasMore(result.has_more);
      } else {
        const result = await memoriesApi.changes(agentId || undefined, layer, offset);
        setChanges((current) => offset ? [...current, ...result] : result);
        setHasMore(result.length === 50);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : '加载修改记录失败'); }
    finally { setLoading(false); }
  }, [memoryId, agentId, layer]);
  useEffect(() => { void load(); }, [load]);
  const mutate = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try {
      await operation(); message.success('已更新记忆，原状态已保留在修改记录中');
      onChanged(); await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '操作失败'); }
    finally { setBusy(false); }
  };
  return <Drawer open width={850} title={memoryId ? '来源与版本记录' : '修改记录与撤销'}
    onClose={() => { if (!busy) onClose(); }}
    extra={<Button disabled={loading || busy} onClick={() => void load()}>刷新记录</Button>}>
    <p className="mb-4 text-muted-foreground">恢复版本会生成新版本；撤销合并会恢复整组原始记忆。已有后续修改时会阻止直接撤销，避免覆盖新内容。</p>
    {error && <Alert className="mb-4" type="error" showIcon message={error} />}
    <Spin spinning={loading || busy}>
      {memoryId && history ? <div className="space-y-4">
        {history.versions.map((version) => <section key={version.version} className="rounded-lg border border-border p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <strong>v{version.version} · {LABELS[version.kind] || version.kind}</strong>
            <span className="text-xs text-muted-foreground">{new Date(version.created_at).toLocaleString('zh-CN')}</span>
            {history.current_version === version.version ? <Tag color="blue">当前版本</Tag>
              : history.current_version != null && version.snapshot.exists && <Popconfirm title="恢复此版本的正文、层级、范围和元数据？"
                description="当前内容仍会保留在版本记录中。" okText="确认恢复" cancelText="取消"
                onConfirm={() => mutate(() => memoriesApi.restoreVersion(memoryId, version.version, history.current_version!))}>
                <Button size="small" disabled={busy}>恢复此版本</Button>
              </Popconfirm>}
          </div>
          <Snapshot value={version.snapshot} sessions={history.sources} />
        </section>)}
        {history.legacy_revisions.length > 0 && <Collapse items={[{
          key: 'legacy', label: '旧版正文记录（非完整快照，仅供参考）',
          children: <>{history.legacy_revisions.map((revision, index) => <p key={index} className="mb-3 whitespace-pre-wrap">{revision.content}</p>)}</>,
        }]} />}
      </div> : !memoryId && <>
        {changes.length === 0 && !loading && <Empty description="当前范围暂无修改记录" />}
        <Collapse items={changes.map((change) => ({
          key: change.id,
          label: `${LABELS[change.kind] || change.kind} · ${new Date(change.created_at).toLocaleString('zh-CN')} · ${Object.keys(change.after).length} 条`,
          children: <div className="space-y-4">
            {Object.keys(change.after).map((id) => <section key={id} className="border-b border-border pb-4">
              <p className="mb-2 text-xs text-muted-foreground break-all">记忆 {id}</p>
              <div className="grid gap-4 md:grid-cols-2">
                <div><strong>修改前</strong><Snapshot value={change.before[id]} /></div>
                <div><strong>修改后</strong><Snapshot value={change.after[id]} /></div>
              </div>
            </section>)}
            {change.undone_by ? <Tag>已撤销</Tag> : <Popconfirm title="撤销整次操作？"
              description="将恢复所有相关记忆；如果其中任何一条又被修改，将拒绝撤销。" okText="确认撤销" cancelText="取消"
              onConfirm={() => mutate(() => memoriesApi.undoChange(change.id))}>
              <Button disabled={busy}>撤销这次{LABELS[change.kind] || '操作'}</Button>
            </Popconfirm>}
          </div>,
        }))} />
      </>}
      {hasMore && <Button className="mt-4" disabled={loading || busy}
        onClick={() => void load(memoryId ? history?.versions.length || 0 : changes.length)}>加载更早记录</Button>}
    </Spin>
  </Drawer>;
}
