import { useState, useEffect, useCallback } from 'react';
import { SaveOutlined } from '@ant-design/icons';
import { Form, Select, Button, Card, Typography, Spin, App, Input } from 'antd';
import { systemConfigApi, adminApi } from '@/lib/api';
import type { LLMModel } from '@/lib/api';

const { Text } = Typography;
const { TextArea } = Input;

export default function SystemConfigPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState<LLMModel[]>([]);
  const [defaultPrompt, setDefaultPrompt] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, modelList] = await Promise.all([
        systemConfigApi.getAutoTitle(),
        adminApi.listModels(),
      ]);
      setModels(modelList.filter((m) => m.is_active));
      setDefaultPrompt(cfg.default_prompt);
      form.setFieldsValue({
        model_id: cfg.model_id,
        prompt: cfg.prompt,
      });
    } catch {
      message.error('加载配置失败');
    } finally {
      setLoading(false);
    }
  }, [form, message]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await systemConfigApi.updateAutoTitle({
        model_id: values.model_id ?? null,
        prompt: values.prompt ?? '',
      });
      message.success('已保存');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl p-6">
      <Card
        title="自动总结会话标题"
        extra={
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="model_id"
            label="标题生成模型"
            extra="不选择时使用系统默认模型"
          >
            <Select
              allowClear
              placeholder="使用默认模型"
              options={models.map((m) => ({
                value: m.id,
                label: `${m.name}（${m.provider_name}）${m.is_default ? ' · 默认' : ''}`,
              }))}
            />
          </Form.Item>

          <Form.Item
            name="prompt"
            label="标题生成提示词"
            extra={
              <Text type="secondary">
                提示词中可使用 {'{message}'} 占位符引用用户的首条消息
              </Text>
            }
            rules={[{ required: true, message: '请输入提示词' }]}
          >
            <TextArea rows={10} placeholder={defaultPrompt} />
          </Form.Item>

          <Form.Item label={null}>
            <Button onClick={() => form.setFieldsValue({ prompt: defaultPrompt })}>
              恢复默认提示词
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
