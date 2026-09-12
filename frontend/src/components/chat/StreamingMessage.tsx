import { useEffect, useState } from 'react';
import type { StreamingState } from '@/lib/types';
import { BulbOutlined, LoadingOutlined } from '@ant-design/icons';
import { Collapse } from 'antd';
import { parseThinkBlocks } from '@/lib/utils';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeBlock from './CodeBlock';
import ToolCallCard from './ToolCallCard';
import DelegationCard from './DelegationCard';
import { ConfirmationCard } from '../confirmation';
import FileChangeList, { resolveToolFileChange } from './FileChangeList';

interface Props {
  streaming: StreamingState;
  /** 紧凑模式：窄浮窗（宠物对话）下缩小头像与间距 */
  compact?: boolean;
  workspaceId?: string | null;
}

export default function StreamingMessage({ streaming, compact, workspaceId }: Props) {
  const [thinkingVisibility, setThinkingVisibility] = useState<Record<string, boolean>>({});

  // Parse <think> blocks from finalText (some LLMs embed thinking inline)
  const { thinking: inlineThinking, content: cleanFinalText } =
    parseThinkBlocks(streaming.finalText, streaming.isStreaming);

  const hasFinalText = cleanFinalText.length > 0;
  // Filter out delegate_task from tool calls (shown separately as delegation cards)
  const visibleToolCalls = streaming.toolCalls.filter(
    tc => tc.name !== 'delegate_task'
  );
  const hasToolCalls = visibleToolCalls.length > 0;
  const hasDelegations = streaming.delegations.length > 0;
  const hasConfirmations = streaming.confirmations.length > 0;
  const hasThinking = streaming.thinkingChunks.length > 0 || inlineThinking.length > 0;

  // Loading: streaming active but no content yet
  const showLoading = streaming.isStreaming && !hasThinking && !hasFinalText && !hasToolCalls && !hasDelegations && !hasConfirmations && streaming.fileChanges.length === 0;

  const [waitingSeconds, setWaitingSeconds] = useState(0);
  useEffect(() => {
    setWaitingSeconds(0);
    if (!showLoading) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setWaitingSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [showLoading]);

  // Waiting for next step: tool calls done but more streaming expected
  const waitingForNextStep =
    streaming.isStreaming && hasToolCalls && !hasFinalText &&
    streaming.toolCalls.some((tc) => tc.result) && !hasDelegations && !hasConfirmations;

  // Build action order with inline thinking from finalText inserted at the end if present
  const orderedActions = [...streaming.actionOrder];
  if (inlineThinking.length > 0) {
    const inlineId = 'thinking-inline';
    // Only add if not already in actionOrder (from previous render)
    if (!orderedActions.some(a => a.id === inlineId)) {
      orderedActions.push({ type: 'thinking' as const, id: inlineId });
    }
  }

  return (
    <div className={`flex ${compact ? 'gap-2' : 'gap-3'}`}>
      {/* Avatar */}
      <div className={`flex flex-shrink-0 items-center justify-center rounded-full bg-muted ${compact ? 'h-7 w-7' : 'h-8 w-8'}`}>
        <span className={`font-medium text-muted-foreground ${compact ? 'text-xs' : 'text-sm'}`}>AI</span>
      </div>

      {/* Content */}
      <div className={`flex-1 ${compact ? 'space-y-2' : 'space-y-3'}`}>
        {/* Loading indicator — no content yet */}
        {showLoading && (
          <div className="space-y-1 py-1 text-muted-foreground" role="status">
            <div className="flex items-center gap-2">
              <LoadingOutlined spin />
              <span className="text-sm">等待模型响应{waitingSeconds > 0 ? ` · ${waitingSeconds} 秒` : '…'}</span>
            </div>
            {waitingSeconds >= 10 && (
              <p className="text-xs">尚未收到回复内容，返回后会实时显示。你也可以停止本次请求。</p>
            )}
          </div>
        )}

        {/* Render actions in order — thinking, tools, delegations, confirmations */}
        {orderedActions.length > 0 && (
          <div className="space-y-2">
            {orderedActions.map((action, index) => {
              const isLast = index === orderedActions.length - 1;

              if (action.type === 'thinking') {
                let chunkContent: string;
                if (action.id === 'thinking-inline') {
                  chunkContent = inlineThinking;
                } else {
                  const chunk = streaming.thinkingChunks.find(c => c.id === action.id);
                  if (!chunk) return null;
                  chunkContent = chunk.content;
                }

                // Current thinking expands automatically; completed thinking defaults to collapsed.
                const isCurrentlyStreaming = isLast && streaming.isStreaming && !hasFinalText;
                const visibilityKey = `${action.id}:${isCurrentlyStreaming ? 'streaming' : 'complete'}`;
                const isOpen = thinkingVisibility[visibilityKey] ?? isCurrentlyStreaming;

                return (
                  <Collapse
                    key={action.id}
                    ghost
                    activeKey={isOpen ? ['1'] : []}
                    onChange={(keys) => {
                      setThinkingVisibility((prev) => ({
                        ...prev,
                        [visibilityKey]: keys.includes('1'),
                      }));
                    }}
                    items={[
                      {
                        key: '1',
                        label: (
                          <span className="flex items-center gap-2 text-sm text-muted-foreground">
                            <BulbOutlined />
                            {isCurrentlyStreaming ? '思考中...' : '推理过程'}
                          </span>
                        ),
                        children: (
                          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                            {chunkContent}
                          </p>
                        ),
                      },
                    ]}
                  />
                );
              }

              if (action.type === 'tool') {
                const toolCall = visibleToolCalls.find(tc => tc.id === action.id);
                return toolCall ? (
                  <ToolCallCard
                    key={action.id}
                    toolCall={toolCall}
                    fileChange={resolveToolFileChange(toolCall, streaming.fileChanges, workspaceId)}
                  />
                ) : null;
              }

              if (action.type === 'delegation') {
                const delegation = streaming.delegations.find(d => d.delegation_id === action.id);
                return delegation ? <DelegationCard key={action.id} delegation={delegation} /> : null;
              }

              if (action.type === 'confirmation') {
                const confirmation = streaming.confirmations.find(c => c.confirmation_id === action.id);
                if (!confirmation) return null;
                const resolved = streaming.confirmationsResolved[action.id];
                return (
                  <ConfirmationCard
                    key={action.id}
                    confirmationId={confirmation.confirmation_id}
                    question={confirmation.question}
                    mode={confirmation.mode}
                    options={confirmation.options}
                    tableSchema={confirmation.table_schema}
                    context={confirmation.context}
                    resolved={resolved}
                  />
                );
              }

              return null;
            })}
          </div>
        )}

        <FileChangeList files={streaming.fileChanges} />

        {/* Final text (rendered as Markdown) */}
        {hasFinalText && (
          <div className={`rounded-2xl bg-muted text-foreground ${compact ? 'px-3 py-2' : 'px-4 py-2.5'}`}>
            <div className="prose prose-sm max-w-none dark:prose-invert">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  code: ({ node, className, children, ...props }) => {
                    const match = /language-(\w+)/.exec(className || '');
                    const codeString = String(children).replace(/\n$/, '');
                    return match ? (
                      <CodeBlock language={match[1]} code={codeString} />
                    ) : (
                      <code className={className} {...props}>
                        {children}
                      </code>
                    );
                  },
                }}
              >
                {cleanFinalText}
              </ReactMarkdown>
            </div>
          </div>
        )}

        {/* Waiting for next step after tool execution */}
        {waitingForNextStep && (
          <div className="flex items-center gap-2 text-muted-foreground py-1">
            <LoadingOutlined spin />
            <span className="text-sm">正在处理工具结果...</span>
          </div>
        )}
      </div>
    </div>
  );
}
