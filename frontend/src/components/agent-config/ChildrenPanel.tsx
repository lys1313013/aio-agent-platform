import { useState } from 'react';
import { CloseOutlined, SearchOutlined, PlusOutlined, ApartmentOutlined } from '@ant-design/icons';
import { Button, Checkbox, InputNumber, Tag, Typography, Modal } from 'antd';
import type { Agent } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';
import { useAgentConfig } from './context';
import { SaveButtonRow } from './SaveButtonRow';

const { Text } = Typography;

export function ChildrenPanel() {
  const { agentId, allAgents, allTools, selectedChildIds, setSelectedChildIds, childMaxIterations, setChildMaxIterations, savingSections, savedSections, saveChildrenSection } = useAgentConfig();
  const [childrenModalOpen, setChildrenModalOpen] = useState(false);
  const [childrenSearchQuery, setChildrenSearchQuery] = useState('');
  const [childrenDetailModalOpen, setChildrenDetailModalOpen] = useState(false);
  const [selectedChildDetail, setSelectedChildDetail] = useState<Agent | null>(null);
  // Draft selection edited inside the modal; committed to selectedChildIds only on confirm.
  const [childrenDraft, setChildrenDraft] = useState<string[]>([]);
  return (
    <>

      <div className="agent-config-panel">
        <Text type="secondary" className="text-xs block mb-3">
          添加可接收任务委派的子智能体。
        </Text>

        {/* Button to open child agent selection modal */}
        <Button
          block
          icon={<PlusOutlined />}
          onClick={() => {
            setChildrenDraft([...selectedChildIds]);
            setChildrenSearchQuery('');
            setChildrenModalOpen(true);
          }}
        >
          添加子智能体
        </Button>

        {/* Child agent cards */}
        {selectedChildIds.length > 0 ? (
          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between">
              <Text className="text-xs font-medium">
                已配置 {selectedChildIds.length} 个子智能体
              </Text>
              <Button
                size="small"
                type="link"
                danger
                className="!p-0 !text-xs !h-auto"
                onClick={() => setSelectedChildIds([])}
              >
                全部移除
              </Button>
            </div>
            {selectedChildIds.map((cid) => {
              const child = allAgents.find((a) => a.id === cid);
              if (!child) return null;
              return (
                <div
                  key={cid}
                  className="flex items-start gap-2 p-2 rounded-lg bg-muted/50 border border-border/50 group"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm flex-shrink-0">
                        {getAgentIcon(child.icon)}
                      </span>
                      <Text className="text-xs font-medium truncate">{child.name}</Text>
                      <Tag
                        color={child.is_active ? 'green' : 'default'}
                        className="text-[10px] leading-none flex-shrink-0"
                      >
                        {child.is_active ? '启用' : '禁用'}
                      </Tag>
                    </div>
                    {child.description && (
                      <Text type="secondary" className="text-xs block mt-0.5 line-clamp-2">
                        {child.description}
                      </Text>
                    )}
                    <div className="flex items-center gap-1.5 mt-1">
                      <Tag className="text-[10px] leading-none">
                        {child.model_name || '全局默认'}
                      </Tag>
                      <Tag className="text-[10px] leading-none">
                        {(() => {
                          const builtinNames = new Set(
                            allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                          );
                          return (child.enabled_tools || []).filter((t) => builtinNames.has(t)).length;
                        })()} 个工具
                      </Tag>
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                      <Text className="text-[11px] text-muted-foreground flex-shrink-0">最大轮次</Text>
                      <InputNumber
                        min={1}
                        max={500}
                        step={1}
                        precision={0}
                        size="small"
                        className="w-full"
                        placeholder="默认"
                        value={childMaxIterations[cid] ?? undefined}
                        onChange={(v) =>
                          setChildMaxIterations((prev) => ({ ...prev, [cid]: v ?? null }))
                        }
                      />
                    </div>
                  </div>
                  <button
                    onClick={() =>
                      setSelectedChildIds((prev) => prev.filter((id) => id !== cid))
                    }
                    className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <CloseOutlined className="text-xs" />
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="mt-3 py-4 text-center">
            <ApartmentOutlined className="text-lg text-muted-foreground/40" />
            <Text type="secondary" className="text-xs block mt-1">暂未配置子智能体</Text>
            <Text type="secondary" className="text-xs block mt-0.5">
              添加后，可向子智能体委派任务
            </Text>
          </div>
        )}

        <SaveButtonRow
          saving={!!savingSections['children']}
          saved={!!savedSections['children']}
          onSave={saveChildrenSection}
        />
      </div>
      {/* Children (sub-agents) selection modal */}
      <Modal
        title="选择子智能体"
        open={childrenModalOpen}
        onCancel={() => {
          setChildrenModalOpen(false);
          setChildrenSearchQuery('');
        }}
        onOk={() => {
          setSelectedChildIds(childrenDraft);
          setChildMaxIterations((prev) => {
            const next = { ...prev };
            Object.keys(next).forEach((id) => {
              if (!childrenDraft.includes(id)) delete next[id];
            });
            return next;
          });
          setChildrenModalOpen(false);
          setChildrenSearchQuery('');
        }}
        okText="确定"
        cancelText="取消"
        width={1000}
        destroyOnHidden
        styles={{ body: { maxHeight: '75vh', overflowY: 'auto' } }}
      >
        <div className="relative mb-3">
          <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/60" />
          <input
            type="text"
            placeholder="搜索智能体名称或描述..."
            value={childrenSearchQuery}
            onChange={(e) => setChildrenSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
          />
        </div>

        {(() => {
          const candidates = allAgents.filter((a) => {
            if (a.id === agentId) return false;
            if (a.parent_ids && a.parent_ids.includes(agentId)) return false;
            const q = childrenSearchQuery.toLowerCase();
            if (q) {
              const nameMatch = a.name.toLowerCase().includes(q);
              const descMatch = a.description?.toLowerCase().includes(q) ?? false;
              if (!nameMatch && !descMatch) return false;
            }
            return true;
          });

          if (candidates.length === 0) {
            return (
              <div className="py-8 text-center">
                <ApartmentOutlined className="text-2xl text-muted-foreground/40 mb-2" />
                <Text type="secondary" className="text-xs block">
                  {childrenSearchQuery ? '未找到匹配的智能体' : '暂无可选的智能体'}
                </Text>
              </div>
            );
          }

          const selectedCount = candidates.filter((a) => childrenDraft.includes(a.id)).length;
          const allSelected = selectedCount === candidates.length;

          return (
            <div>
              <div className="sticky top-0 bg-background z-10 py-1.5 flex items-center justify-between">
                <Text type="secondary" className="text-sm font-semibold">
                  可选智能体 ({candidates.length})
                  {selectedCount > 0 && (
                    <Text type="secondary" className="text-xs font-normal ml-1.5">
                      已选 {selectedCount}
                    </Text>
                  )}
                </Text>
                <Button
                  size="small"
                  type="link"
                  className="!p-0 !text-xs"
                  onClick={() => {
                    if (allSelected) {
                      setChildrenDraft((prev) =>
                        prev.filter((id) => !candidates.find((a) => a.id === id)),
                      );
                    } else {
                      const ids = candidates.map((a) => a.id);
                      setChildrenDraft((prev) => [...prev, ...ids.filter((id) => !prev.includes(id))]);
                    }
                  }}
                >
                  {allSelected ? '取消全选' : '全选'}
                </Button>
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                {candidates.map((agent) => {
                  const isSelected = childrenDraft.includes(agent.id);
                  const builtinNames = new Set(
                    allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                  );
                  const toolCount = (agent.enabled_tools || []).filter((t) => builtinNames.has(t)).length;
                  return (
                    <div
                      key={agent.id}
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
                            setChildrenDraft((prev) => prev.filter((id) => id !== agent.id));
                          } else {
                            setChildrenDraft((prev) => [...prev, agent.id]);
                          }
                        }}
                      />
                      <div
                        className="flex-1 min-w-0"
                        onClick={() => {
                          setSelectedChildDetail(agent);
                          setChildrenDetailModalOpen(true);
                        }}
                      >
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-sm">{getAgentIcon(agent.icon)}</span>
                          <Text className="text-sm font-medium truncate">{agent.name}</Text>
                          <Tag
                            color={agent.is_active ? 'green' : 'default'}
                            className="text-[10px] leading-none"
                          >
                            {agent.is_active ? '启用' : '禁用'}
                          </Tag>
                        </div>
                        {agent.description && (
                          <Text type="secondary" className="text-xs block mt-1 line-clamp-2">
                            {agent.description}
                          </Text>
                        )}
                        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                          <Tag className="text-[10px] leading-none">
                            {agent.model_name || '全局默认'}
                          </Tag>
                          <Tag className="text-[10px] leading-none">
                            {toolCount} 个工具
                          </Tag>
                          {agent.knowledge_base_ids && agent.knowledge_base_ids.length > 0 && (
                            <Tag color="blue" className="text-[10px] leading-none">
                              {agent.knowledge_base_ids.length} 个知识库
                            </Tag>
                          )}
                          {agent.skill_ids && agent.skill_ids.length > 0 && (
                            <Tag color="purple" className="text-[10px] leading-none">
                              {agent.skill_ids.length} 个技能
                            </Tag>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}
      </Modal>

      {/* Child agent detail modal */}
      <Modal
        title="智能体详情"
        open={childrenDetailModalOpen}
        onCancel={() => {
          setChildrenDetailModalOpen(false);
          setSelectedChildDetail(null);
        }}
        footer={null}
        width={800}
        destroyOnHidden
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        {selectedChildDetail && (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <span className="text-2xl">{getAgentIcon(selectedChildDetail.icon)}</span>
              <div>
                <Text className="text-lg font-semibold block">{selectedChildDetail.name}</Text>
                <div className="flex items-center gap-2 mt-0.5">
                  <Tag
                    color={selectedChildDetail.is_active ? 'green' : 'default'}
                    className="text-xs leading-none"
                  >
                    {selectedChildDetail.is_active ? '启用' : '禁用'}
                  </Tag>
                  <Text type="secondary" className="text-xs">
                    {selectedChildDetail.model_name || '全局默认模型'}
                  </Text>
                </div>
              </div>
            </div>

            {selectedChildDetail.description && (
              <div>
                <Text type="secondary" className="text-xs font-medium block mb-1">描述</Text>
                <Text className="text-sm">{selectedChildDetail.description}</Text>
              </div>
            )}

            <div>
              <Text type="secondary" className="text-xs font-medium block mb-2">
                可用工具 ({(() => {
                  const names = new Set(
                    allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                  );
                  return (selectedChildDetail.enabled_tools || []).filter((t) => names.has(t)).length;
                })()})
              </Text>
              {selectedChildDetail.enabled_tools?.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {selectedChildDetail.enabled_tools.map((t) => (
                    <Tag key={t} className="text-xs">{t}</Tag>
                  ))}
                </div>
              ) : (
                <Text type="secondary" className="text-xs">未配置工具</Text>
              )}
            </div>

            <div>
              <Text type="secondary" className="text-xs font-medium block mb-2">
                知识库 ({(selectedChildDetail.knowledge_base_ids || []).length})
              </Text>
              {selectedChildDetail.knowledge_base_ids?.length > 0 ? (
                <Text className="text-xs">
                  已绑定 {selectedChildDetail.knowledge_base_ids.length} 个知识库
                </Text>
              ) : (
                <Text type="secondary" className="text-xs">未绑定知识库</Text>
              )}
            </div>

            <div>
              <Text type="secondary" className="text-xs font-medium block mb-2">
                技能 ({(selectedChildDetail.skill_ids || []).length})
              </Text>
              {selectedChildDetail.skill_ids?.length > 0 ? (
                <Text className="text-xs">
                  已绑定 {selectedChildDetail.skill_ids.length} 个技能
                </Text>
              ) : (
                <Text type="secondary" className="text-xs">未绑定技能</Text>
              )}
            </div>

            {selectedChildDetail.system_prompt && (
              <div>
                <Text type="secondary" className="text-xs font-medium block mb-1">系统提示词</Text>
                <div className="p-3 rounded-lg bg-muted/50 border border-border/50 max-h-48 overflow-y-auto">
                  <Text className="text-xs whitespace-pre-wrap">
                    {selectedChildDetail.system_prompt}
                  </Text>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>

    </>
  );
}
