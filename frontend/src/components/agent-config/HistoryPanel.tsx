import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { CloseOutlined, HistoryOutlined, MessageOutlined, SearchOutlined, PushpinOutlined, PushpinFilled, InboxOutlined, EditOutlined, CheckOutlined, MoreOutlined, UndoOutlined, DeleteOutlined } from '@ant-design/icons';
import { Input, Typography, Spin, App, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { useChatStore } from '@/stores/chatStore';
import { formatRelativeTime, cn } from '@/lib/utils';
import type { Session } from '@/lib/types';

const { Text } = Typography;

const SESSION_SOURCE_LABELS: Record<string, string> = {
  chat: '界面',
  pet: '宠物',
  api: '接口',
  cron: '定时任务',
  feishu: '飞书',
  wecom: '企微',
  wecom_bot: '企微机器人',
};
export function HistoryPanel({ agentId, closePanel }: { agentId: string; closePanel: () => void; }) {
  const navigate = useNavigate();
  const { modal } = App.useApp();
  const [searchQuery, setSearchQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const {
    sessions,
    activeSessionId,
    setActiveSession,
    deleteSession,
    renameSession,
    pinSession,
    archiveSession,
    refreshSessions,
    isSessionsLoading,
  } = useChatStore();

  useEffect(() => { void refreshSessions(agentId); }, [agentId, refreshSessions]);
  const handleDeleteSession = (id: string) => {
    modal.confirm({
      title: '删除对话？',
      content: '此操作无法撤销。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => deleteSession(id),
    });
  };

  const handleStartRename = (id: string, currentTitle: string) => {
    setEditingId(id);
    setEditValue(currentTitle);
  };

  const handleFinishRename = async (id: string) => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== sessions.find((s) => s.id === id)?.title) {
      await renameSession(id, trimmed);
    }
    setEditingId(null);
  };

  const handleCancelRename = () => {
    setEditingId(null);
    setEditValue('');
  };

  const handlePin = async (id: string, currentPinned: boolean) => {
    await pinSession(id, !currentPinned);
  };

  const handleArchive = async (id: string, currentArchived: boolean) => {
    await archiveSession(id, !currentArchived);
  };

  const handleSelectSession = (id: string) => {
    setActiveSession(id);
    navigate(`/agents/${agentId}/chat/${id}`, { replace: true });
  };

  const filteredSessions = sessions.filter(
    (s) =>
      s.agent_id === agentId && (searchQuery === '' ||
        (s.title || '').toLowerCase().includes(searchQuery.toLowerCase())),
  );

  const pinnedSessions = filteredSessions.filter((s) => s.is_pinned);
  const regularSessions = filteredSessions.filter((s) => !s.is_pinned && !s.is_archived);
  const archivedSessions = filteredSessions.filter((s) => s.is_archived);

  const groupedRegular = useMemo(() => {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const yesterdayStart = todayStart - 86400000;

    const groups: { today: Session[]; yesterday: Session[]; earlier: Session[]; } = {
      today: [],
      yesterday: [],
      earlier: [],
    };

    for (const s of regularSessions) {
      const ts = new Date(s.updated_at).getTime();
      if (ts >= todayStart) groups.today.push(s);
      else if (ts >= yesterdayStart) groups.yesterday.push(s);
      else groups.earlier.push(s);
    }
    return groups;
  }, [regularSessions]);
  return (
    <>
      <div className="flex items-center justify-between border-b border-border/50 bg-card px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <HistoryOutlined />
          </span>
          <div>
            <Text strong className="block text-sm">对话历史</Text>
            <Text type="secondary" className="block text-[10px]">查找和管理历史会话</Text>
          </div>
        </div>
        <button
          onClick={closePanel}
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <CloseOutlined className="text-xs" />
        </button>
      </div>

      <div className="bg-muted/20 px-3 py-2.5">
        <div className="relative">
          <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground/60" />
          <input
            type="text"
            placeholder="搜索对话..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-border bg-background py-1.5 pl-8 pr-8 text-xs text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 flex h-4 w-4 items-center justify-center rounded-full bg-muted text-muted-foreground transition hover:bg-muted-foreground/30"
            >
              <CloseOutlined className="text-[8px]" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {isSessionsLoading ? (
          <div className="px-3 py-8 text-center">
            <Spin size="small" />
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-3 py-10 text-center">
            <MessageOutlined className="text-3xl text-muted-foreground/20" />
            <span className="text-xs text-muted-foreground">
              {searchQuery ? '未找到匹配结果' : '暂无对话'}
            </span>
          </div>
        ) : (
          <div className="space-y-1">
            {pinnedSessions.length > 0 && (
              <SessionSection
                title="已置顶"
                icon={<PushpinFilled className="text-[10px]" />}
                sessions={pinnedSessions}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {groupedRegular.today.length > 0 && (
              <SessionSection
                title="今天"
                sessions={groupedRegular.today}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {groupedRegular.yesterday.length > 0 && (
              <SessionSection
                title="昨天"
                sessions={groupedRegular.yesterday}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {groupedRegular.earlier.length > 0 && (
              <SessionSection
                title="更早"
                sessions={groupedRegular.earlier}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {archivedSessions.length > 0 && (
              <SessionSection
                title="已归档"
                icon={<InboxOutlined className="text-[10px]" />}
                sessions={archivedSessions}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}
          </div>
        )}
      </div>
    </>
  );
}
// Session section component
interface SessionSectionProps {
  title: string;
  icon?: React.ReactNode;
  sessions: Session[];
  activeSessionId: string | null;
  editingId: string | null;
  editValue: string;
  onEditValueChange: (v: string) => void;
  onSelect: (id: string) => void;
  onStartRename: (id: string, title: string) => void;
  onFinishRename: (id: string) => void;
  onCancelRename: () => void;
  onDelete: (id: string) => void;
  onPin: (id: string, currentPinned: boolean) => void;
  onArchive: (id: string, currentArchived: boolean) => void;
}

function SessionSection({
  title,
  icon,
  sessions,
  activeSessionId,
  editingId,
  editValue,
  onEditValueChange,
  onSelect,
  onStartRename,
  onFinishRename,
  onCancelRename,
  onDelete,
  onPin,
  onArchive,
}: SessionSectionProps) {
  return (
    <div className="mb-1">
      {title && (
        <div className="flex items-center gap-1.5 px-3 py-1.5">
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {title}
          </span>
        </div>
      )}
      <div className="space-y-0.5">
        {sessions.map((session) => (
          <SessionItem
            key={session.id}
            session={session}
            isActive={activeSessionId === session.id}
            isEditing={editingId === session.id}
            editValue={editValue}
            onEditValueChange={onEditValueChange}
            onSelect={() => onSelect(session.id)}
            onStartRename={() => onStartRename(session.id, session.title || '')}
            onFinishRename={() => onFinishRename(session.id)}
            onCancelRename={onCancelRename}
            onDelete={() => onDelete(session.id)}
            onPin={() => onPin(session.id, session.is_pinned)}
            onArchive={() => onArchive(session.id, session.is_archived)}
          />
        ))}
      </div>
    </div>
  );
}

// Session item component
interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isEditing: boolean;
  editValue: string;
  onEditValueChange: (v: string) => void;
  onSelect: () => void;
  onStartRename: () => void;
  onFinishRename: () => void;
  onCancelRename: () => void;
  onDelete: () => void;
  onPin: () => void;
  onArchive: () => void;
}

function SessionItem({
  session,
  isActive,
  isEditing,
  editValue,
  onEditValueChange,
  onSelect,
  onStartRename,
  onFinishRename,
  onCancelRename,
  onDelete,
  onPin,
  onArchive,
}: SessionItemProps) {
  const menuItems: MenuProps['items'] = [
    {
      key: 'rename',
      icon: <EditOutlined />,
      label: '重命名',
      onClick: onStartRename,
    },
    {
      key: 'pin',
      icon: session.is_pinned ? <PushpinOutlined /> : <PushpinFilled />,
      label: session.is_pinned ? '取消置顶' : '置顶',
      onClick: onPin,
    },
    {
      key: 'archive',
      icon: session.is_archived ? <UndoOutlined /> : <InboxOutlined />,
      label: session.is_archived ? '取消归档' : '归档',
      onClick: onArchive,
    },
    { type: 'divider' },
    {
      key: 'delete',
      icon: <DeleteOutlined />,
      label: '删除',
      danger: true,
      onClick: onDelete,
    },
  ];

  return (
    <div
      onClick={onSelect}
      className={cn(
        'group relative flex cursor-pointer items-start gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-all',
        isActive
          ? 'bg-muted/60 text-foreground shadow-[0_1px_4px_rgba(0,0,0,0.06),0_0_0_1px_rgba(99,102,241,0.12)]'
          : 'text-muted-foreground hover:bg-muted/40 hover:text-foreground',
      )}
    >
      <MessageOutlined
        className={cn(
          'mt-0.5 flex-shrink-0 text-xs transition',
          isActive ? 'text-primary' : 'text-muted-foreground/40 group-hover:text-muted-foreground',
        )}
      />

      {isEditing ? (
        <div className="flex flex-1 items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <Input
            size="small"
            value={editValue}
            onChange={(e) => onEditValueChange(e.target.value)}
            onPressEnter={onFinishRename}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onCancelRename();
            }}
            autoFocus
          />
          <button
            onClick={onFinishRename}
            className="flex h-6 w-6 items-center justify-center rounded text-green-500 transition hover:bg-green-500/10"
          >
            <CheckOutlined className="text-xs" />
          </button>
          <button
            onClick={onCancelRename}
            className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted"
          >
            <CloseOutlined className="text-xs" />
          </button>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-hidden">
            <div
              className={cn(
                'truncate text-sm leading-snug',
                isActive && 'font-medium text-primary',
              )}
            >
              {session.title || '无标题'}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground/60">
              <span className="truncate">{formatRelativeTime(session.updated_at)}</span>
              <span className="flex-shrink-0 rounded bg-muted/60 px-1 py-px text-[9px] leading-none">
                {SESSION_SOURCE_LABELS[session.source] ?? session.source}
              </span>
            </div>
          </div>

          <Dropdown menu={{ items: menuItems }} trigger={['click']}>
            <button
              onClick={(e) => e.stopPropagation()}
              className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground group-hover:opacity-100"
            >
              <MoreOutlined className="text-xs" />
            </button>
          </Dropdown>
        </>
      )}
    </div>
  );
}
