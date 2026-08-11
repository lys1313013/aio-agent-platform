import { useState, type MouseEvent } from 'react';
import {
  CodeOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  RightOutlined,
  DownOutlined,
  BulbOutlined,
  SearchOutlined,
  QuestionCircleOutlined,
  ClockCircleOutlined,
  EditOutlined,
  MessageOutlined,
  ReadOutlined,
  GlobalOutlined,
  ExportOutlined,
} from '@ant-design/icons';
import { App, Tag } from 'antd';
import type { ToolCallInfo } from '@/lib/types';
import { webpagesApi } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useWebpagePreviewStore } from '@/stores/webpagePreviewStore';

interface Props {
  toolCall: ToolCallInfo;
}

const LAYER_LABELS: Record<string, string> = {
  L1: '常驻上下文',
  L2: '长期记忆',
  L3: '情景记忆',
};

const TOOL_LABELS: Record<string, string> = {
  run_shell: 'Shell 命令',
  run_code: '运行代码',
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  list_directory: '列出目录',
  memory_read: '读取记忆',
  memory_write: '写入记忆',
  search_skills: '搜索技能',
  view_skill: '查看技能',
  create_skill: '创建技能',
  deploy_skill_files: '部署技能文件',
  report_skill_result: '上报技能结果',
  delegate_task: '委派任务',
  AskUserQuestion: '询问用户',
  knowledge_retrieval: '知识库检索',
  file_info: '文件信息',
  file_grep: '文件检索',
  file_query: '文件查询',
  read_pdf: '读取 PDF',
  update_user_portrait: '更新用户画像',
  create_webpage: '创建网页',
};

const LAYER_COLORS: Record<string, string> = {
  L1: 'blue',
  L2: 'purple',
  L3: 'green',
};

/** Custom renderer for memory_write tool calls */
function MemoryWriteCard({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);
  const content = toolCall.arguments.content as string | undefined;
  const layer = toolCall.arguments.layer as string | undefined;
  const tags = toolCall.arguments.tags as string[] | undefined;
  const hasResult = !!toolCall.result;
  const isPending = !hasResult;
  const isSuccess = toolCall.result?.status === 'ok';
  const isError = toolCall.result?.status === 'err';

  return (
    <div className="rounded-lg border border-blue-200 dark:border-blue-800/50 overflow-hidden bg-blue-50/50 dark:bg-blue-950/20">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-blue-100/50 dark:hover:bg-blue-900/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <BulbOutlined className="text-blue-500 flex-shrink-0" />
          <span className="text-sm font-medium text-blue-900 dark:text-blue-100">已记住</span>
          {layer && (
            <Tag color={LAYER_COLORS[layer]} className="text-xs">
              {LAYER_LABELS[layer] || layer}
            </Tag>
          )}
        </div>
        <div className="flex-shrink-0 ml-2 flex items-center gap-2">
          {isPending && <LoadingOutlined className="text-blue-400" />}
          {isSuccess && <CheckCircleOutlined className="text-green-500" />}
          {isError && <CloseCircleOutlined className="text-red-500" />}
          <span className="text-muted-foreground text-xs flex-shrink-0">
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
        </div>
      </div>

      {/* Content preview (always visible) */}
      {content && (
        <div className="px-3 pb-2">
          <p className="text-sm text-foreground leading-relaxed">{content}</p>
          {tags && tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {tags.map((tag) => (
                <Tag key={tag} className="text-xs" bordered={false}>
                  {tag}
                </Tag>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Expanded: raw error if failed */}
      {expanded && isError && toolCall.result?.preview && (
        <div className="px-3 pb-3 border-t border-blue-200 dark:border-blue-800/50">
          <pre className="text-xs p-2 mt-2 rounded bg-red-50 dark:bg-red-950/20 text-red-900 dark:text-red-100 whitespace-pre-wrap break-all">
            {toolCall.result.preview}
          </pre>
        </div>
      )}
    </div>
  );
}

/** Custom renderer for memory_read tool calls */
function MemoryReadCard({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);
  const query = toolCall.arguments.query as string | undefined;
  const hasResult = !!toolCall.result;
  const isPending = !hasResult;
  const isSuccess = toolCall.result?.status === 'ok';
  const isError = toolCall.result?.status === 'err';

  // Parse result count from output like "Found N relevant memories"
  let resultCount: number | null = null;
  if (isSuccess && toolCall.result?.preview) {
    const match = toolCall.result.preview.match(/Found (\d+)/);
    if (match) resultCount = parseInt(match[1]);
  }

  return (
    <div className="rounded-lg border border-purple-200 dark:border-purple-800/50 overflow-hidden bg-purple-50/50 dark:bg-purple-950/20">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-purple-100/50 dark:hover:bg-purple-900/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <SearchOutlined className="text-purple-500 flex-shrink-0" />
          <span className="text-sm font-medium text-purple-900 dark:text-purple-100">
            检索记忆
          </span>
          {resultCount !== null && (
            <Tag className="text-xs">
              {resultCount === 0 ? '无匹配' : `${resultCount} 条相关`}
            </Tag>
          )}
        </div>
        <div className="flex-shrink-0 ml-2 flex items-center gap-2">
          {isPending && <LoadingOutlined className="text-purple-400" />}
          {isSuccess && <CheckCircleOutlined className="text-green-500" />}
          {isError && <CloseCircleOutlined className="text-red-500" />}
          <span className="text-muted-foreground text-xs flex-shrink-0">
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
        </div>
      </div>

      {/* Query (always visible) */}
      {query && (
        <div className="px-3 pb-2">
          <p className="text-sm text-foreground italic">"{query}"</p>
        </div>
      )}

      {/* Expanded: show full result or error */}
      {expanded && hasResult && (
        <div className="px-3 pb-3 border-t border-purple-200 dark:border-purple-800/50">
          <pre
            className={cn(
              'text-xs p-2 mt-2 rounded max-h-60 whitespace-pre-wrap break-all',
              isError
                ? 'bg-red-50 dark:bg-red-950/20 text-red-900 dark:text-red-100'
                : 'bg-green-50 dark:bg-green-950/20',
            )}
          >
            {toolCall.result?.preview || '无输出'}
          </pre>
        </div>
      )}
    </div>
  );
}

/** Custom renderer for AskUserQuestion tool calls */
function AskUserQuestionCard({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);

  const question = toolCall.arguments.question as string | undefined;
  const mode = toolCall.arguments.mode as string | undefined;
  const options = (toolCall.arguments.options as Array<{
    id: string;
    label: string;
    description?: string;
  }>) || [];

  const isSuccess = toolCall.result?.status === 'ok';

  // Parse the output to extract user's selection
  const output = toolCall.result?.preview || '';
  let selectedLabels: string[] = [];
  let userAnswer = '';
  let resolvedStatus: 'answered' | 'rejected' | 'modified' | 'timeout' | 'waiting' = 'waiting';

  if (isSuccess && output) {
    if (output.startsWith('User selected: ')) {
      const raw = output.replace('User selected: ', '');
      const noteMatch = raw.match(/^(.+?) \(with note: (.+)\)$/);
      if (noteMatch) {
        selectedLabels = noteMatch[1].split(', ');
        userAnswer = noteMatch[2];
      } else {
        selectedLabels = raw.split(', ');
      }
      resolvedStatus = 'answered';
    } else if (output.startsWith('User responded: ')) {
      userAnswer = output.replace('User responded: ', '');
      resolvedStatus = 'answered';
    } else if (output.startsWith('User requested modifications: ')) {
      userAnswer = output.replace('User requested modifications: ', '');
      resolvedStatus = 'modified';
    } else if (output.startsWith('User rejected')) {
      const noteMatch = output.match(/and noted: (.+)$/);
      if (noteMatch) userAnswer = noteMatch[1];
      resolvedStatus = 'rejected';
    } else if (output.startsWith('用户暂未响应')) {
      resolvedStatus = 'timeout';
    } else if (output.startsWith('User did not select')) {
      resolvedStatus = 'rejected';
    }
  }

  const modeLabels: Record<string, string> = {
    single_select: '单选',
    multi_select: '多选',
    free_input: '自由输入',
    approve: '审批',
  };

  const statusConfig: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    waiting: {
      icon: <LoadingOutlined className="text-blue-400" />,
      color: 'processing',
      label: '等待回答',
    },
    answered: {
      icon: <CheckCircleOutlined className="text-green-500" />,
      color: 'success',
      label: '已回答',
    },
    rejected: {
      icon: <CloseCircleOutlined className="text-red-500" />,
      color: 'error',
      label: '已拒绝',
    },
    modified: {
      icon: <EditOutlined className="text-blue-500" />,
      color: 'processing',
      label: '已修改',
    },
    timeout: {
      icon: <ClockCircleOutlined className="text-orange-500" />,
      color: 'warning',
      label: '已超时',
    },
  };

  const status = statusConfig[resolvedStatus];

  return (
    <div className="rounded-lg border border-indigo-200 dark:border-indigo-800/50 overflow-hidden bg-indigo-50/50 dark:bg-indigo-950/20">
      {/* Header */}
      <div
        className="flex items-start justify-between gap-2 px-3 py-2 cursor-pointer hover:bg-indigo-100/50 dark:hover:bg-indigo-900/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <QuestionCircleOutlined className="text-indigo-500 flex-shrink-0 mt-0.5" />
          <span className="text-sm font-medium text-indigo-900 dark:text-indigo-100 break-words min-w-0">
            {question || '向用户提问'}
          </span>
          {mode && (
            <Tag className="text-xs flex-shrink-0 mt-0.5">
              {modeLabels[mode] || mode}
            </Tag>
          )}
        </div>
        <div className="flex-shrink-0 ml-2 flex items-center gap-2 mt-0.5">
          {status.icon}
          <span className="text-muted-foreground text-xs flex-shrink-0">
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
        </div>
      </div>

      {/* Answer summary (always visible when resolved) */}
      {resolvedStatus === 'answered' && selectedLabels.length > 0 && (
        <div className="px-3 pb-2">
          <div className="flex flex-wrap gap-1.5">
            {selectedLabels.map((label) => (
              <Tag key={label} color="blue" className="text-xs">
                {label}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {resolvedStatus === 'answered' && userAnswer && (
        <div className="px-3 pb-2">
          <div className="flex items-start gap-1.5 text-sm text-foreground bg-white/50 dark:bg-black/20 rounded p-2">
            <MessageOutlined className="text-indigo-400 mt-0.5 flex-shrink-0" />
            <span className="italic line-clamp-2">{userAnswer}</span>
          </div>
        </div>
      )}

      {resolvedStatus === 'modified' && userAnswer && (
        <div className="px-3 pb-2">
          <div className="flex items-start gap-1.5 text-sm text-foreground bg-amber-50/50 dark:bg-amber-950/20 rounded p-2 border border-amber-200 dark:border-amber-800/50">
            <EditOutlined className="text-amber-500 mt-0.5 flex-shrink-0" />
            <span className="italic">{userAnswer}</span>
          </div>
        </div>
      )}

      {resolvedStatus === 'rejected' && (
        <div className="px-3 pb-2">
          <span className="text-xs text-muted-foreground">用户跳过了此问题</span>
          {userAnswer && (
            <div className="flex items-start gap-1.5 text-sm text-foreground bg-red-50/50 dark:bg-red-950/20 rounded p-2 mt-1.5">
              <MessageOutlined className="text-red-400 mt-0.5 flex-shrink-0" />
              <span className="italic">{userAnswer}</span>
            </div>
          )}
        </div>
      )}

      {resolvedStatus === 'timeout' && (
        <div className="px-3 pb-2">
          <span className="text-xs text-muted-foreground">用户未在限定时间内回答</span>
        </div>
      )}

      {/* Expanded: show all options for context */}
      {expanded && options.length > 0 && (
        <div className="px-3 pb-3 border-t border-indigo-200 dark:border-indigo-800/50 pt-2 space-y-1">
          <div className="text-xs font-medium text-muted-foreground mb-1">可选方案</div>
          {options.map((opt) => {
            const isSelected = selectedLabels.includes(opt.label);
            return (
              <div
                key={opt.id}
                className={cn(
                  'text-xs px-2 py-1.5 rounded',
                  isSelected
                    ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-900 dark:text-blue-100 font-medium'
                    : 'bg-muted/30 text-muted-foreground',
                )}
              >
                {isSelected && <CheckCircleOutlined className="mr-1 text-blue-500" />}
                {opt.label}
                {opt.description && (
                  <span className="text-muted-foreground font-normal ml-1">
                    — {opt.description}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface KnowledgeChunk {
  index: number;
  kbName: string;
  title: string;
  score: number;
  content: string;
}

function parseKnowledgeChunks(preview: string): KnowledgeChunk[] {
  if (!preview) return [];
  const chunks: KnowledgeChunk[] = [];
  const lines = preview.split('\n');
  let i = 0;
  while (i < lines.length && !/^\d+\./.test(lines[i])) i++;
  while (i < lines.length) {
    const headerMatch = lines[i].match(
      /^(\d+)\.\s+\[(.+?)\/(.+?)\]\s+\(score:\s+([\d.]+)\)/,
    );
    if (!headerMatch) {
      i++;
      continue;
    }
    const contentLines: string[] = [];
    i++;
    while (i < lines.length && lines[i].startsWith('   ')) {
      contentLines.push(lines[i].replace(/^   /, ''));
      i++;
    }
    chunks.push({
      index: parseInt(headerMatch[1]),
      kbName: headerMatch[2].trim(),
      title: headerMatch[3].trim(),
      score: parseFloat(headerMatch[4]),
      content: contentLines.join('\n').trim(),
    });
  }
  return chunks;
}

/** Custom renderer for knowledge_retrieval tool calls */
function KnowledgeRetrievalCard({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);
  const query = toolCall.arguments.query as string | undefined;
  const hasResult = !!toolCall.result;
  const isPending = !hasResult;
  const isSuccess = toolCall.result?.status === 'ok';
  const isError = toolCall.result?.status === 'err';
  const chunks = parseKnowledgeChunks(toolCall.result?.preview || '');

  return (
    <div className="rounded-lg border border-border overflow-hidden bg-card">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2.5 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-muted-foreground text-xs flex-shrink-0">
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
          <ReadOutlined className="text-primary flex-shrink-0" />
          <span className="text-sm font-medium truncate">知识库检索</span>
        </div>
        <div className="flex-shrink-0 ml-2">
          {isPending && (
            <Tag icon={<LoadingOutlined />} color="processing">
              执行中
            </Tag>
          )}
          {isSuccess && (
            <Tag icon={<CheckCircleOutlined />} color="success">
              成功
            </Tag>
          )}
          {isError && (
            <Tag icon={<CloseCircleOutlined />} color="error">
              失败
            </Tag>
          )}
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-border">
          {/* Query */}
          {query && (
            <div className="pt-2">
              <div className="text-xs font-medium text-muted-foreground mb-1">检索内容</div>
              <div className="text-sm text-foreground bg-muted p-2 rounded">{query}</div>
            </div>
          )}

          {/* Result chunks */}
          {hasResult && chunks.length > 0 && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                检索结果 ({chunks.length})
              </div>
              <div className="space-y-1.5">
                {chunks.map((chunk) => (
                  <div
                    key={chunk.index}
                    className="bg-muted/50 rounded p-2 text-xs leading-relaxed"
                  >
                    <div className="flex items-center gap-1.5 mb-1 text-muted-foreground">
                      <span className="font-mono">{chunk.index}.</span>
                      <span>{chunk.kbName}</span>
                      <span>/</span>
                      <span className="font-medium text-foreground">{chunk.title}</span>
                      <span className="font-mono ml-auto flex-shrink-0">
                        {(chunk.score * 100).toFixed(0)}%
                      </span>
                    </div>
                    <pre className="whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                      {chunk.content}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Error */}
          {hasResult && isError && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">执行结果</div>
              <pre className="text-xs p-2 rounded bg-red-50 dark:bg-red-950/20 text-red-900 dark:text-red-100 whitespace-pre-wrap break-all max-h-60">
                {toolCall.result?.preview || '无输出'}
              </pre>
            </div>
          )}

          {/* No result */}
          {hasResult && isSuccess && chunks.length === 0 && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">执行结果</div>
              <div className="text-xs text-muted-foreground bg-muted/50 rounded p-2">
                {toolCall.result?.preview || '无输出'}
              </div>
            </div>
          )}

          {/* Loading */}
          {isPending && (
            <div className="flex items-center gap-2 text-muted-foreground py-1">
              <LoadingOutlined spin className="text-xs" />
              <span className="text-xs">等待执行结果...</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Default renderer for generic tool calls */
function DefaultToolCard({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);

  const hasResult = !!toolCall.result;
  const isPending = !hasResult;
  const isSuccess = toolCall.result?.status === 'ok';
  const isError = toolCall.result?.status === 'err';

  const toolLabel = TOOL_LABELS[toolCall.name] || toolCall.name;

  return (
    <div className="rounded-lg border border-border overflow-hidden bg-card">
      {/* Header — always visible */}
      <div
        className="flex items-center justify-between px-3 py-2.5 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-muted-foreground text-xs flex-shrink-0">
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
          <CodeOutlined className="text-primary flex-shrink-0" />
          <span className="text-sm font-medium truncate" title={toolLabel}>
            {toolLabel}
          </span>
        </div>

        <div className="flex-shrink-0 ml-2">
          {isPending && (
            <Tag icon={<LoadingOutlined />} color="processing">
              执行中
            </Tag>
          )}
          {isSuccess && (
            <Tag icon={<CheckCircleOutlined />} color="success">
              成功
            </Tag>
          )}
          {isError && (
            <Tag icon={<CloseCircleOutlined />} color="error">
              失败
            </Tag>
          )}
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-border">
          {/* Arguments */}
          {Object.keys(toolCall.arguments).length > 0 && (
            <div className="pt-2">
              <div className="text-xs font-medium text-muted-foreground mb-1">输入参数</div>
              <pre className="text-xs bg-muted p-2 rounded overflow-x-auto whitespace-pre-wrap break-all">
                {JSON.stringify(toolCall.arguments, null, 2)}
              </pre>
            </div>
          )}

          {/* Result */}
          {hasResult && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">执行结果</div>
              <pre
                className={cn(
                  'text-xs p-2 rounded overflow-x-auto max-h-80 whitespace-pre-wrap break-all',
                  isError
                    ? 'bg-red-50 dark:bg-red-950/20 text-red-900 dark:text-red-100'
                    : 'bg-green-50 dark:bg-green-950/20',
                )}
              >
                {toolCall.result?.preview || '无输出'}
              </pre>
            </div>
          )}

          {/* Loading bar for pending */}
          {isPending && (
            <div className="flex items-center gap-2 text-muted-foreground py-1">
              <LoadingOutlined spin className="text-xs" />
              <span className="text-xs">等待执行结果...</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface WebpageResult {
  page_id: string;
  title: string;
  url: string;
}

/** Parse the JSON payload returned by the create_webpage tool handler. */
function parseWebpageResult(preview?: string): WebpageResult | null {
  if (!preview) return null;
  try {
    const data = JSON.parse(preview);
    if (data && typeof data.page_id === 'string' && typeof data.url === 'string') {
      return {
        page_id: data.page_id,
        title: typeof data.title === 'string' ? data.title : '未命名网页',
        url: data.url,
      };
    }
  } catch {
    /* not a webpage result */
  }
  return null;
}

/** Custom renderer for create_webpage — clickable webpage artifact card. */
function CreateWebpageCard({ toolCall }: Props) {
  const { message } = App.useApp();
  const panelAvailable = useWebpagePreviewStore((s) => s.panelAvailable);
  const isPending = !toolCall.result;
  const isError = toolCall.result?.status === 'err';
  const data = parseWebpageResult(toolCall.result?.preview);
  const title =
    (toolCall.arguments.title as string) || data?.title || '未命名网页';

  /** 换取新鲜令牌 URL（创建时返回的令牌会过期） */
  const fetchFreshUrl = async (): Promise<string | null> => {
    if (!data) return null;
    try {
      const { url } = await webpagesApi.getAccess(data.page_id);
      return url;
    } catch {
      message.error('网页加载失败，可能已被删除');
      return null;
    }
  };

  const handlePreview = async () => {
    if (!data) return;
    // 无预览面板的场景（宠物浮窗）降级为新标签页打开
    if (!useWebpagePreviewStore.getState().panelAvailable) {
      const url = await fetchFreshUrl();
      if (url) window.open(url, '_blank', 'noopener');
      return;
    }
    useWebpagePreviewStore.getState().openPreview({ pageId: data.page_id, title });
  };

  const handleOpenNewTab = async (e: MouseEvent) => {
    e.stopPropagation();
    const url = await fetchFreshUrl();
    if (url) window.open(url, '_blank', 'noopener');
  };

  return (
    <div
      className={cn(
        'rounded-lg border overflow-hidden transition-colors',
        isError
          ? 'border-red-200 dark:border-red-800/50 bg-red-50/50 dark:bg-red-950/20'
          : 'border-purple-200 dark:border-purple-800/50 bg-purple-50/50 dark:bg-purple-950/20',
        data && 'cursor-pointer hover:bg-purple-100/50 dark:hover:bg-purple-900/20',
      )}
      onClick={() => void handlePreview()}
    >
      <div className="flex items-center justify-between px-3 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <GlobalOutlined className="text-purple-500 flex-shrink-0" />
          <span className="text-sm font-medium text-purple-900 dark:text-purple-100 truncate" title={title}>
            {title}
          </span>
          <Tag color="purple" className="text-xs flex-shrink-0">
            网页
          </Tag>
        </div>
        <div className="flex-shrink-0 ml-2 flex items-center gap-2">
          {isPending && <LoadingOutlined className="text-purple-400" />}
          {isError && <CloseCircleOutlined className="text-red-500" />}
          {data && (
            <span
              className="text-xs text-purple-500 hover:text-purple-700 flex items-center gap-1"
              onClick={(e) => void handleOpenNewTab(e)}
            >
              <ExportOutlined />
              新标签页
            </span>
          )}
        </div>
      </div>
      {isError && toolCall.result?.preview && (
        <div className="px-3 pb-3 border-t border-red-200 dark:border-red-800/50">
          <pre className="text-xs p-2 mt-2 rounded bg-red-50 dark:bg-red-950/20 text-red-900 dark:text-red-100 whitespace-pre-wrap break-all">
            {toolCall.result.preview}
          </pre>
        </div>
      )}
      {data && (
        <div className="px-3 pb-2 text-xs text-purple-400 dark:text-purple-300/70">
          {panelAvailable ? '点击卡片在右侧预览' : '点击卡片在新标签页打开'}
        </div>
      )}
    </div>
  );
}

export default function ToolCallCard({ toolCall }: Props) {
  // Delegate to custom renderers for specific tools
  if (toolCall.name === 'memory_write') {
    return <MemoryWriteCard toolCall={toolCall} />;
  }
  if (toolCall.name === 'memory_read') {
    return <MemoryReadCard toolCall={toolCall} />;
  }
  if (toolCall.name === 'AskUserQuestion') {
    return <AskUserQuestionCard toolCall={toolCall} />;
  }
  if (toolCall.name === 'knowledge_retrieval') {
    return <KnowledgeRetrievalCard toolCall={toolCall} />;
  }
  if (toolCall.name === 'create_webpage') {
    return <CreateWebpageCard toolCall={toolCall} />;
  }
  return <DefaultToolCard toolCall={toolCall} />;
}
