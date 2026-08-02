import { useState, useEffect, useCallback } from 'react';
import { GlobalOutlined, SaveOutlined } from '@ant-design/icons';
import {
  Form,
  Input,
  Select,
  Button,
  Card,
  Typography,
  Spin,
  App,
  Switch,
  InputNumber,
  Tag,
  Space,
  Tooltip,
} from 'antd';
import { webToolSettingsApi } from '@/lib/api';
import type { WebToolConfig } from '@/lib/api';

const { Text } = Typography;

type SecretField = 'brave_api_key' | 'tavily_api_key' | 'firecrawl_api_key';

const SECRET_LABELS: Record<SecretField, string> = {
  brave_api_key: 'Brave API Key',
  tavily_api_key: 'Tavily API Key',
  firecrawl_api_key: 'Firecrawl API Key',
};

export default function WebToolSettingsPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<WebToolConfig | null>(null);
  // undefined = untouched (keep), '' = clear on save, other = replace on save
  const [secrets, setSecrets] = useState<Record<SecretField, string | undefined>>({
    brave_api_key: undefined,
    tavily_api_key: undefined,
    firecrawl_api_key: undefined,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const cfg = await webToolSettingsApi.get();
      setConfig(cfg);
      form.setFieldsValue({
        enabled: cfg.enabled,
        search_provider: cfg.search_provider,
        searxng_url: cfg.searxng_url,
        summary_enabled: cfg.summary_enabled,
        cache_ttl_seconds: cfg.cache_ttl_seconds,
        fetch_max_chars: cfg.fetch_max_chars,
      });
      setSecrets({
        brave_api_key: undefined,
        tavily_api_key: undefined,
        firecrawl_api_key: undefined,
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
      const payload: Record<string, unknown> = { ...values };
      for (const field of Object.keys(secrets) as SecretField[]) {
        const v = secrets[field];
        if (v !== undefined) payload[field] = v;
      }
      const updated = await webToolSettingsApi.update(payload);
      setConfig(updated);
      setSecrets({
        brave_api_key: undefined,
        tavily_api_key: undefined,
        firecrawl_api_key: undefined,
      });
      message.success('已保存，数秒内生效');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const hasKey = (field: SecretField): boolean => {
    if (!config) return false;
    return config[`has_${field}` as keyof WebToolConfig] as boolean;
  };

  const renderSecretInput = (field: SecretField) => {
    const configured = hasKey(field);
    const value = secrets[field];
    const pendingClear = value === '';
    return (
      <Form.Item
        key={field}
        label={
          <Space size={4}>
            {SECRET_LABELS[field]}
            {configured && !pendingClear && <Tag color="success">已配置</Tag>}
            {!configured && <Tag>未配置</Tag>}
            {pendingClear && <Tag color="warning">保存后清除</Tag>}
          </Space>
        }
      >
        <Space.Compact style={{ width: '100%' }}>
          <Input.Password
            placeholder={
              configured ? '输入新 Key 以更换，留空保持不变' : '输入 API Key'
            }
            value={value ?? ''}
            onChange={(e) =>
              setSecrets((prev) => ({ ...prev, [field]: e.target.value }))
            }
          />
          {configured && (
            <Tooltip title="保存后清除该 Key（回退到环境变量默认值）">
              <Button
                danger
                onClick={() => setSecrets((prev) => ({ ...prev, [field]: '' }))}
              >
                清除
              </Button>
            </Tooltip>
          )}
        </Space.Compact>
      </Form.Item>
    );
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full max-w-3xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <GlobalOutlined className="text-primary" />
            Web 工具
          </h1>
          <Text type="secondary">
            配置 Agent 的联网能力：web_search（网页搜索）与
            web_fetch（网页抓取）。改动保存后数秒内生效，无需重启。
          </Text>
        </div>

        <Form form={form} layout="vertical" disabled={saving}>
          <Card title="基本设置" className="mb-4">
            <Form.Item
              name="enabled"
              label="启用 Web 工具"
              valuePropName="checked"
              extra="关闭后所有 Agent 调用 web_search / web_fetch 将返回停用提示"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="search_provider"
              label="搜索提供商"
              extra="auto：按已配置的 Key 自动选择（Brave → Tavily → SearXNG → DuckDuckGo）"
            >
              <Select
                options={[
                  { value: 'auto', label: '自动检测（推荐）' },
                  { value: 'duckduckgo', label: 'DuckDuckGo（免费，无需 Key）' },
                  { value: 'brave', label: 'Brave Search' },
                  { value: 'tavily', label: 'Tavily' },
                  { value: 'searxng', label: 'SearXNG（自托管）' },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="cache_ttl_seconds"
              label="结果缓存时间（秒）"
              extra="搜索结果与抓取内容的缓存时间，0 表示禁用缓存"
            >
              <InputNumber min={0} max={86400} style={{ width: 200 }} />
            </Form.Item>
          </Card>

          <Card title="搜索提供商凭证" className="mb-4">
            {renderSecretInput('brave_api_key')}
            {renderSecretInput('tavily_api_key')}
            <Form.Item name="searxng_url" label="SearXNG 地址" extra="例如 http://searxng.internal:8888">
              <Input placeholder="留空则不使用 SearXNG" allowClear />
            </Form.Item>
          </Card>

          <Card title="抓取设置" className="mb-4">
            <Form.Item
              name="fetch_max_chars"
              label="单次抓取返回上限（字符）"
              extra="超过此长度的正文将被截断（或摘要）后返回给 Agent"
            >
              <InputNumber min={500} max={10000} style={{ width: 200 }} />
            </Form.Item>
            <Form.Item
              name="summary_enabled"
              label="大页面 LLM 摘要"
              valuePropName="checked"
              extra="开启后，超长页面用默认模型压缩为摘要而非直接截断（消耗 LLM 调用）"
            >
              <Switch />
            </Form.Item>
            {renderSecretInput('firecrawl_api_key')}
            <Text type="secondary" className="text-xs">
              Firecrawl：本地正文提取失败时的兜底抓取服务（真实浏览器渲染，可穿透
              JS 渲染页面），可选。
            </Text>
          </Card>

          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存
          </Button>
        </Form>
      </div>
    </div>
  );
}
