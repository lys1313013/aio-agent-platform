import { PlusOutlined, ArrowUpOutlined, ArrowDownOutlined, MinusCircleOutlined } from '@ant-design/icons';
import { Button, Select, Input, InputNumber, Tag, Typography, Switch, Divider } from 'antd';
import { PROMPT_TEMPLATES } from '@/lib/promptTemplates';
import { useAgentConfig } from './context';
import { SaveButtonRow } from './SaveButtonRow';

const { Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

export function PromptPanel() {
  const { models, selectedModelId, setSelectedModelId, systemPrompt, setSystemPrompt, agentTemperature, setAgentTemperature, agentMaxIterations, setAgentMaxIterations, welcomeMessage, setWelcomeMessage, starterPrompts, setStarterPrompts, enableMemoryExtraction, setEnableMemoryExtraction, enableRetry, setEnableRetry, enableAutoTitle, setEnableAutoTitle, savingSections, savedSections, saveSection } = useAgentConfig();
  return (

    <div className="agent-config-panel">
      {/* Model selection */}
      <Text type="secondary" className="text-xs block mb-2">
        模型 <span className="font-normal text-muted-foreground/70">· 默认使用全局模型</span>
      </Text>
      <Select
        value={selectedModelId || undefined}
        onChange={(v) => setSelectedModelId(v || null)}
        allowClear
        placeholder="选择模型"
        className="w-full"
        size="small"
      >
        {models
          .filter((m) => m.is_active)
          .map((m) => (
            <Option key={m.id} value={m.id}>
              {m.provider_name} / {m.name}
              {m.is_default && (
                <Tag color="blue" className="ml-1 text-xs">默认</Tag>
              )}
            </Option>
          ))}
      </Select>

      {/* Temperature control */}
      <div className="mt-3">
        <Text type="secondary" className="text-xs block mb-2">
          推理温度 <span className="font-normal text-muted-foreground/70">· 留空使用模型默认</span>
        </Text>
        <InputNumber
          min={0}
          max={2}
          step={0.1}
          precision={1}
          value={agentTemperature ?? undefined}
          onChange={(v) => setAgentTemperature(v ?? null)}
          placeholder="默认"
          className="w-full"
          size="small"
        />
        {agentTemperature !== null && (
          <Button
            type="link"
            size="small"
            className="!p-0 !text-xs mt-1"
            onClick={() => setAgentTemperature(null)}
          >
            重置为模型默认
          </Button>
        )}
      </div>

      {/* Max iterations control */}
      <div className="mt-3">
        <Text type="secondary" className="text-xs block mb-2">
          最大迭代次数 <span className="font-normal text-muted-foreground/70">· 默认 100，最高 500</span>
        </Text>
        <InputNumber
          min={1}
          max={500}
          step={1}
          precision={0}
          value={agentMaxIterations ?? undefined}
          onChange={(v) => setAgentMaxIterations(v ?? null)}
          placeholder="默认"
          className="w-full"
          size="small"
        />
        {agentMaxIterations !== null && (
          <Button
            type="link"
            size="small"
            className="!p-0 !text-xs mt-1"
            onClick={() => setAgentMaxIterations(null)}
          >
            重置为全局默认
          </Button>
        )}
      </div>

      <Divider className="my-4" />

      {/* System prompt */}
      <Text type="secondary" className="text-xs block mb-2">
        系统提示词 <span className="font-normal text-muted-foreground/70">· 可从模板开始</span>
      </Text>
      <Select
        placeholder="从模板加载提示词..."
        allowClear
        size="small"
        className="w-full mb-2"
        onChange={(templateId: string) => {
          const tpl = PROMPT_TEMPLATES.find(t => t.id === templateId);
          if (tpl) setSystemPrompt(tpl.content);
        }}
      >
        {PROMPT_TEMPLATES.map(t => (
          <Option key={t.id} value={t.id}>
            {t.icon} {t.name}
          </Option>
        ))}
      </Select>
      <TextArea
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
        rows={10}
        placeholder="你是一个专业的编程助手..."
        className="font-mono text-xs"
      />

      {/* Welcome message */}
      <div className="mt-4 pt-4 border-t border-border/50">
        <Text type="secondary" className="text-xs block mb-2">
          欢迎语
        </Text>
        <TextArea
          value={welcomeMessage}
          onChange={(e) => setWelcomeMessage(e.target.value)}
          rows={3}
          placeholder="你好！有什么可以帮你的吗？"
          className="text-xs"
        />
      </div>

      {/* Starter prompts */}
      <div className="mt-4 pt-4 border-t border-border/50">
        <div className="flex items-center justify-between mb-2">
          <Text type="secondary" className="text-xs">
            快捷提问
          </Text>
          <Button
            type="link"
            size="small"
            icon={<PlusOutlined />}
            className="!p-0 !text-xs !h-auto"
            onClick={() => setStarterPrompts([...starterPrompts, { label: '', icon: '💡' }])}
          >
            添加
          </Button>
        </div>

        {starterPrompts.length === 0 ? (
          <div className="py-3 text-center rounded-lg bg-muted/30 border border-dashed border-border/50">
            <Text type="secondary" className="text-xs">
              暂未配置，将使用默认提示
            </Text>
          </div>
        ) : (
          <div className="space-y-2">
            {starterPrompts.map((sp, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 p-2 rounded-lg bg-muted/30 border border-border/50 group"
              >
                <Input
                  size="small"
                  value={sp.icon}
                  onChange={(e) => {
                    const next = [...starterPrompts];
                    next[idx] = { ...sp, icon: e.target.value };
                    setStarterPrompts(next);
                  }}
                  className="!w-12 text-center !text-sm"
                  maxLength={4}
                />
                <Input
                  size="small"
                  value={sp.label}
                  onChange={(e) => {
                    const next = [...starterPrompts];
                    next[idx] = { ...sp, label: e.target.value };
                    setStarterPrompts(next);
                  }}
                  placeholder="提问内容"
                  className="flex-1 !text-xs"
                />
                <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => {
                      if (idx === 0) return;
                      const next = [...starterPrompts];
                      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                      setStarterPrompts(next);
                    }}
                    disabled={idx === 0}
                    className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
                  >
                    <ArrowUpOutlined className="text-[10px]" />
                  </button>
                  <button
                    onClick={() => {
                      if (idx === starterPrompts.length - 1) return;
                      const next = [...starterPrompts];
                      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
                      setStarterPrompts(next);
                    }}
                    disabled={idx === starterPrompts.length - 1}
                    className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
                  >
                    <ArrowDownOutlined className="text-[10px]" />
                  </button>
                  <button
                    onClick={() => setStarterPrompts(starterPrompts.filter((_, i) => i !== idx))}
                    className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition hover:bg-red-500/10 hover:text-red-500"
                  >
                    <MinusCircleOutlined className="text-[10px]" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Memory extraction toggle */}
      <div className="mt-4 pt-4 border-t border-border/50">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <Text className="text-xs block">自动记忆提取</Text>
            <Text type="secondary" className="text-[10px] block mt-0.5 leading-snug">
              对话结束后自动提取关键信息写入记忆，用于后续对话上下文
            </Text>
          </div>
          <Switch
            size="small"
            checked={enableMemoryExtraction}
            onChange={setEnableMemoryExtraction}
            className="flex-shrink-0"
          />
        </div>
      </div>

      {/* LLM retry toggle */}
      <div className="mt-4 pt-4 border-t border-border/50">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <Text className="text-xs block">LLM 自动重试</Text>
            <Text type="secondary" className="text-[10px] block mt-0.5 leading-snug">
              API 调用失败时自动重试（最多 3 次，指数退避），关闭后立即报错
            </Text>
          </div>
          <Switch
            size="small"
            checked={enableRetry}
            onChange={setEnableRetry}
            className="flex-shrink-0"
          />
        </div>
      </div>

      {/* Auto session title toggle */}
      <div className="mt-4 pt-4 border-t border-border/50">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <Text className="text-xs block">自动总结会话标题</Text>
            <Text type="secondary" className="text-[10px] block mt-0.5 leading-snug">
              新会话发出第一条消息时自动调用模型生成会话标题
            </Text>
          </div>
          <Switch
            size="small"
            checked={enableAutoTitle}
            onChange={setEnableAutoTitle}
            className="flex-shrink-0"
          />
        </div>
      </div>

      <SaveButtonRow
        saving={!!savingSections['prompt']}
        saved={!!savedSections['prompt']}
        onSave={() => saveSection('prompt', {
          model_id: selectedModelId,
          temperature: agentTemperature,
          max_iterations: agentMaxIterations,
          system_prompt: systemPrompt || null,
          welcome_message: welcomeMessage || null,
          starter_prompts: starterPrompts.filter((sp) => sp.label.trim()).length > 0
            ? starterPrompts.filter((sp) => sp.label.trim())
            : null,
          enable_memory_extraction: enableMemoryExtraction,
          enable_retry: enableRetry,
          enable_auto_title: enableAutoTitle,
        })}
      />
    </div>

  );
}
