import { useState } from 'react';
import type { DelegationInfo } from '@/lib/types';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  BulbOutlined,
  ToolOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { Tag, Collapse } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { parseThinkBlocks } from '@/lib/utils';
import ToolCallCard from './ToolCallCard';
import { getAgentIcon, DEFAULT_ICON } from '@/lib/agent-icons';

interface Props {
  delegation: DelegationInfo;
}

export default function DelegationCard({ delegation }: Props) {
  const [expanded, setExpanded] = useState(delegation.status === 'running');

  const statusConfig = {
    running: {
      icon: <LoadingOutlined spin className="text-blue-500" />,
      color: 'processing' as const,
      label: '执行中',
    },
    completed: {
      icon: <CheckCircleOutlined className="text-green-500" />,
      color: 'success' as const,
      label: '完成',
    },
    failed: {
      icon: <CloseCircleOutlined className="text-red-500" />,
      color: 'error' as const,
      label: '失败',
    },
    timeout: {
      icon: <ExclamationCircleOutlined className="text-orange-500" />,
      color: 'warning' as const,
      label: '超时',
    },
  };

  const status = statusConfig[delegation.status];
  const icon = getAgentIcon(delegation.child_agent_icon || DEFAULT_ICON);

  const durationText = delegation.duration_ms
    ? `${(delegation.duration_ms / 1000).toFixed(1)}s`
    : null;

  const hasThinking = !!delegation.thinking;
  const hasToolCalls = (delegation.toolCalls?.length || 0) > 0;
  const hasResult = !!delegation.result;

  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 overflow-hidden">
      {/* Header — always visible */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <span className="text-base">{icon}</span>
          <span className="text-sm font-medium">
            委派给「{delegation.child_agent_name}」
          </span>
          <Tag
            icon={status.icon}
            color={status.color}
            className="ml-1"
            style={{ margin: 0 }}
          >
            {status.label}
          </Tag>
          {durationText && (
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <ClockCircleOutlined />
              {durationText}
            </span>
          )}
        </div>
        <span className="text-muted-foreground text-xs">
          {expanded ? <DownOutlined /> : <RightOutlined />}
        </span>
      </div>

      {/* Task summary — always visible */}
      <div className="px-3 pb-2 text-xs text-muted-foreground">
        {delegation.task.length > 120
          ? delegation.task.slice(0, 120) + '...'
          : delegation.task}
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-border/40 px-3 py-2 space-y-2">
          {/* Thinking */}
          {hasThinking && (
            <Collapse
              ghost
              size="small"
              defaultActiveKey={[]}
              items={[
                {
                  key: '1',
                  label: (
                    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <BulbOutlined />
                      推理过程
                    </span>
                  ),
                  children: (
                    <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                      {delegation.thinking}
                    </p>
                  ),
                },
              ]}
            />
          )}

          {/* Tool calls */}
          {hasToolCalls && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <ToolOutlined />
                <span>工具调用 ({delegation.toolCalls!.length})</span>
              </div>
              {delegation.toolCalls!.map((tc) => (
                <ToolCallCard key={tc.id} toolCall={tc} />
              ))}
            </div>
          )}

          {/* Result */}
          {hasResult && (() => {
            const { thinking: resultThinking, content: resultContent } = parseThinkBlocks(delegation.result!);
            const hasResultThinking = resultThinking.length > 0;
            const hasResultContent = resultContent.length > 0;

            return (
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground font-medium">结果</div>
                <div className="rounded-md bg-muted/60 px-3 py-2 text-xs space-y-2">
                  {hasResultThinking && (
                    <Collapse
                      ghost
                      size="small"
                      defaultActiveKey={[]}
                      items={[
                        {
                          key: 'result-thinking',
                          label: (
                            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                              <BulbOutlined />
                              推理过程
                            </span>
                          ),
                          children: (
                            <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                              {resultThinking}
                            </p>
                          ),
                        },
                      ]}
                    />
                  )}
                  {hasResultContent && (
                    <div className="prose prose-xs max-w-none dark:prose-invert text-foreground">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {resultContent}
                      </ReactMarkdown>
                    </div>
                  )}
                  {!hasResultThinking && !hasResultContent && (
                    <p className="whitespace-pre-wrap">{delegation.result}</p>
                  )}
                </div>
              </div>
            );
          })()}

          {/* Error */}
          {delegation.error && (
            <div className="rounded-md bg-red-50 dark:bg-red-900/20 px-3 py-2 text-xs text-red-600 dark:text-red-400">
              ⚠️ {delegation.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
