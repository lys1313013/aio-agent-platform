import { useState } from 'react';
import { ToolOutlined, CloseOutlined, SearchOutlined, PlusOutlined } from '@ant-design/icons';
import { Alert, Button, Checkbox, Tag, Typography, Divider, Modal } from 'antd';
import type { ToolInfo } from '@/lib/types';
import { useAgentConfig } from './context';
import { SaveButtonRow } from './SaveButtonRow';

const { Text } = Typography;

const TOOL_CATEGORY_LABELS: Record<string, string> = {
  sandbox: '沙箱执行',
  memory: '记忆',
  skills: '技能',
  multi_agent: '多智能体',
  interaction: '交互',
  knowledge: '知识库',
  file: '文件',
  remote: '远程工具',
  automation: '自动化',
  ui_automation: '页面自动化',
  other: '其他',
};

const TOOL_CATEGORY_ORDER = [
  'sandbox',
  'file',
  'knowledge',
  'memory',
  'skills',
  'multi_agent',
  'interaction',
  'automation',
  'ui_automation',
  'remote',
  'other',
];

export function ToolsPanel() {
  const { allTools, enabledTools, setEnabledTools, enabledMcpTools, savingSections, savedSections, saveSection } = useAgentConfig();
  const [toolModalOpen, setToolModalOpen] = useState(false);
  const [toolSearchQuery, setToolSearchQuery] = useState('');
  const [toolDetailModalOpen, setToolDetailModalOpen] = useState(false);
  const [selectedToolDetail, setSelectedToolDetail] = useState<ToolInfo | null>(null);
  // Draft selection edited inside the tool modal; committed to enabledTools only on confirm.
  const [toolDraft, setToolDraft] = useState<string[]>([]);

  // knowledge_retrieval and delegate_task are auto-injected by backend
  // when knowledge bases / child agents are bound;
  // hide them from the manual tool selection UI so users don't need to check them.
  // mcp tools have their own dedicated section, so exclude them here too.
  const allLocalTools = allTools.filter(
    (t) => t.category !== 'mcp' && t.name !== 'knowledge_retrieval' && t.name !== 'delegate_task',
  );

  // Filter tools for the modal by search query
  const q = toolSearchQuery.toLowerCase();
  const filteredTools = allLocalTools.filter((tool) => {
    if (!toolSearchQuery) return true;
    return (
      tool.name.toLowerCase().includes(q) ||
      tool.label.toLowerCase().includes(q) ||
      tool.description.toLowerCase().includes(q)
    );
  });

  // Group filtered tools by category, ordered by TOOL_CATEGORY_ORDER
  const toolsByCategory = filteredTools.reduce<Record<string, ToolInfo[]>>((acc, tool) => {
    const cat = tool.category || 'other';
    (acc[cat] ||= []).push(tool);
    return acc;
  }, {});
  const orderedCategories = Object.keys(toolsByCategory).sort((a, b) => {
    const ia = TOOL_CATEGORY_ORDER.indexOf(a);
    const ib = TOOL_CATEGORY_ORDER.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  return (
    <>
      <div className="agent-config-panel">
        <Text type="secondary" className="text-xs block mb-2">
          选择智能体可调用的工具，系统工具会自动注入。
        </Text>

        {enabledTools.length === 0 && (
          <Alert
            type="info"
            showIcon
            className="!mb-3 !text-xs"
            message="未勾选任何工具表示不限制白名单：该智能体可调用全部内置工具（含创建技能、修改技能）。勾选后仅能调用所选工具。"
          />
        )}

        {/* Selected tools display */}
        {enabledTools.length > 0 && (
          <>
            <div className="flex items-center justify-between mb-2">
              <Text type="secondary" className="text-xs font-semibold">
                已选择 ({enabledTools.length})
              </Text>
              <div className="flex items-center gap-2">
                <Button
                  size="small"
                  type="link"
                  className="!p-0 !text-xs"
                  onClick={() => setEnabledTools(allLocalTools.map((t) => t.name))}
                >
                  全选
                </Button>
                <Button
                  size="small"
                  type="link"
                  danger
                  className="!p-0 !text-xs"
                  onClick={() => setEnabledTools([])}
                >
                  清空
                </Button>
              </div>
            </div>
            <div className="space-y-1 mb-3 max-h-80 overflow-y-auto">
              {enabledTools.map((toolName) => {
                const tool = allLocalTools.find((t) => t.name === toolName);
                if (!tool) return null;
                return (
                  <div
                    key={tool.name}
                    className="flex items-center gap-2 p-2 rounded-lg border border-border/50 bg-muted/30 hover:bg-muted/50 transition-colors group"
                  >
                    <div
                      className="flex-1 min-w-0 cursor-pointer"
                      onClick={() => {
                        setSelectedToolDetail(tool);
                        setToolDetailModalOpen(true);
                      }}
                    >
                      <div className="flex items-center gap-1.5">
                        <Text className="text-xs font-medium">{tool.label}</Text>
                        {tool.permission_level === 'dangerous' && (
                          <Tag color="red" className="text-[10px] leading-none">危险</Tag>
                        )}
                        {tool.category === 'multi_agent' && (
                          <Tag color="cyan" className="text-[10px] leading-none">多智能体</Tag>
                        )}
                        {tool.category === 'remote' && (
                          <Tag color="purple" className="text-[10px] leading-none">远程</Tag>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={() => {
                        setEnabledTools((prev) => prev.filter((t) => t !== tool.name));
                      }}
                      className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100"
                    >
                      <CloseOutlined className="text-xs" />
                    </button>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {/* Add button */}
        <div className="mb-3">
          <Button
            block
            icon={<PlusOutlined />}
            onClick={() => {
              setToolDraft(enabledTools);
              setToolModalOpen(true);
            }}
            className="text-xs"
          >
            添加工具
          </Button>
        </div>

        <SaveButtonRow
          saving={!!savingSections['tools']}
          saved={!!savedSections['tools']}
          onSave={() => saveSection('tools', {
            enabled_tools: [...enabledTools, ...enabledMcpTools],
          })}
        />
      </div>

      {/* Tool selection modal */}
      <Modal
        title="选择工具"
        open={toolModalOpen}
        onCancel={() => {
          setToolModalOpen(false);
          setToolSearchQuery('');
        }}
        onOk={() => {
          setEnabledTools(toolDraft);
          setToolModalOpen(false);
          setToolSearchQuery('');
        }}
        okText="确定"
        cancelText="取消"
        width={1320}
        destroyOnHidden
        styles={{ body: { maxHeight: '80vh', overflowY: 'auto' } }}
      >
        <div className="relative mb-3">
          <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/60" />
          <input
            type="text"
            placeholder="搜索工具名称或描述..."
            value={toolSearchQuery}
            onChange={(e) => setToolSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
          />
        </div>

        <div className="space-y-3">
          {orderedCategories.map((cat) => {
            const catTools = toolsByCategory[cat];
            const catLabel = TOOL_CATEGORY_LABELS[cat] || cat;
            const catSelectedCount = catTools.filter((t) => toolDraft.includes(t.name)).length;
            const catAllSelected = catTools.length > 0 && catSelectedCount === catTools.length;
            return (
              <div key={cat}>
                <div className="sticky top-0 bg-background z-10 py-1.5 flex items-center justify-between">
                  <Text type="secondary" className="text-sm font-semibold">
                    {catLabel} ({catTools.length})
                    {catSelectedCount > 0 && (
                      <Text type="secondary" className="text-xs font-normal ml-1.5">
                        已选 {catSelectedCount}
                      </Text>
                    )}
                  </Text>
                  <Button
                    size="small"
                    type="link"
                    className="!p-0 !text-xs"
                    onClick={() => {
                      const names = catTools.map((t) => t.name);
                      if (catAllSelected) {
                        setToolDraft((prev) => prev.filter((n) => !names.includes(n)));
                      } else {
                        setToolDraft((prev) => [
                          ...prev,
                          ...names.filter((n) => !prev.includes(n)),
                        ]);
                      }
                    }}
                  >
                    {catAllSelected ? '取消全选' : '全选'}
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  {catTools.map((tool) => {
                    const isSelected = toolDraft.includes(tool.name);
                    return (
                      <div
                        key={tool.name}
                        className={`flex items-start gap-2 p-3 rounded-lg cursor-pointer border transition-colors ${isSelected
                          ? 'bg-primary/5 border-primary/30'
                          : 'border-border/50 hover:bg-muted/50'
                          }`}
                      >
                        <Checkbox
                          checked={isSelected}
                          className="mt-0.5"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (isSelected) {
                              setToolDraft((prev) => prev.filter((t) => t !== tool.name));
                            } else {
                              setToolDraft((prev) => [...prev, tool.name]);
                            }
                          }}
                        />
                        <div
                          className="flex-1 min-w-0"
                          onClick={() => {
                            setSelectedToolDetail(tool);
                            setToolDetailModalOpen(true);
                          }}
                        >
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <Text className="text-sm font-medium">{tool.label}</Text>
                            <Tag className="text-xs leading-none font-mono">{tool.name}</Tag>
                            {tool.permission_level === 'dangerous' && (
                              <Tag color="red" className="text-xs leading-none">危险</Tag>
                            )}
                            {tool.category === 'multi_agent' && (
                              <Tag color="cyan" className="text-xs leading-none">多智能体</Tag>
                            )}
                            {tool.category === 'remote' && (
                              <Tag color="purple" className="text-xs leading-none">远程</Tag>
                            )}
                            {tool.category === 'remote' && tool.method && (
                              <Tag color="blue" className="text-xs leading-none">{tool.method}</Tag>
                            )}
                          </div>
                          {tool.description && (
                            <Text type="secondary" className="text-xs block mt-1 line-clamp-2">
                              {tool.description}
                            </Text>
                          )}
                          <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                            {tool.timeout && (
                              <Text type="secondary" className="text-xs">
                                超时: {tool.timeout}s
                              </Text>
                            )}
                            {tool.requires_sandbox && (
                              <Tag color="orange" className="text-xs leading-none">需要沙箱</Tag>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}

          {/* No results */}
          {orderedCategories.length === 0 && (
            <div className="py-8 text-center">
              <ToolOutlined className="text-2xl text-muted-foreground/40 mb-2" />
              <Text type="secondary" className="text-xs block">
                {toolSearchQuery ? '未找到匹配的工具' : '暂无可用工具'}
              </Text>
            </div>
          )}
        </div>
      </Modal>

      {/* Tool detail modal */}
      <Modal
        title="工具详情"
        open={toolDetailModalOpen}
        onCancel={() => {
          setToolDetailModalOpen(false);
          setSelectedToolDetail(null);
        }}
        footer={null}
        width={800}
        destroyOnHidden
      >
        {selectedToolDetail && (
          <div className="space-y-4">
            {/* Basic info */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Text className="text-base font-semibold">{selectedToolDetail.label}</Text>
                <Tag className="font-mono">{selectedToolDetail.name}</Tag>
                {selectedToolDetail.permission_level === 'dangerous' && (
                  <Tag color="red">危险</Tag>
                )}
                {selectedToolDetail.category === 'multi_agent' && (
                  <Tag color="cyan">多智能体</Tag>
                )}
                {selectedToolDetail.category === 'remote' && (
                  <Tag color="purple">远程</Tag>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Text type="secondary" className="text-xs block mb-1">分类</Text>
                  <Tag className="text-xs">{selectedToolDetail.category}</Tag>
                </div>
                <div>
                  <Text type="secondary" className="text-xs block mb-1">权限级别</Text>
                  <Tag
                    color={selectedToolDetail.permission_level === 'dangerous' ? 'red' : 'default'}
                    className="text-xs"
                  >
                    {selectedToolDetail.permission_level}
                  </Tag>
                </div>
                {selectedToolDetail.timeout && (
                  <div>
                    <Text type="secondary" className="text-xs block mb-1">超时时间</Text>
                    <Text className="text-xs">{selectedToolDetail.timeout}s</Text>
                  </div>
                )}
                <div>
                  <Text type="secondary" className="text-xs block mb-1">需要沙箱</Text>
                  <Tag color={selectedToolDetail.requires_sandbox ? 'orange' : 'default'} className="text-xs">
                    {selectedToolDetail.requires_sandbox ? '是' : '否'}
                  </Tag>
                </div>
                {selectedToolDetail.method && (
                  <div>
                    <Text type="secondary" className="text-xs block mb-1">HTTP 方法</Text>
                    <Tag color="blue" className="text-xs">{selectedToolDetail.method}</Tag>
                  </div>
                )}
              </div>
            </div>

            <Divider className="!my-3" />

            {/* Description */}
            <div>
              <Text type="secondary" className="text-xs block mb-2">工具说明</Text>
              <div className="p-3 rounded-lg bg-muted/30 border border-border/50">
                <Text className="text-xs whitespace-pre-wrap">
                  {selectedToolDetail.description || '暂无说明'}
                </Text>
              </div>
            </div>

            {/* Function signature with parameters */}
            {selectedToolDetail.parameters && (
              <div>
                <Text type="secondary" className="text-xs block mb-2">函数签名</Text>
                <div className="p-3 rounded-lg bg-muted/30 border border-border/50">
                  <pre className="text-xs font-mono whitespace-pre-wrap overflow-x-auto">
                    {(() => {
                      const params = selectedToolDetail.parameters as Record<string, unknown>;
                      const properties = (params.properties || {}) as Record<string, Record<string, unknown>>;
                      const required = (params.required || []) as string[];

                      const paramsList = Object.entries(properties).map(([key, value]) => {
                        const type = value.type as string || 'any';
                        const isRequired = required.includes(key);
                        const description = value.description as string || '';
                        return `  ${key}${isRequired ? '' : '?'}: ${type}  // ${description}`;
                      });

                      return `${selectedToolDetail.name}(\n${paramsList.join('\n')}\n)`;
                    })()}
                  </pre>
                </div>
              </div>
            )}

            {/* Parameters detail */}
            {selectedToolDetail.parameters && (
              <div>
                <Text type="secondary" className="text-xs block mb-2">参数详情</Text>
                <div className="space-y-2">
                  {(() => {
                    const params = selectedToolDetail.parameters as Record<string, unknown>;
                    const properties = (params.properties || {}) as Record<string, Record<string, unknown>>;
                    const required = (params.required || []) as string[];

                    return Object.entries(properties).map(([key, value]) => {
                      const type = value.type as string || 'any';
                      const isRequired = required.includes(key);
                      const description = value.description as string || '';
                      const enumValues = value.enum as string[] | undefined;
                      const defaultValue = value.default;

                      return (
                        <div key={key} className="p-2.5 rounded-lg bg-muted/20 border border-border/30">
                          <div className="flex items-center gap-2 mb-1">
                            <Text className="text-xs font-mono font-semibold">{key}</Text>
                            <Tag className="text-[10px] leading-none">{type}</Tag>
                            {isRequired ? (
                              <Tag color="red" className="text-[10px] leading-none">必填</Tag>
                            ) : (
                              <Tag className="text-[10px] leading-none">可选</Tag>
                            )}
                          </div>
                          {description && (
                            <Text type="secondary" className="text-xs block mb-1">
                              {description}
                            </Text>
                          )}
                          {enumValues && (
                            <div className="mt-1">
                              <Text type="secondary" className="text-[10px]">
                                可选值: {enumValues.join(', ')}
                              </Text>
                            </div>
                          )}
                          {defaultValue !== undefined && (
                            <div className="mt-1">
                              <Text type="secondary" className="text-[10px]">
                                默认值: {String(defaultValue)}
                              </Text>
                            </div>
                          )}
                        </div>
                      );
                    });
                  })()}
                </div>
              </div>
            )}

            {/* Usage note */}
            {selectedToolDetail.permission_level === 'dangerous' && (
              <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
                <div className="flex items-start gap-2">
                  <span className="text-red-500">⚠️</span>
                  <div>
                    <Text className="text-xs font-medium text-red-600 block mb-1">危险工具提示</Text>
                    <Text type="secondary" className="text-xs">
                      此工具具有较高权限，可能对系统产生重大影响。使用时请谨慎评估风险。
                    </Text>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
