import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Alert, App, Button, Empty, Form, Input, Modal, Select, Spin, Tag, Tooltip } from 'antd';
import { CloseOutlined, DeleteOutlined, PaperClipOutlined, PlusOutlined, PushpinOutlined, ReloadOutlined, SendOutlined, SettingOutlined, StopOutlined, TeamOutlined } from '@ant-design/icons';
import { chatApi, portalApi } from '@/lib/api';
import { getAgentIcon } from '@/lib/agent-icons';
import { mergeRoom, mergeRoomHistory, roomsApi, RUNNING, STATUS } from '@/lib/rooms';
import type { Room, RoomMessage, RoomSend, RoomSummary, RoomTask } from '@/lib/rooms';
import type { ChatAttachment, FileAttachmentRef, PortalAgent } from '@/lib/types';
import RoomMentionInput from '@/components/chat/RoomMentionInput';
import { ALL_MEMBERS, emptyMentionDraft, mentionRecipients, readMentionDraft } from '@/lib/roomMentions';
import ChatMessage from '@/components/chat/ChatMessage';
import { ConfirmationCard } from '@/components/confirmation';

const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';

function RoomEditor({ room, agents, onCancel, onSaved }: {
  room?: Room; agents: PortalAgent[]; onCancel: () => void; onSaved: (room: Room) => void;
}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const { message } = App.useApp();
  const selected: string[] = Form.useWatch('agent_ids', form) || [];
  const currentDefault = room?.members.find(m => m.id === room.default_member_id)?.agent_id;
  const options = new Map(agents.map(a => [a.id, { value: a.id, label: a.name }]));
  room?.members.filter(m => m.is_active).forEach(m => {
    if (!options.has(m.agent_id)) options.set(m.agent_id, { value: m.agent_id, label: `${m.name}（不可用）` });
  });

  const save = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const result = room ? await roomsApi.update(room.id, values) : await roomsApi.create(values);
      onSaved(result);
    } catch (error) { if (!(error && typeof error === 'object' && 'errorFields' in error)) message.error(errorText(error)); }
    finally { setSaving(false); }
  };

  return (
    <Modal open title={room ? '聊天室设置' : '新建聊天室'} okText="保存" cancelText="取消" confirmLoading={saving} onOk={() => void save()} onCancel={onCancel}>
      <Form form={form} layout="vertical" initialValues={{
        title: room?.title || '新聊天室', goal: room?.goal || '',
        agent_ids: room?.members.filter(m => m.is_active).map(m => m.agent_id) || [],
        default_agent_id: currentDefault,
      }}>
        <Form.Item name="title" label="聊天室名称" rules={[{ required: true, whitespace: true, max: 512 }]}><Input maxLength={512} /></Form.Item>
        <Form.Item name="goal" label="讨论目标" rules={[{ required: true, whitespace: true, max: 10000 }]}>
          <Input.TextArea rows={3} maxLength={10000} placeholder="例如：评审订单系统需求，明确实现难点和测试范围" />
        </Form.Item>
        <Form.Item name="agent_ids" label="邀请智能体（最多 5 位）" rules={[{ required: true, type: 'array', min: 1, max: 5 }]}>
          <Select mode="multiple" maxCount={5} optionFilterProp="label" options={[...options.values()]} placeholder="选择不同专业的智能体" />
        </Form.Item>
        <Form.Item name="default_agent_id" label="默认回答成员" rules={[
          { validator: (_, value) => !value || selected.includes(value) ? Promise.resolve() : Promise.reject(new Error('请选择房间内的默认成员')) },
        ]}>
          <Select allowClear options={selected.flatMap(id => options.has(id) ? [options.get(id)!] : [])} placeholder="未选择时由第一位成员回答" />
        </Form.Item>
        <p className="text-xs text-muted-foreground">每位成员使用自己的专业配置。默认成员只负责未点名的问题，其他成员由你点名。</p>
      </Form>
    </Modal>
  );
}

function RoomChat({ id, agents, onChanged, onDeleted }: {
  id: string; agents: PortalAgent[]; onChanged: () => void; onDeleted: () => void;
}) {
  const [room, setRoom] = useState<Room | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [connection, setConnection] = useState('connected');
  const [editing, setEditing] = useState(false);
  const [draftState, setDraftState] = useState(() => readMentionDraft(sessionStorage.getItem(`room-draft:${id}`)));
  const draft = draftState.text;
  const { all, ids: targets } = mentionRecipients(draftState);
  const [quote, setQuote] = useState<RoomMessage | null>(null);
  const [images, setImages] = useState<ChatAttachment[]>([]);
  const [files, setFiles] = useState<FileAttachmentRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryMember, setSummaryMember] = useState<string>();
  const [olderLoading, setOlderLoading] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const keepBottom = useRef(true);
  const mounted = useRef(true);
  // Reuse the exact id after an uncertain network response, until the request body changes.
  const pendingSend = useRef<{ signature: string; id: string }>();
  const { message, modal } = App.useApp();

  const accept = useCallback((next: Room) => {
    if (mounted.current) setRoom(previous => mergeRoom(previous, next));
  }, []);
  const refresh = useCallback(async () => {
    const next = await roomsApi.get(id);
    accept(next);
    onChanged();
  }, [id, accept, onChanged]);

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    let close: (() => void) | undefined;
    roomsApi.get(id).then(next => {
      if (cancelled) return;
      accept(next);
      close = roomsApi.watch(id, nextRoom => {
        if (!cancelled) accept(nextRoom);
      }, state => { if (!cancelled) setConnection(state); });
    }).catch(error => { if (!cancelled) setLoadError(errorText(error)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; mounted.current = false; close?.(); };
  }, [id, accept]);

  useEffect(() => { sessionStorage.setItem(`room-draft:${id}`, JSON.stringify(draftState)); }, [id, draftState]);
  useEffect(() => {
    if (keepBottom.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [room?.revision]);

  const activeRun = room?.runs.find(r => RUNNING.has(r.status));
  const busy = Boolean(activeRun);
  const editable = !busy && !submitting && connection !== 'denied';
  const canSend = editable && !room?.is_archived && !uploading;
  const members = room?.members.filter(m => m.is_active) || [];
  const selected = all ? members.filter(m => m.available) : targets.length
    ? targets.map(mid => members.find(m => m.id === mid)).filter((m): m is NonNullable<typeof m> => Boolean(m))
    : members.filter(m => m.id === room?.default_member_id);

  const send = async (summary = false) => {
    if (!room || !canSend) return;
    const selectedSummary = summaryMember || room.default_member_id;
    if (summary && !members.some(m => m.id === selectedSummary && m.available)) {
      message.warning('请选择可用的总结成员'); return;
    }
    if (!summary && (!selected.length || selected.some(m => !m.available))) {
      message.warning('请调整不可用的接收成员'); return;
    }
    const data = {
      message: summary ? '' : draft.trim(),
      mode: summary ? 'summary' : all ? 'all' : targets.length ? 'mentions' : 'default',
      member_ids: summary ? [selectedSummary] : all ? [] : targets,
      reply_to_id: summary ? undefined : quote?.id,
      attachments: summary ? [] : images,
      file_attachments: summary ? [] : files,
    } as Omit<RoomSend, 'request_id'>;
    if (!summary && !data.message && !images.length && !files.length) return;
    const signature = JSON.stringify(data);
    if (pendingSend.current?.signature !== signature) pendingSend.current = { signature, id: crypto.randomUUID() };
    setSubmitting(true);
    try {
      await roomsApi.send(id, { ...data, request_id: pendingSend.current.id });
      pendingSend.current = undefined;
      if (!summary) { setDraftState(emptyMentionDraft()); setImages([]); setFiles([]); setQuote(null); }
      setSummaryOpen(false);
      keepBottom.current = true;
      await refresh();
    } catch (error) { message.error(errorText(error)); }
    finally { if (mounted.current) setSubmitting(false); }
  };

  const mutate = async (action: () => Promise<unknown>) => {
    try { await action(); await refresh(); }
    catch (error) { message.error(errorText(error)); }
  };

  const retry = (task: RoomTask, msg?: RoomMessage) => {
    const requestId = crypto.randomUUID();
    const hasTools = Boolean(msg?.tool_calls?.length);
    modal.confirm({
      title: '重试该成员', okText: '重新执行', cancelText: '取消',
      content: hasTools
        ? '原任务调用过工具。请先检查该消息下的工具结果和文件变化；重新执行可能重复修改文件或发出外部请求，已完成动作不会回滚。'
        : '将使用原问题和当时的公开讨论，重新执行这一位成员。其他成员不会重跑。',
      onOk: async () => { await roomsApi.retry(id, task.id, requestId, hasTools); await refresh(); },
    });
  };

  const upload = async (file: File) => {
    if (!canSend || uploading) return;
    const image = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(file.type);
    if (image ? images.length >= 4 : files.length >= 10) { message.warning('附件数量已达上限'); return; }
    if (file.size > (image ? 10 : 500) * 1024 * 1024) { message.warning(image ? '图片最大 10 MB' : '文件最大 500 MB'); return; }
    setUploading(true);
    try {
      if (image) {
        const result = await chatApi.uploadAttachment(file, id);
        if (mounted.current) setImages(previous => [...previous, result]);
      } else {
        const result = await chatApi.uploadFile(file, id);
        const { file_id, filename, mime, size, workspace_path } = result;
        if (mounted.current) setFiles(previous => [...previous, { file_id, filename, mime, size, workspace_path }]);
      }
    } catch (error) { message.error(errorText(error)); }
    finally { if (mounted.current) setUploading(false); }
  };

  if (loading) return <div className="flex flex-1 items-center justify-center"><Spin /></div>;
  if (!room || loadError) return <div className="p-6"><Alert type="error" title={loadError || '聊天室不存在'} /></div>;
  if (connection === 'denied') return <div className="p-6"><Alert type="warning" title="聊天室已删除或访问权限已变化，请返回列表。" /></div>;

  const currentTasks = activeRun ? room.tasks.filter(t => t.run_id === activeRun.id).sort((a, b) => a.position - b.position) : [];
  const latestRun = room.runs[0];
  const latestTasks = room.tasks.filter(t => t.run_id === latestRun?.id);
  const totalTokens = latestTasks.length && latestTasks.every(t => t.token_usage) ? latestTasks.reduce((sum, t) => sum + t.token_usage!.total_tokens, 0) : null;

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <header className="border-b border-border bg-card px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <h1 className="truncate text-base font-semibold">{room.title}{room.is_archived && <Tag className="ml-2">已归档</Tag>}</h1>
          <div className="flex gap-1">
            <Tooltip title="聊天室设置"><Button aria-label="聊天室设置" icon={<SettingOutlined />} disabled={!editable} onClick={() => setEditing(true)} /></Tooltip>
            <Tooltip title={room.is_pinned ? '取消置顶' : '置顶'}><Button aria-label="置顶" icon={<PushpinOutlined />} type={room.is_pinned ? 'primary' : 'default'} disabled={!editable} onClick={() => void mutate(() => roomsApi.update(id, { is_pinned: !room.is_pinned }))} /></Tooltip>
            <Button disabled={!editable} onClick={() => void mutate(() => roomsApi.update(id, { is_archived: !room.is_archived }))}>{room.is_archived ? '恢复' : '归档'}</Button>
            <Button danger aria-label="删除聊天室" icon={<DeleteOutlined />} disabled={!editable} onClick={() => modal.confirm({ title: '删除聊天室？', content: '讨论记录和运行记录将被删除，工作区文件保留。', okText: '删除', cancelText: '取消', onOk: async () => { await roomsApi.delete(id); onDeleted(); } })} />
          </div>
        </div>
        <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-sm text-muted-foreground" title={room.goal}>{room.goal}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {members.map(m => <Tooltip key={m.id} title={m.description}><Tag className="inline-flex items-center gap-1" color={m.available ? 'blue' : 'default'}>{getAgentIcon(m.icon || 'robot', undefined, 14)}{m.name}{m.id === room.default_member_id ? ' · 默认' : ''}{!m.available ? ' · 不可用' : ''}</Tag></Tooltip>)}
        </div>
      </header>
      {connection === 'retrying' && <Alert type="warning" title="连接中断，正在重连。已提交的任务继续执行，请勿重复发送。" />}
      {activeRun && <div className="border-b border-border bg-muted/40 px-4 py-2 text-sm" aria-live="polite">
        {STATUS[activeRun.status]}：{currentTasks.map(t => `${room.members.find(m => m.id === t.member_id)?.name || '成员'}（${STATUS[t.status]}）`).join(' → ')}
      </div>}
      <div ref={scrollRef} onScroll={() => { const el = scrollRef.current; if (el) keepBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90; }} className="min-h-0 flex-1 overflow-y-auto" data-ui-exclude>
        <div className="mx-auto max-w-4xl space-y-5 px-4 py-5">
          {room.has_more && <Button block loading={olderLoading} onClick={async () => {
            setOlderLoading(true); keepBottom.current = false;
            try {
              const page = await roomsApi.get(id, room.messages[0]?.sequence);
              setRoom(previous => previous ? mergeRoomHistory(previous, page) : page);
            } catch (error) { message.error(errorText(error)); }
            finally { setOlderLoading(false); }
          }}>加载更早的讨论</Button>}
          {room.messages.length <= 1 && <div className="rounded-xl border border-dashed border-border p-6 text-center text-muted-foreground">邀请成员后，用 @ 点名开始讨论；也可以让全体依次回答。</div>}
          {room.messages.map(msg => {
            const task = room.tasks.find(t => t.message_id === msg.id);
            const quoted = room.messages.find(m => m.id === msg.reply_to_id);
            return <article key={msg.id} id={`room-message-${msg.id}`} className="scroll-mt-4">
              {msg.role === 'system' ? <p className="text-center text-xs text-muted-foreground">{msg.content}</p> : <>
                <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                  {getAgentIcon(msg.icon || 'robot', 'text-primary', 16)}<strong>{msg.name}</strong>
                  {msg.role === 'assistant' && <Tag color={msg.status === 'failed' ? 'error' : msg.status === 'completed' ? 'success' : 'processing'}>{STATUS[msg.status] || msg.status}</Tag>}
                  <span className="text-xs text-muted-foreground">#{msg.sequence}</span>
                  {task && <span className="text-xs text-muted-foreground">Token：{task.token_usage?.total_tokens ?? '未知'}{task.duration_ms != null ? ` · ${(task.duration_ms / 1000).toFixed(1)} 秒` : ''}</span>}
                  <Button size="small" type="text" disabled={!canSend} onClick={() => setQuote(msg)}>引用</Button>
                  {task?.status === 'failed' && <Button size="small" disabled={!canSend} onClick={() => retry(task, msg)}>重试该成员</Button>}
                </div>
                {msg.reply_to_id && <button className="mb-2 block max-w-full truncate border-l-2 border-primary pl-3 text-left text-xs text-muted-foreground" onClick={async () => {
                  if (!quoted) {
                    try {
                      const source = await roomsApi.getMessage(id, msg.reply_to_id!);
                      modal.info({ title: `引用原消息 · ${source.name}`, width: 720, content: <ChatMessage message={source} workspaceId={room.workspace_id} /> });
                    }
                    catch (error) { message.error(errorText(error)); }
                  } else document.getElementById(`room-message-${quoted.id}`)?.scrollIntoView({ behavior: 'smooth' });
                }}>引用：{quoted ? `${quoted.name}：${quoted.content}` : '更早的消息（加载历史可查看）'}</button>}
                {msg.member_ids?.length ? <div className="mb-2 text-xs text-muted-foreground">接收：{msg.member_ids.map(mid => room.members.find(m => m.id === mid)?.name || '已移除成员').join(' → ')}</div> : null}
                <ChatMessage message={msg} workspaceId={room.workspace_id} />
                {task?.error && <Alert className="mt-2" type="warning" title={task.error} />}
                {task?.confirmation && task.status === 'waiting_user' && !activeRun?.stop_requested && (task.response_submitted
                  ? <Alert type="info" title="答复已提交，等待成员继续执行" />
                  : <ConfirmationCard
                    confirmationId={task.confirmation.confirmation_id}
                    question={task.confirmation.question} mode={task.confirmation.mode}
                    options={task.confirmation.options} context={task.confirmation.context || {}}
                    tableSchema={task.confirmation.table_schema}
                    onRespond={async response => { await roomsApi.respond(id, task.id, task.confirmation!.confirmation_id, response); await refresh(); }}
                  />)}
              </>}
            </article>;
          })}
          {room.tasks.filter(t => t.status === 'failed' && !t.message_id).map(task => <Alert key={task.id} type="error" title={`${room.members.find(m => m.id === task.member_id)?.name || '成员'}：${task.error}`} action={<Button size="small" disabled={!canSend} onClick={() => retry(task)}>重试</Button>} />)}
          {latestRun && !busy && <div className="text-center text-xs text-muted-foreground">最近运行：{STATUS[latestRun.status]} · Token：{totalTokens ?? '未知'}{latestRun.error ? ` · ${latestRun.error}` : ''}</div>}
        </div>
      </div>
      <div className="border-t border-border bg-card p-3 sm:p-4">
        <div className="mx-auto max-w-4xl space-y-2">
          {quote && <div className="flex items-center gap-2 rounded bg-muted px-3 py-2 text-xs"><span className="flex-1 truncate">引用 {quote.name}：{quote.content}</span><Button size="small" type="text" aria-label="取消引用" icon={<CloseOutlined />} onClick={() => setQuote(null)} /></div>}
          <div className="flex flex-wrap gap-1">
            {images.map((image, i) => <Tag key={image.key} closable={canSend} onClose={() => setImages(old => old.filter((_, n) => n !== i))}>{image.filename}</Tag>)}
            {files.map((file, i) => <Tag key={file.file_id} closable={canSend} onClose={() => setFiles(old => old.filter((_, n) => n !== i))}>{file.filename}</Tag>)}
          </div>
          <RoomMentionInput value={draftState} onChange={setDraftState} disabled={room.is_archived} busy={busy}
            candidates={[
              { id: ALL_MEMBERS, label: '全体成员', description: '所有可用成员依次回答' },
              ...members.filter(m => m.available).map(m => ({ id: m.id, label: m.name, icon: m.icon })),
            ]} onSend={() => void send()} />
          <div className="flex items-center justify-between">
            <input ref={uploadRef} type="file" className="hidden" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void upload(file); }} />
            <div className="flex items-center gap-2"><Tooltip title="添加附件"><Button type="text" aria-label="添加附件" icon={<PaperClipOutlined />} loading={uploading} disabled={!canSend} onClick={() => uploadRef.current?.click()} /></Tooltip><Button disabled={!canSend} onClick={() => { setSummaryMember(room.default_member_id); setSummaryOpen(true); }}>总结讨论</Button></div>
            {activeRun ? <Button danger icon={<StopOutlined />} disabled={activeRun.stop_requested} onClick={() => void mutate(() => roomsApi.stop(id, activeRun.id))}>{activeRun.stop_requested ? '停止中' : '停止本轮'}</Button>
              : <Button type="primary" icon={<SendOutlined />} loading={submitting} disabled={!canSend || (!draft.trim() && !images.length && !files.length)} onClick={() => void send()}>发送</Button>}
          </div>
        </div>
      </div>
      {editing && <RoomEditor room={room} agents={agents} onCancel={() => setEditing(false)} onSaved={next => { accept(next); setEditing(false); onChanged(); }} />}
      <Modal open={summaryOpen} title="选择总结成员" okText="开始总结" cancelText="取消" confirmLoading={submitting} onCancel={() => setSummaryOpen(false)} onOk={() => void send(true)}>
        <p className="mb-3 text-sm text-muted-foreground">整理共识、分歧、待确认事项和下一步，只由所选成员回答。</p>
        <Select className="w-full" value={summaryMember} onChange={setSummaryMember} options={members.filter(m => m.available).map(m => ({ value: m.id, label: m.name }))} />
      </Modal>
    </section>
  );
}

export default function RoomsPage() {
  const { roomId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const base = location.pathname.startsWith('/portal') ? '/portal/rooms' : '/rooms';
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [agents, setAgents] = useState<PortalAgent[]>([]);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const { message } = App.useApp();
  const refresh = useCallback(() => {
    roomsApi.list().then(rows => { setRooms(rows); setHasMore(rows.length === 50); })
      .catch(error => message.error(errorText(error))).finally(() => setLoading(false));
  }, [message]);
  useEffect(() => { refresh(); portalApi.listAgents().then(setAgents).catch(error => message.error(errorText(error))); }, [refresh, message]);

  return <div className="flex min-h-0 flex-1 flex-col md:flex-row">
    <aside className="flex max-h-48 shrink-0 flex-col border-b border-border bg-card md:max-h-none md:w-64 md:border-b-0 md:border-r">
      <div className="flex items-center justify-between px-4 py-3"><span className="font-semibold"><TeamOutlined className="mr-2" />协作聊天室</span><Button aria-label="刷新聊天室" type="text" size="small" icon={<ReloadOutlined />} onClick={refresh} /></div>
      <Button className="mx-3 mb-3" type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建聊天室</Button>
      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {loading ? <div className="p-4 text-center"><Spin /></div> : rooms.map(room => <button key={room.id} onClick={() => navigate(`${base}/${room.id}`)} className={`block w-full rounded-lg px-3 py-2 text-left text-sm transition ${roomId === room.id ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`}>
          <div className="truncate font-medium">{room.is_pinned && <PushpinOutlined className="mr-1" />}{room.title}</div>
          <div className="truncate text-xs text-muted-foreground">{room.is_archived ? '已归档 · ' : ''}{room.goal}</div>
        </button>)}
        {!loading && !rooms.length && <p className="p-3 text-xs text-muted-foreground">还没有聊天室</p>}
        {hasMore && <Button block type="text" onClick={async () => { try { const next = await roomsApi.list(rooms.length); setRooms(old => [...old, ...next]); setHasMore(next.length === 50); } catch (error) { message.error(errorText(error)); } }}>加载更多</Button>}
      </nav>
    </aside>
    {roomId ? <RoomChat key={roomId} id={roomId} agents={agents} onChanged={refresh} onDeleted={() => { navigate(base); refresh(); }} />
      : <div className="flex flex-1 items-center justify-center p-6"><Empty image={<TeamOutlined className="text-6xl text-primary/40" />} description={<><p className="text-base">让不同专业的智能体一起讨论</p><p className="mt-2 text-sm text-muted-foreground">共享背景，按需点名，交叉评审并总结结论。</p></>}><Button type="primary" onClick={() => setCreating(true)}>创建第一个聊天室</Button></Empty></div>}
    {creating && <RoomEditor agents={agents} onCancel={() => setCreating(false)} onSaved={room => { setCreating(false); navigate(`${base}/${room.id}`); refresh(); }} />}
  </div>;
}
