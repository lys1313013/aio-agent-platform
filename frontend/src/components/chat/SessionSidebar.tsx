import { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatStore } from '@/stores/chatStore';
import { formatRelativeTime, cn } from '@/lib/utils';
import type { Session } from '@/lib/types';
import {
  PlusOutlined,
  MessageOutlined,
  DeleteOutlined,
  SearchOutlined,
  PushpinOutlined,
  PushpinFilled,
  InboxOutlined,
  EditOutlined,
  CheckOutlined,
  CloseOutlined,
  MoreOutlined,
  UndoOutlined,
  ReloadOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import { Input, Dropdown, App, Tooltip, Spin } from 'antd';
import type { MenuProps } from 'antd';

export default function SessionSidebar({ agentId }: { agentId?: string | null }) {
  const navigate = useNavigate();
  const { modal } = App.useApp();
  const {
    sessions,
    activeSessionId,
    setActiveSession,
    createSession,
    deleteSession,
    renameSession,
    pinSession,
    archiveSession,
    refreshSessions,
    isSessionsLoading,
  } = useChatStore();

  const [searchQuery, setSearchQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [panelOpen, setPanelOpen] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Close panel on click outside
  useEffect(() => {
    if (!panelOpen) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (
        target.closest('.ant-select-dropdown') ||
        target.closest('.ant-popover') ||
        target.closest('.ant-tooltip') ||
        target.closest('.ant-dropdown')
      ) {
        return;
      }
      if (sidebarRef.current && !sidebarRef.current.contains(target)) {
        setPanelOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [panelOpen]);

  const handleNewChat = async () => {
    await createSession('新对话', agentId);
  };

  const handleDelete = (id: string) => {
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
    setPanelOpen(false);
    if (agentId) {
      navigate(`/agents/${agentId}/chat/${id}`, { replace: true });
    }
  };

  const filteredSessions = sessions.filter(
    (s) =>
      searchQuery === '' ||
      (s.title || '').toLowerCase().includes(searchQuery.toLowerCase()),
  );

  const pinnedSessions = filteredSessions.filter((s) => s.is_pinned);
  const regularSessions = filteredSessions.filter((s) => !s.is_pinned && !s.is_archived);
  const archivedSessions = filteredSessions.filter((s) => s.is_archived);

  const groupedRegular = useMemo(() => {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const yesterdayStart = todayStart - 86400000;

    const groups: { today: Session[]; yesterday: Session[]; earlier: Session[] } = {
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
    <div
      ref={sidebarRef}
      className={cn(
        'flex flex-shrink-0 border-r border-border bg-card overflow-hidden transition-all duration-200 ease-in-out',
        panelOpen ? 'w-[340px]' : 'w-11',
      )}
    >
      {/* Icon rail */}
      <div className="flex flex-col items-center py-2 w-11 flex-shrink-0 gap-1">
        <Tooltip title="对话历史" placement="right" mouseEnterDelay={0.5}>
          <button
            onClick={() => {
              setPanelOpen((prev) => !prev);
              if (!panelOpen) refreshSessions(agentId || undefined);
            }}
            className={cn(
              'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
              panelOpen
                ? 'bg-primary/15 text-primary'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            <HistoryOutlined />
          </button>
        </Tooltip>

        <Tooltip title="新对话" placement="right" mouseEnterDelay={0.5}>
          <button
            onClick={handleNewChat}
            className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-r from-[#6366f1] to-[#8b5cf6] text-white shadow-[0_2px_8px_rgba(99,102,241,0.25)] transition-all hover:shadow-[0_4px_12px_rgba(99,102,241,0.35)] hover:-translate-y-[1px] active:translate-y-0"
          >
            <PlusOutlined />
          </button>
        </Tooltip>

        <Tooltip title="刷新" placement="right" mouseEnterDelay={0.5}>
          <button
            onClick={() => refreshSessions(agentId || undefined)}
            className="flex items-center justify-center w-9 h-9 rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ReloadOutlined className="text-sm" />
          </button>
        </Tooltip>
      </div>

      {/* Inline panel */}
      {panelOpen && (
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {/* Panel header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
            <div className="flex items-center gap-2">
              <HistoryOutlined className="text-primary" />
              <span className="text-sm font-semibold">对话历史</span>
            </div>
            <button
              onClick={() => setPanelOpen(false)}
              className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              <CloseOutlined className="text-xs" />
            </button>
          </div>

          {/* Search */}
          <div className="px-3 py-2">
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

          {/* Session list */}
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
                {!searchQuery && (
                  <span className="text-[10px] text-muted-foreground/60">
                    点击「+」开始新对话
                  </span>
                )}
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
                    onDelete={handleDelete}
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
                    onDelete={handleDelete}
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
                    onDelete={handleDelete}
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
                    onDelete={handleDelete}
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
                    onDelete={handleDelete}
                    onPin={handlePin}
                    onArchive={handleArchive}
                  />
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Section Component ----

interface SectionProps {
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
}: SectionProps) {
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

// ---- Session Item Component ----

interface ItemProps {
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
}: ItemProps) {
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
        <div
          className="flex flex-1 items-center gap-1"
          onClick={(e) => e.stopPropagation()}
        >
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
            <div className="mt-0.5 truncate text-[10px] text-muted-foreground/60">
              {formatRelativeTime(session.updated_at)}
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
