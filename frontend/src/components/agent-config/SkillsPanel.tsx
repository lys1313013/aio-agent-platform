import { useState } from 'react';
import { ThunderboltOutlined, CloseOutlined, SearchOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Checkbox, Tag, Typography, Modal } from 'antd';
import { useAgentConfig } from './context';
import { SaveButtonRow } from './SaveButtonRow';

const { Text } = Typography;

const SKILL_CATEGORY_REVERSE: Record<string, string> = {
  'general': '通用', 'coding': '编码', 'ops': '运维', 'research': '研究', 'writing': '写作',
};
export function SkillsPanel() {
  const { skills, selectedSkillIds, setSelectedSkillIds, savingSections, savedSections, saveSection } = useAgentConfig();
  const [skillsModalOpen, setSkillsModalOpen] = useState(false);
  const [skillsSearchQuery, setSkillsSearchQuery] = useState('');
  const [skillsDraft, setSkillsDraft] = useState<string[]>([]);
  return (

    <>
      <div className="agent-config-panel">
        <Text type="secondary" className="text-xs block mb-2">
          绑定对话中可自动匹配的技能。
        </Text>

        {/* Selected skills */}
        {selectedSkillIds.length > 0 && (
          <>
            <div className="flex items-center justify-between mb-2">
              <Text type="secondary" className="text-xs font-semibold">
                已绑定 ({selectedSkillIds.length})
              </Text>
              <div className="flex items-center gap-2">
                <Button
                  size="small"
                  type="link"
                  className="!p-0 !text-xs"
                  onClick={() => setSelectedSkillIds(skills.map((s) => s.id))}
                >
                  全选
                </Button>
                <Button
                  size="small"
                  type="link"
                  danger
                  className="!p-0 !text-xs"
                  onClick={() => setSelectedSkillIds([])}
                >
                  清空
                </Button>
              </div>
            </div>
            <div className="space-y-1.5 mb-3 max-h-80 overflow-y-auto">
              {selectedSkillIds.map((sid) => {
                const skill = skills.find((s) => s.id === sid);
                if (!skill) return null;
                const rate = skill.use_count > 0 ? Math.round((skill.success_count / skill.use_count) * 100) : 0;
                return (
                  <div
                    key={sid}
                    className="flex items-start gap-2.5 p-2.5 rounded-lg border border-primary/20 bg-primary/[0.03] hover:bg-primary/[0.06] transition-colors group"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <ThunderboltOutlined className="text-amber-500 text-xs flex-shrink-0" />
                        <Text className="text-xs font-medium truncate">{skill.name}</Text>
                        <Tag className="!text-[9px] !px-1 !py-0 flex-shrink-0" color="blue">v{skill.version}</Tag>
                      </div>
                      {skill.description && (
                        <Text type="secondary" className="text-[11px] block mt-0.5 line-clamp-2 leading-relaxed">
                          {skill.description}
                        </Text>
                      )}
                      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                        {skill.category && (
                          <span className="text-[10px] text-muted-foreground bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded-full">
                            {SKILL_CATEGORY_REVERSE[skill.category] || skill.category}
                          </span>
                        )}
                        <span className="text-[10px] text-muted-foreground">
                          使用 {skill.use_count} 次
                        </span>
                        {skill.use_count > 0 && (
                          <span className={`text-[10px] ${rate >= 80 ? 'text-green-600' : rate >= 50 ? 'text-amber-600' : 'text-red-500'}`}>
                            成功率 {rate}%
                          </span>
                        )}
                        {skill.files && skill.files.length > 0 && (
                          <span className="text-[10px] text-muted-foreground">
                            {skill.files.length} 个文件
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={() => setSelectedSkillIds((prev) => prev.filter((id) => id !== sid))}
                      className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100 mt-0.5"
                    >
                      <CloseOutlined className="text-[10px]" />
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
              setSkillsDraft([...selectedSkillIds]);
              setSkillsSearchQuery('');
              setSkillsModalOpen(true);
            }}
            className="text-xs"
          >
            添加技能
          </Button>
        </div>

        {selectedSkillIds.length === 0 && (
          <div className="py-4 text-center">
            <ThunderboltOutlined className="text-lg text-muted-foreground/30" />
            <Text type="secondary" className="text-xs block mt-1">暂未绑定技能</Text>
            <Text type="secondary" className="text-[10px] block mt-0.5">绑定后 Agent 可自动调用相关技能</Text>
          </div>
        )}

        <SaveButtonRow
          saving={!!savingSections['skills']}
          saved={!!savedSections['skills']}
          onSave={() => saveSection('skills', { skill_ids: selectedSkillIds })}
        />
      </div>

      {/* Skills selection modal */}
      <Modal
        title={
          <div className="flex items-center gap-2">
            <ThunderboltOutlined className="text-amber-500" />
            <span className="font-semibold">选择技能</span>
          </div>
        }
        open={skillsModalOpen}
        onCancel={() => {
          setSkillsModalOpen(false);
          setSkillsSearchQuery('');
        }}
        onOk={() => {
          setSelectedSkillIds(skillsDraft);
          setSkillsModalOpen(false);
          setSkillsSearchQuery('');
        }}
        okText="确定"
        cancelText="取消"
        width={Math.min(1100, window.innerWidth - 80)}
        destroyOnHidden
        styles={{ body: { maxHeight: '75vh', overflowY: 'auto', padding: '20px' } }}
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1">
            <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/60" />
            <input
              type="text"
              placeholder="搜索技能名称或描述..."
              value={skillsSearchQuery}
              onChange={(e) => setSkillsSearchQuery(e.target.value)}
              className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
            />
          </div>
          <Text type="secondary" className="text-xs whitespace-nowrap">
            已选 {skillsDraft.length} / 共 {skills.length}
          </Text>
          <Button
            size="small"
            type="link"
            className="!p-0 !text-xs"
            onClick={() => setSkillsDraft(skills.map((s) => s.id))}
          >
            全选
          </Button>
          <Button
            size="small"
            type="link"
            danger
            className="!p-0 !text-xs"
            disabled={skillsDraft.length === 0}
            onClick={() => setSkillsDraft([])}
          >
            清空
          </Button>
        </div>

        {(() => {
          const q = skillsSearchQuery.toLowerCase();
          const filtered = skills.filter((s) => {
            if (!q) return true;
            return (
              s.name.toLowerCase().includes(q) ||
              (s.description || '').toLowerCase().includes(q) ||
              (s.tags || []).some((t: string) => t.toLowerCase().includes(q))
            );
          });

          if (filtered.length === 0) {
            return (
              <div className="py-12 text-center">
                <ThunderboltOutlined className="text-3xl text-muted-foreground/20 mb-3 block" />
                <Text type="secondary" className="text-sm block">
                  {skillsSearchQuery ? '未找到匹配的技能' : '暂无可用的技能'}
                </Text>
              </div>
            );
          }

          const selectedCount = filtered.filter((s) => skillsDraft.includes(s.id)).length;
          const allSelected = selectedCount === filtered.length;

          return (
            <div>
              <div className="flex items-center justify-between mb-3">
                <Text type="secondary" className="text-sm font-semibold">
                  可用技能 ({filtered.length})
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
                      setSkillsDraft((prev) => prev.filter((id) => !filtered.find((s) => s.id === id)));
                    } else {
                      const ids = filtered.map((s) => s.id);
                      setSkillsDraft((prev) => [...prev, ...ids.filter((id) => !prev.includes(id))]);
                    }
                  }}
                >
                  {allSelected ? '取消全选' : '全选'}
                </Button>
              </div>

              <div className="grid grid-cols-2 gap-2.5">
                {filtered.map((skill) => {
                  const isSelected = skillsDraft.includes(skill.id);
                  const rate = skill.use_count > 0 ? Math.round((skill.success_count / skill.use_count) * 100) : 0;
                  return (
                    <div
                      key={skill.id}
                      className={`flex items-start gap-2.5 p-3 rounded-lg cursor-pointer border transition-colors ${isSelected
                        ? 'bg-primary/5 border-primary/30 ring-1 ring-primary/10'
                        : 'border-border/50 hover:bg-muted/50'
                        }`}
                      onClick={() => {
                        if (isSelected) {
                          setSkillsDraft((prev) => prev.filter((id) => id !== skill.id));
                        } else {
                          setSkillsDraft((prev) => [...prev, skill.id]);
                        }
                      }}
                    >
                      <Checkbox checked={isSelected} className="mt-0.5" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <ThunderboltOutlined className="text-amber-500 text-xs" />
                          <Text className="text-sm font-medium truncate">{skill.name}</Text>
                          <Tag className="!text-[9px] !px-1 !py-0" color="blue">v{skill.version}</Tag>
                          {skill.is_public && (
                            <Tag className="!text-[9px] !px-1 !py-0" color="green">公共</Tag>
                          )}
                        </div>
                        {skill.description && (
                          <Text type="secondary" className="text-xs block mt-1 line-clamp-2 leading-relaxed">
                            {skill.description}
                          </Text>
                        )}
                        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                          <span className="text-[10px] text-muted-foreground bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded-full">
                            {SKILL_CATEGORY_REVERSE[skill.category] || skill.category}
                          </span>
                          <span className="text-[10px] text-muted-foreground">
                            使用 {skill.use_count} 次
                          </span>
                          {skill.use_count > 0 && (
                            <span className={`text-[10px] ${rate >= 80 ? 'text-green-600' : rate >= 50 ? 'text-amber-600' : 'text-red-500'}`}>
                              成功率 {rate}%
                            </span>
                          )}
                        </div>
                        {skill.tags && skill.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {skill.tags.slice(0, 3).map((t: string) => (
                              <Tag key={t} className="!text-[9px] !px-1 !py-0 rounded-full">{t}</Tag>
                            ))}
                            {skill.tags.length > 3 && (
                              <Text type="secondary" className="text-[9px]">+{skill.tags.length - 3}</Text>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}
      </Modal>
    </>

  );
}
