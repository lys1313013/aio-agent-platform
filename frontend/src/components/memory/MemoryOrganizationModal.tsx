import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Empty, Input, Modal, Spin } from 'antd';
import { memoriesApi } from '@/lib/api';
import type { MemoryOrganizePreview } from '@/lib/types';
import MemorySources from './MemorySources';

export default function MemoryOrganizationModal({ layer, agentId, ids, onClose, onChanged }: {
  layer: 'L1' | 'L2'; agentId: string; ids?: string[]; onClose: () => void; onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [preview, setPreview] = useState<MemoryOrganizePreview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [contents, setContents] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (offset = 0) => {
    setLoading(true); setError(null); setPreview(null); setSelected([]);
    try {
      const result = await memoriesApi.previewOrganization({ layer, agent_id: agentId || null, ids, offset });
      setPreview(result);
      setContents(Object.fromEntries(result.groups.map((group) => [group.id, group.content])));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '生成预览失败');
    } finally { setLoading(false); }
  }, [layer, agentId, ids]);
  useEffect(() => { void load(); }, [load]);
  const apply = async () => {
    if (!preview) return;
    setSubmitting(true); setError(null);
    try {
      await memoriesApi.applyOrganization(preview.id, selected.map((id) => ({ id, content: contents[id].trim() })));
      message.success('合并完成，可在“修改记录”中撤销');
      onChanged(); onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '合并失败');
    } finally { setSubmitting(false); }
  };
  return <Modal open width={900} title="整理预览" onCancel={() => { if (!submitting) onClose(); }}
    footer={<div className="flex flex-wrap justify-end gap-2">
      <Button onClick={() => void load()} disabled={loading || submitting}>重新预览</Button>
      {preview?.next_offset != null && <Button disabled={loading || submitting}
        onClick={() => void load(preview.next_offset!)}>下一批（放弃本批预览）</Button>}
      <Button onClick={onClose} disabled={submitting}>取消</Button>
      <Button type="primary" loading={submitting}
        disabled={loading || selected.length === 0 || selected.some((id) => !contents[id]?.trim())}
        onClick={() => void apply()}>确认合并 {selected.length} 组</Button>
    </div>}>
    <p className="mb-3 text-muted-foreground">核对原始内容、来源及合并建议，勾选需要合并的组。可先修改合并后的正文；确认前不会改动记忆。</p>
    {error && <Alert className="mb-3" type="error" showIcon message={error} />}
    <Spin spinning={loading} tip="正在生成整理建议…">
      <div className="min-h-24 space-y-4">
        {preview && <p>本批检查 {preview.scanned} 条，当前范围共 {preview.total} 条。</p>}
        {preview?.warnings.map((warning) => <Alert key={warning} type="warning" message={warning} showIcon />)}
        {preview?.groups.length === 0 && <Empty description="本批未发现可合并建议，可返回列表选择相关记忆后再预览" />}
        {preview?.groups.map((group) => <section key={group.id} className="rounded-lg border border-border p-4">
          <Checkbox checked={selected.includes(group.id)} disabled={submitting}
            onChange={(event) => setSelected((current) => event.target.checked ? [...current, group.id] : current.filter((id) => id !== group.id))}>
            合并 {group.ids.length} 条记忆
          </Checkbox>
          <p className="my-2 text-sm text-muted-foreground">{group.reason}</p>
          <div className="grid gap-4 md:grid-cols-2">
            <div><strong>合并前</strong>{group.ids.map((id) => <div key={id} className="my-2 rounded bg-muted/40 p-3">
              <p className="whitespace-pre-wrap">{preview.originals[id].content}</p>
              <MemorySources metadata={preview.originals[id].metadata} />
            </div>)}</div>
            <div><label htmlFor={`merged-${group.id}`} className="font-semibold">合并后（可纠正）</label>
              <Input.TextArea id={`merged-${group.id}`} className="mt-2" autoSize={{ minRows: 6, maxRows: 16 }}
                maxLength={5000} showCount value={contents[group.id]} disabled={submitting}
                onChange={(event) => setContents((current) => ({ ...current, [group.id]: event.target.value }))} />
              <p className="mt-6 text-xs text-muted-foreground">保留目标记忆 ID，汇总来源；原始内容与元数据完整保存在版本记录中。</p>
            </div>
          </div>
        </section>)}
      </div>
    </Spin>
  </Modal>;
}
