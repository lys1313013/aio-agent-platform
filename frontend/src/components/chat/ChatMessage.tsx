import { useState } from 'react';
import { CopyOutlined, CheckOutlined, EditOutlined, NodeIndexOutlined, BulbOutlined } from '@ant-design/icons';
import { Button, App, Input, Collapse, Tag, Image } from 'antd';
import { cn, parseThinkBlocks } from '@/lib/utils';
import { withImageAuth } from '@/lib/auth';
import type { Message, ToolCallInfo, DelegationInfo, PersistedConfirmation } from '@/lib/types';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeBlock from './CodeBlock';
import ToolCallCard from './ToolCallCard';
import DelegationCard from './DelegationCard';
import { ConfirmationCard } from '../confirmation';

interface Props {
  message: Message;
  onEditResend?: (content: string) => void;
}

/** Compact card for delegate_task entries in saved message history */
function DelegateTaskCard({ toolCall }: { toolCall: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const task = toolCall.arguments.task as string | undefined;
  const context = toolCall.arguments.context as string | undefined;
  const isSuccess = toolCall.result?.status === 'ok';
  const isError = toolCall.result?.status === 'err';

  return (
    <div className="rounded-lg border border-cyan-200 dark:border-cyan-800/50 overflow-hidden bg-cyan-50/50 dark:bg-cyan-950/20">
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-cyan-100/50 dark:hover:bg-cyan-900/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <NodeIndexOutlined className="text-cyan-500 flex-shrink-0" />
          <span className="text-sm font-medium text-cyan-900 dark:text-cyan-100">委派任务</span>
          <Tag color="cyan" className="text-xs">子智能体</Tag>
        </div>
        <div className="flex-shrink-0 ml-2 flex items-center gap-2">
          {isSuccess && <Tag color="success" className="text-xs">完成</Tag>}
          {isError && <Tag color="error" className="text-xs">失败</Tag>}
          <span className="text-muted-foreground text-xs">
            {expanded ? '▼' : '▶'}
          </span>
        </div>
      </div>

      {task && (
        <div className="px-3 pb-2">
          <p className="text-sm text-foreground">
            {task.length > 100 && !expanded ? task.slice(0, 100) + '...' : task}
          </p>
        </div>
      )}

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-cyan-200 dark:border-cyan-800/50 pt-2">
          {context && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">上下文</div>
              <p className="text-xs text-foreground">{context}</p>
            </div>
          )}
          {toolCall.result?.preview && (() => {
            const { thinking: previewThinking, content: previewContent } = parseThinkBlocks(toolCall.result.preview);
            const hasPreviewThinking = previewThinking.length > 0;
            const hasPreviewContent = previewContent.length > 0;

            return (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">
                  {isSuccess ? '执行结果' : '错误信息'}
                </div>
                <div
                  className={cn(
                    'text-xs p-2 rounded max-h-60 space-y-2',
                    isError
                      ? 'bg-red-50 dark:bg-red-950/20 text-red-900 dark:text-red-100'
                      : 'bg-green-50 dark:bg-green-950/20',
                  )}
                >
                  {hasPreviewThinking && (
                    <Collapse
                      ghost
                      size="small"
                      defaultActiveKey={[]}
                      items={[
                        {
                          key: 'preview-thinking',
                          label: (
                            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                              <BulbOutlined />
                              推理过程
                            </span>
                          ),
                          children: (
                            <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                              {previewThinking}
                            </p>
                          ),
                        },
                      ]}
                    />
                  )}
                  {hasPreviewContent && (
                    <div className="prose prose-xs max-w-none dark:prose-invert">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {previewContent}
                      </ReactMarkdown>
                    </div>
                  )}
                  {!hasPreviewThinking && !hasPreviewContent && (
                    <pre className="whitespace-pre-wrap break-all">
                      {toolCall.result.preview}
                    </pre>
                  )}
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

export default function ChatMessage({ message: msg, onEditResend }: Props) {
  const { message: msgApi } = App.useApp();
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(msg.content || '');

  const isUser = msg.role === 'user';
  const isSystem = msg.role === 'system';

  // Parse tool calls from message, preserving original LLM order
  // Also extract delegation details for delegate_task entries
  const allToolCalls: (ToolCallInfo & { delegation?: DelegationInfo })[] = (msg.tool_calls || []).map((tc) => ({
    id: (tc.id as string) || '',
    name: (tc.name as string) || '',
    arguments: (tc.arguments as Record<string, unknown>) || {},
    result: tc.result as { status: string; preview: string } | undefined,
    delegation: tc.delegation as DelegationInfo | undefined,
    confirmation: tc.confirmation as PersistedConfirmation | undefined,
  }));
  // AskUserQuestion confirmations are rendered as standalone read-only cards,
  // not inside the collapsed "tools used" group.
  const confirmationCalls = allToolCalls.filter(
    (tc) => tc.name === 'AskUserQuestion' && tc.confirmation,
  );
  const nonConfirmationCalls = allToolCalls.filter((tc) => tc.name !== 'AskUserQuestion');
  const hasToolCalls = nonConfirmationCalls.length > 0;
  const regularToolCalls = nonConfirmationCalls.filter(tc => tc.name !== 'delegate_task');

  const handleCopy = async () => {
    if (msg.content) {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      msgApi.success('已复制');
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleEditSave = () => {
    if (editContent.trim() && onEditResend) {
      onEditResend(editContent.trim());
      setEditing(false);
    }
  };

  const handleEditChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setEditContent(e.target.value);
  };

  return (
    <div
      className="group flex gap-3"
      style={
        isUser
          ? { marginLeft: 'auto', flexDirection: 'row-reverse' }
          : undefined
      }
    >
      {/* Avatar */}
      <div
        className={cn(
          'flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full',
          isUser ? 'bg-primary/10' : isSystem ? 'border border-border bg-card' : 'bg-muted',
        )}
      >
        <span className={cn('text-sm font-medium', isUser ? 'text-primary' : 'text-muted-foreground')}>
          {isUser ? '我' : isSystem ? '系' : 'AI'}
        </span>
      </div>

      {/* Message content */}
      <div
        className={cn(
          'space-y-2',
          isUser ? 'flex min-w-0 max-w-[80%] shrink-0 flex-col items-end' : 'flex-1',
        )}
      >
        {editing ? (
          <div className="space-y-2">
            <Input.TextArea
              value={editContent}
              onChange={handleEditChange}
              autoSize={{ minRows: 2, maxRows: 8 }}
            />
            <div className="flex gap-2">
              <Button size="small" type="primary" onClick={handleEditSave}>
                保存并重发
              </Button>
              <Button size="small" onClick={() => setEditing(false)}>
                取消
              </Button>
            </div>
          </div>
        ) : (
          <>
            {/* Tool calls (for assistant messages) — rendered in original LLM order */}
            {hasToolCalls && (
              <div className="space-y-2">
                <Collapse
                  ghost
                  defaultActiveKey={regularToolCalls.length <= 3 ? ['1'] : []}
                  items={[
                    {
                      key: '1',
                      label: (
                        <span className="text-sm text-muted-foreground">
                          {regularToolCalls.every((tc) => tc.name.startsWith('memory_'))
                            ? '记忆操作'
                            : `使用了 ${regularToolCalls.length} 次工具`}
                        </span>
                      ),
                      children: (
                        <div className="space-y-2">
                          {nonConfirmationCalls.map((tc) =>
                            tc.name === 'delegate_task' ? (
                              tc.delegation ? (
                                <DelegationCard key={tc.id} delegation={tc.delegation} />
                              ) : (
                                <DelegateTaskCard key={tc.id} toolCall={tc} />
                              )
                            ) : (
                              <ToolCallCard key={tc.id} toolCall={tc} />
                            )
                          )}
                        </div>
                      ),
                    },
                  ]}
                />
              </div>
            )}

            {/* Confirmation cards (AskUserQuestion) — read-only history view */}
            {confirmationCalls.map((tc) => (
              <ConfirmationCard
                key={tc.id}
                confirmationId={tc.confirmation!.confirmation_id}
                question={tc.confirmation!.question}
                mode={tc.confirmation!.mode}
                options={tc.confirmation!.options}
                tableSchema={tc.confirmation!.table_schema}
                context={tc.confirmation!.context}
                resolved={
                  tc.confirmation!.resolved ?? {
                    confirmation_id: tc.confirmation!.confirmation_id,
                    status: 'timeout',
                    resolved_at: msg.created_at,
                  }
                }
              />
            ))}

            {/* Image attachments (user messages only) */}
            {isUser && msg.attachments && msg.attachments.length > 0 && (
              <Image.PreviewGroup>
                <div className="flex flex-wrap gap-3 mb-2 max-w-lg">
                  {msg.attachments.map((att) => (
                    <div
                      key={att.key}
                      className="group relative cursor-pointer overflow-hidden rounded-lg shadow-sm hover:shadow-md transition-all duration-200 hover:scale-[1.02]"
                    >
                      <Image
                        src={withImageAuth(att.url)}
                        alt={att.filename}
                        width={160}
                        height={160}
                        className="object-cover"
                        preview={{ src: withImageAuth(att.url) }}
                        style={{ display: 'block' }}
                      />
                      {/* Hover overlay with filename */}
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-2 py-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                        <p className="text-xs text-white truncate" title={att.filename}>
                          {att.filename}
                        </p>
                      </div>
                      {/* Click hint icon */}
                      <div className="absolute top-2 right-2 bg-black/50 rounded-full w-6 h-6 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                        <svg className="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                        </svg>
                      </div>
                    </div>
                  ))}
                </div>
              </Image.PreviewGroup>
            )}

            {/* File attachments (user messages only) */}
            {isUser && msg.file_attachments && msg.file_attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {msg.file_attachments.map((f) => (
                  <div
                    key={f.file_id}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-background/50 text-sm"
                    title={f.filename}
                  >
                    <svg className="w-4 h-4 text-muted-foreground flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <div className="flex flex-col min-w-0">
                      <span className="truncate max-w-[200px] font-medium text-foreground">{f.filename}</span>
                      <span className="text-xs text-muted-foreground">
                        {f.size > 1024 * 1024
                          ? `${(f.size / (1024 * 1024)).toFixed(1)} MB`
                          : f.size > 1024
                          ? `${(f.size / 1024).toFixed(1)} KB`
                          : `${f.size} B`}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Text content */}
            {msg.content && (() => {
              const { thinking: thinkContent, content: visibleContent } =
                isUser
                  ? { thinking: '', content: msg.content }
                  : isSystem
                    ? { thinking: '', content: msg.content }
                    : parseThinkBlocks(msg.content);
              const hasThinkContent = thinkContent.length > 0;

              return (
                <>
                  {/* Thinking block — collapsed by default for saved messages */}
                  {hasThinkContent && (
                    <Collapse
                      ghost
                      items={[
                        {
                          key: '1',
                          label: (
                            <span className="flex items-center gap-2 text-sm text-muted-foreground">
                              <BulbOutlined />
                              推理过程
                            </span>
                          ),
                          children: (
                            <div className="prose prose-sm max-w-none dark:prose-invert text-muted-foreground">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {thinkContent}
                              </ReactMarkdown>
                            </div>
                          ),
                        },
                      ]}
                    />
                  )}

                  {/* Visible message content */}
                  {visibleContent && (
                    <div
                      className={cn(
                        'inline-block max-w-full rounded-2xl px-4 py-2.5',
                        isUser
                          ? 'bg-primary text-primary-foreground'
                          : isSystem
                            ? 'border border-border bg-card text-foreground'
                            : 'bg-muted text-foreground',
                      )}
                    >
                      {isUser ? (
                        <p
                          className="whitespace-pre-wrap break-words text-sm"
                          style={{ overflowWrap: 'anywhere' }}
                        >
                          {visibleContent}
                        </p>
                      ) : (
                        <div className="prose prose-sm max-w-none break-words dark:prose-invert">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              code: ({ node, className, children, ...props }) => {
                                const match = /language-(\w+)/.exec(className || '');
                                const codeString = String(children).replace(/\n$/, '');
                                return match ? (
                                  <CodeBlock
                                    language={match[1]}
                                    code={codeString}
                                  />
                                ) : (
                                  <code
                                    className="rounded bg-black/10 dark:bg-white/15 px-1.5 py-0.5 text-[0.85em] font-mono"
                                    {...props}
                                  >
                                    {children}
                                  </code>
                                );
                              },
                            }}
                          >
                            {visibleContent}
                          </ReactMarkdown>
                        </div>
                      )}
                    </div>
                  )}
                </>
              );
            })()}

            {/* Actions */}
            <div
              className={cn(
                'flex w-full items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity',
                isUser ? 'flex-row-reverse' : 'justify-end',
              )}
            >
              <Button
                type="text"
                size="small"
                icon={copied ? <CheckOutlined /> : <CopyOutlined />}
                onClick={handleCopy}
              />
              {isUser && onEditResend && (
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => setEditing(true)}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
