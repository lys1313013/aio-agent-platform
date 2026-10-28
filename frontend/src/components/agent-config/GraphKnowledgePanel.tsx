import { ApartmentOutlined } from '@ant-design/icons';
import { Tag, Typography } from 'antd';
import { cn } from '@/lib/utils';
import { useAgentConfig } from './context';
import { SaveButtonRow } from './SaveButtonRow';

const { Text } = Typography;

export function GraphKnowledgePanel() {
  const { graphKnowledgeBases, selectedGraphKnowledgeBaseIds, setSelectedGraphKnowledgeBaseIds, savingSections, savedSections, saveSection } = useAgentConfig();
  return (

    <div className="agent-config-panel">
      <Text type="secondary" className="text-xs block mb-2">
        选择用于关系检索和多跳问题的知识图谱。
      </Text>

      {graphKnowledgeBases.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-muted-foreground/60">
          <ApartmentOutlined className="text-lg mb-2" />
          <Text type="secondary" className="text-xs">
            暂无可用的图谱知识库
          </Text>
          <Text type="secondary" className="text-xs">
            请先在知识图谱页面创建并抽取知识库
          </Text>
        </div>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {graphKnowledgeBases.map((kb) => {
            const isSelected = selectedGraphKnowledgeBaseIds.includes(kb.id);
            return (
              <div
                key={kb.id}
                className={cn(
                  'flex items-center gap-2 p-2 rounded-lg cursor-pointer border transition-colors',
                  isSelected
                    ? 'bg-primary/5 border-primary/30'
                    : 'border-border/50 hover:bg-muted/50',
                  !kb.is_active && 'opacity-50',
                )}
                onClick={() => {
                  setSelectedGraphKnowledgeBaseIds((prev) =>
                    isSelected
                      ? prev.filter((id) => id !== kb.id)
                      : [...prev, kb.id]
                  );
                }}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  readOnly
                  className="accent-primary"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1">
                    <Text className="text-xs font-medium truncate">{kb.name}</Text>
                    {!kb.is_active && (
                      <Tag className="text-[10px] leading-none px-1 py-0 ml-auto">已禁用</Tag>
                    )}
                  </div>
                  <Text type="secondary" className="text-[10px] block">
                    {kb.entity_count} 实体 · {kb.relationship_count} 关系
                  </Text>
                  {kb.description && (
                    <Text type="secondary" className="text-[10px] block truncate">
                      {kb.description}
                    </Text>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <SaveButtonRow
        saving={!!savingSections['graph-knowledge']}
        saved={!!savedSections['graph-knowledge']}
        onSave={() =>
          saveSection('graph-knowledge', {
            graph_knowledge_base_ids: selectedGraphKnowledgeBaseIds,
          })
        }
      />
    </div>

  );
}
