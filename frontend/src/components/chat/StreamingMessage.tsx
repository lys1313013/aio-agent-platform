import { useState } from 'react';
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

interface Props {
  streaming: StreamingState;
}

export default function StreamingMessage({ streaming }: Props) {
  const [openThinkings, setOpenThinkings] = useState<Set<string>>(new Set());

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
  const showLoading = streaming.isStreaming && !hasThinking && !hasFinalText && !hasToolCalls && !hasDelegations && !hasConfirmations;

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
    <div className="flex gap-3">
      {/* Avatar */}
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-muted">
        <span className="text-sm font-medium text-muted-foreground">AI</span>
      </div>

      {/* Content */}
      <div className="flex-1 space-y-3">
        {/* Loading indicator — no content yet */}
        {showLoading && (
          <div className="flex items-center gap-2 text-muted-foreground py-1">
            <LoadingOutlined spin />
            <span className="text-sm">正在思考...</span>
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

                // Show open when this is the last action, still streaming, and no text yet
                const isCurrentlyStreaming = isLast && streaming.isStreaming && !hasFinalText;
                const isOpen = isCurrentlyStreaming || openThinkings.has(action.id);

                return (
                  <Collapse
                    key={action.id}
                    ghost
                    activeKey={isOpen ? ['1'] : []}
                    onChange={() => {
                      setOpenThinkings((prev) => {
                        const next = new Set(prev);
                        if (next.has(action.id)) {
                          next.delete(action.id);
                        } else {
                          next.add(action.id);
                        }
                        return next;
                      });
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
                return toolCall ? <ToolCallCard key={action.id} toolCall={toolCall} /> : null;
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

        {/* Final text (rendered as Markdown) */}
        {hasFinalText && (
          <div className="rounded-2xl bg-muted px-4 py-2.5 text-foreground">
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
