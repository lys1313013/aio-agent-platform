import { useState, useEffect, useCallback } from 'react';
import {
  ApiOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import {
  Form,
  Input,
  Select,
  Button,
  Card,
  Typography,
  Spin,
  App,
  Modal,
  Tag,
  Popconfirm,
  Space,
  Switch,
  InputNumber,
  Empty,
  Tooltip,
  Collapse,
} from 'antd';
import { remoteToolsApi } from '@/lib/api';
import type { RemoteTool, RemoteToolCreate } from '@/lib/types';
import { useAuthStore } from '@/stores/authStore';
import { Navigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

// HTTP method display metadata
const METHOD_META: Record<string, { color: string }> = {
  GET: { color: 'green' },
  POST: { color: 'blue' },
  PUT: { color: 'orange' },
  DELETE: { color: 'red' },
  PATCH: { color: 'purple' },
};

// Auth type display metadata
const AUTH_META: Record<string, { label: string; color: string }> = {
  none: { label: '无认证', color: 'default' },
  bearer: { label: 'Bearer Token', color: 'blue' },
  api_key: { label: 'API Key', color: 'cyan' },
  basic: { label: 'Basic Auth', color: 'geekblue' },
  custom_header: { label: '自定义 Header', color: 'purple' },
};

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
type AuthType = 'none' | 'bearer' | 'api_key' | 'basic' | 'custom_header';

export default function RemoteToolManagementPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin';

  // ---- List state ----
  const [tools, setTools] = useState<RemoteTool[]>([]);
  const [loading, setLoading] = useState(true);

  // ---- Edit/Create modal ----
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<RemoteTool | null>(null);
  const [form] = Form.useForm();
  const [authType, setAuthType] = useState<AuthType>('none');

  // ---- Test modal ----
  const [testModalOpen, setTestModalOpen] = useState(false);
  const [testTool, setTestTool] = useState<RemoteTool | null>(null);
  const [testForm] = Form.useForm();
  const [testResult, setTestResult] = useState<{ success: boolean; body: string; error: string | null } | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  // ---- Admin guard ----
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  // ---- Data fetching ----
  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await remoteToolsApi.list();
      setTools(data);
    } catch (err: any) {
      message.error(`加载远程工具失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ---- Parse JSON safely ----
  const parseJSON = (text: string | undefined | null): any => {
    if (!text?.trim()) return undefined;
    try {
      return JSON.parse(text);
    } catch {
      throw new Error('JSON 格式不正确');
    }
  };

  // ---- Modal open/close ----
  const openModal = (tool?: RemoteTool) => {
    if (tool) {
      setEditingTool(tool);
      form.setFieldsValue({
        name: tool.name,
        label: tool.label,
        description: tool.description,
        method: tool.method,
        url_template: tool.url_template,
        parameters_schema: JSON.stringify(tool.parameters_schema, null, 2),
        headers: tool.headers ? JSON.stringify(tool.headers, null, 2) : '',
        auth_type: tool.auth_type,
        auth_token: '',
        auth_header_name: tool.auth_config_masked?.header_name as string || '',
        auth_key: '',
        auth_username: '',
        auth_password: '',
        auth_custom_headers: '',
        body_template: tool.body_template ? JSON.stringify(tool.body_template, null, 2) : '',
        response_extract: tool.response_extract || '',
        timeout: tool.timeout,
        is_active: tool.is_active,
      });
      setAuthType(tool.auth_type);
    } else {
      setEditingTool(null);
      form.resetFields();
      form.setFieldsValue({
        method: 'POST',
        auth_type: 'none',
        timeout: 30,
        is_active: true,
        parameters_schema: '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}',
      });
      setAuthType('none');
    }
    setModalOpen(true);
  };

  // ---- Build auth_config from form values ----
  const buildAuthConfig = (values: any): Record<string, unknown> | undefined => {
    switch (values.auth_type) {
      case 'bearer':
        return values.auth_token ? { token: values.auth_token } : undefined;
      case 'api_key':
        return {
          header_name: values.auth_header_name || 'X-API-Key',
          key: values.auth_key || '',
        };
      case 'basic':
        return {
          username: values.auth_username || '',
          password: values.auth_password || '',
        };
      case 'custom_header': {
        if (!values.auth_custom_headers?.trim()) return undefined;
        try {
          return { headers: JSON.parse(values.auth_custom_headers) };
        } catch {
          throw new Error('自定义 Header JSON 格式不正确');
        }
      }
      default:
        return undefined;
    }
  };

  // ---- Save (create or update) ----
  const handleSave = async () => {
    try {
      const values = await form.validateFields();

      // Parse JSON fields
      let parametersSchema: Record<string, unknown>;
      try {
        parametersSchema = parseJSON(values.parameters_schema) || { type: 'object', properties: {} };
      } catch (e: any) {
        message.error(`参数 Schema ${e.message}`);
        return;
      }

      let headers: Record<string, string> | null = null;
      if (values.headers?.trim()) {
        try {
          headers = parseJSON(values.headers);
        } catch (e: any) {
          message.error(`Headers ${e.message}`);
          return;
        }
      }

      let bodyTemplate: Record<string, unknown> | null = null;
      if (values.body_template?.trim()) {
        try {
          bodyTemplate = parseJSON(values.body_template);
        } catch (e: any) {
          message.error(`请求体模板 ${e.message}`);
          return;
        }
      }

      let authConfig: Record<string, unknown> | null = null;
      try {
        authConfig = buildAuthConfig(values) ?? null;
      } catch (e: any) {
        message.error(e.message);
        return;
      }

      // For update: only send auth_config if user provided new values
      if (editingTool && !values.auth_token && !values.auth_key && !values.auth_password && !values.auth_custom_headers) {
        authConfig = null;
      }

      const payload: RemoteToolCreate = {
        name: values.name,
        label: values.label,
        description: values.description,
        method: values.method as HttpMethod,
        url_template: values.url_template,
        parameters_schema: parametersSchema,
        headers,
        auth_type: values.auth_type as AuthType,
        auth_config: authConfig,
        body_template: bodyTemplate,
        response_extract: values.response_extract || null,
        timeout: values.timeout,
        is_active: values.is_active,
      };

      if (editingTool) {
        await remoteToolsApi.update(editingTool.id, payload);
        message.success('远程工具已更新');
      } else {
        await remoteToolsApi.create(payload);
        message.success('远程工具已创建');
      }
      setModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    }
  };

  // ---- Delete ----
  const handleDelete = async (id: string) => {
    try {
      await remoteToolsApi.delete(id);
      message.success('远程工具已删除');
      fetchData();
    } catch (err: any) {
      message.error(`删除失败：${err.message}`);
    }
  };

  // ---- Toggle active ----
  const handleToggle = async (id: string) => {
    try {
      await remoteToolsApi.toggle(id);
      message.success('状态已更新');
      fetchData();
    } catch (err: any) {
      message.error(`操作失败：${err.message}`);
    }
  };

  // ---- Test modal ----
  const openTestModal = (tool: RemoteTool) => {
    setTestTool(tool);
    setTestResult(null);
    setTestLoading(false);
    // Build form fields from parameters_schema
    const props = (tool.parameters_schema as any)?.properties || {};
    const defaults: Record<string, string> = {};
    for (const [key, schema] of Object.entries(props)) {
      defaults[key] = (schema as any)?.default?.toString() || '';
    }
    testForm.setFieldsValue(defaults);
    setTestModalOpen(true);
  };

  const handleTest = async () => {
    if (!testTool) return;
    try {
      const values = await testForm.validateFields();
      setTestLoading(true);
      setTestResult(null);

      // Convert empty strings to proper types based on schema
      const props = (testTool.parameters_schema as any)?.properties || {};
      const args: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(values)) {
        if (value === '' || value === undefined) continue;
        const propSchema = props[key] as any;
        if (propSchema?.type === 'number' || propSchema?.type === 'integer') {
          args[key] = Number(value);
        } else if (propSchema?.type === 'boolean') {
          args[key] = value === 'true' || value === true;
        } else {
          args[key] = value;
        }
      }

      const result = await remoteToolsApi.test(testTool.id, args);
      setTestResult({
        success: result.success,
        body: result.response_body || '',
        error: result.error,
      });
    } catch (err: any) {
      if (err?.errorFields) return;
      setTestResult({
        success: false,
        body: '',
        error: err.message || '测试失败',
      });
    } finally {
      setTestLoading(false);
    }
  };

  // ---- Loading state ----
  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <ApiOutlined className="text-primary" />
              远程工具管理
            </h1>
            <Text type="secondary">
              配置 REST API 端点为 Agent 可调用工具，无需编写代码，无需 MCP 协议
            </Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
            添加远程工具
          </Button>
        </div>

        {/* Empty state */}
        {tools.length === 0 ? (
          <Card>
            <Empty description="暂无远程工具，请添加 REST API 端点以扩展 Agent 的工具能力。" image={Empty.PRESENTED_IMAGE_SIMPLE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
                添加远程工具
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {tools.map((tool) => {
              const methodInfo = METHOD_META[tool.method] || METHOD_META.GET;
              const authInfo = AUTH_META[tool.auth_type] || AUTH_META.none;
              return (
                <Card
                  key={tool.id}
                  className={cn(
                    'transition-all',
                    !tool.is_active && 'opacity-60',
                  )}
                  title={
                    <div className="flex items-center gap-2">
                      <ApiOutlined />
                      <span className="font-semibold">{tool.label}</span>
                      <span className="font-mono text-xs text-muted-foreground">({tool.name})</span>
                      <Tag color={methodInfo.color}>{tool.method}</Tag>
                      <Tag color={authInfo.color}>{authInfo.label}</Tag>
                    </div>
                  }
                  extra={
                    <Space size={4}>
                      <Tooltip title="测试调用">
                        <Button
                          type="text"
                          size="small"
                          icon={<ThunderboltOutlined />}
                          onClick={() => openTestModal(tool)}
                        />
                      </Tooltip>
                      <Tooltip title={tool.is_active ? '禁用' : '启用'}>
                        <Button
                          type="text"
                          size="small"
                          icon={tool.is_active ? <CheckCircleOutlined className="text-green-500" /> : <CloseCircleOutlined />}
                          onClick={() => handleToggle(tool.id)}
                        />
                      </Tooltip>
                      <Tooltip title="编辑">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => openModal(tool)}
                        />
                      </Tooltip>
                      <Popconfirm
                        title="删除远程工具？"
                        description="删除后 Agent 将无法调用该工具。"
                        onConfirm={() => handleDelete(tool.id)}
                        okText="删除"
                        okType="danger"
                        cancelText="取消"
                      >
                        <Tooltip title="删除">
                          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                        </Tooltip>
                      </Popconfirm>
                    </Space>
                  }
                >
                  <div className="space-y-2 text-sm">
                    {/* Description */}
                    <div className="flex items-start gap-2">
                      <Text type="secondary" className="w-20 shrink-0">描述</Text>
                      <Text className="text-xs line-clamp-2">{tool.description}</Text>
                    </div>

                    {/* URL */}
                    <div className="flex items-start gap-2">
                      <Text type="secondary" className="w-20 shrink-0">URL</Text>
                      <Text className="font-mono text-xs truncate" code>
                        {tool.url_template}
                      </Text>
                    </div>

                    {/* Timeout */}
                    <div className="flex items-center gap-2">
                      <Text type="secondary" className="w-20 shrink-0">超时</Text>
                      <Text>{tool.timeout}s</Text>
                    </div>

                    {/* Response extract */}
                    {tool.response_extract && (
                      <div className="flex items-center gap-2">
                        <Text type="secondary" className="w-20 shrink-0">响应提取</Text>
                        <Text className="font-mono text-xs" code>{tool.response_extract}</Text>
                      </div>
                    )}

                    {/* Parameters Schema */}
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground transition-colors">
                        参数 Schema
                      </summary>
                      <pre className="mt-1 text-xs font-mono whitespace-pre-wrap break-all bg-muted/50 p-2 rounded max-h-40 overflow-y-auto">
                        {JSON.stringify(tool.parameters_schema, null, 2)}
                      </pre>
                    </details>
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {/* ---- Create/Edit Modal ---- */}
        <Modal
          title={editingTool ? '编辑远程工具' : '添加远程工具'}
          open={modalOpen}
          onOk={handleSave}
          onCancel={() => setModalOpen(false)}
          destroyOnHidden
          width={720}
          okText="保存"
          cancelText="取消"
        >
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              method: 'POST',
              auth_type: 'none',
              timeout: 30,
              is_active: true,
              parameters_schema: '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}',
            }}
            className="mt-4"
          >
            {/* Section 1: Basic Info */}
            <Collapse
              defaultActiveKey={['basic', 'params', 'request', 'auth']}
              ghost
              items={[
                {
                  key: 'basic',
                  label: <Text strong>基本信息</Text>,
                  children: (
                    <>
                      <Form.Item
                        name="name"
                        label="工具标识"
                        rules={[
                          { required: true, message: '请输入工具标识' },
                          { pattern: /^[a-zA-Z0-9_-]+$/, message: '仅支持字母、数字、下划线、短横线' },
                        ]}
                        tooltip="LLM 可见的工具标识，需符合 function name 规范（英文）"
                      >
                        <Input placeholder="例如：voucher_ocr" />
                      </Form.Item>

                      <Form.Item
                        name="label"
                        label="显示名称"
                        rules={[{ required: true, message: '请输入显示名称' }]}
                        tooltip="在管理界面和工具列表中显示的中文名称"
                      >
                        <Input placeholder="例如：凭证识别" />
                      </Form.Item>

                      <Form.Item
                        name="description"
                        label="工具描述"
                        rules={[{ required: true, message: '请输入工具描述' }]}
                        tooltip="指导 LLM 何时调用此工具"
                      >
                        <TextArea rows={2} placeholder="例如：对凭证图片进行 OCR 识别，提取关键字段" />
                      </Form.Item>

                      <div className="flex gap-4">
                        <Form.Item
                          name="method"
                          label="HTTP 方法"
                          rules={[{ required: true }]}
                          className="w-40"
                        >
                          <Select>
                            <Option value="GET"><Tag color="green">GET</Tag></Option>
                            <Option value="POST"><Tag color="blue">POST</Tag></Option>
                            <Option value="PUT"><Tag color="orange">PUT</Tag></Option>
                            <Option value="DELETE"><Tag color="red">DELETE</Tag></Option>
                            <Option value="PATCH"><Tag color="purple">PATCH</Tag></Option>
                          </Select>
                        </Form.Item>

                        <Form.Item
                          name="url_template"
                          label="URL 模板"
                          rules={[{ required: true, message: '请输入 URL' }]}
                          tooltip="支持 {变量名} 路径变量，如 https://api.example.com/users/{user_id}"
                          className="flex-1"
                        >
                          <Input placeholder="https://api.example.com/v1/resource" />
                        </Form.Item>
                      </div>
                    </>
                  ),
                },
                {
                  key: 'params',
                  label: <Text strong>参数定义</Text>,
                  children: (
                    <Form.Item
                      name="parameters_schema"
                      label="JSON Schema"
                      tooltip="定义 LLM 需要传入的参数，JSON Schema 格式"
                    >
                      <TextArea
                        rows={8}
                        placeholder={'{\n  "type": "object",\n  "properties": {\n    "image_url": {\n      "type": "string",\n      "description": "图片 URL"\n    }\n  },\n  "required": ["image_url"]\n}'}
                        className="font-mono text-xs"
                      />
                    </Form.Item>
                  ),
                },
                {
                  key: 'request',
                  label: <Text strong>请求配置</Text>,
                  children: (
                    <>
                      <Form.Item
                        name="headers"
                        label="静态请求头 (JSON)"
                        tooltip='可选，格式 {"Accept": "application/json"}'
                      >
                        <TextArea
                          rows={2}
                          placeholder='{"Accept": "application/json"}'
                          className="font-mono text-xs"
                        />
                      </Form.Item>

                      <Form.Item
                        name="body_template"
                        label="请求体模板 (JSON)"
                        tooltip="支持 {{变量名}} 插值，留空则将参数直接作为请求体"
                      >
                        <TextArea
                          rows={6}
                          placeholder={'{\n  "model": "my-model",\n  "messages": [\n    {\n      "role": "user",\n      "content": "{{content}}"\n    }\n  ]\n}'}
                          className="font-mono text-xs"
                        />
                      </Form.Item>

                      <Form.Item
                        name="response_extract"
                        label="响应提取 (JSONPath)"
                        tooltip="只返回指定路径的内容，如 $.choices[0].message.content"
                      >
                        <Input placeholder="$.data 或 $.choices[0].message.content" />
                      </Form.Item>
                    </>
                  ),
                },
                {
                  key: 'auth',
                  label: <Text strong>认证与安全</Text>,
                  children: (
                    <>
                      <Form.Item
                        name="auth_type"
                        label="认证类型"
                      >
                        <Select onChange={(v: AuthType) => setAuthType(v)}>
                          <Option value="none">无认证</Option>
                          <Option value="bearer">Bearer Token</Option>
                          <Option value="api_key">API Key (自定义 Header)</Option>
                          <Option value="basic">Basic Auth</Option>
                          <Option value="custom_header">自定义 Header</Option>
                        </Select>
                      </Form.Item>

                      {authType === 'bearer' && (
                        <Form.Item
                          name="auth_token"
                          label="Token"
                          tooltip="将自动注入 Authorization: Bearer {token}"
                        >
                          <Input.Password placeholder={editingTool ? '留空保持原值不变' : '输入 Bearer Token'} />
                        </Form.Item>
                      )}

                      {authType === 'api_key' && (
                        <>
                          <Form.Item
                            name="auth_header_name"
                            label="Header 名称"
                            tooltip="API Key 对应的 Header 名称"
                          >
                            <Input placeholder="X-API-Key" />
                          </Form.Item>
                          <Form.Item
                            name="auth_key"
                            label="API Key"
                          >
                            <Input.Password placeholder={editingTool ? '留空保持原值不变' : '输入 API Key'} />
                          </Form.Item>
                        </>
                      )}

                      {authType === 'basic' && (
                        <>
                          <Form.Item
                            name="auth_username"
                            label="用户名"
                          >
                            <Input placeholder="用户名" />
                          </Form.Item>
                          <Form.Item
                            name="auth_password"
                            label="密码"
                          >
                            <Input.Password placeholder={editingTool ? '留空保持原值不变' : '密码'} />
                          </Form.Item>
                        </>
                      )}

                      {authType === 'custom_header' && (
                        <Form.Item
                          name="auth_custom_headers"
                          label="自定义 Header (JSON)"
                          tooltip='格式 {"X-Token": "xxx", "X-Org-Id": "123"}'
                        >
                          <TextArea
                            rows={3}
                            placeholder='{"X-Token": "xxx"}'
                            className="font-mono text-xs"
                          />
                        </Form.Item>
                      )}

                      <div className="flex gap-4">
                        <Form.Item
                          name="timeout"
                          label="超时时间 (秒)"
                          className="w-40"
                        >
                          <InputNumber min={5} max={600} className="w-full" />
                        </Form.Item>

                        <Form.Item
                          name="is_active"
                          label="启用"
                          valuePropName="checked"
                        >
                          <Switch />
                        </Form.Item>
                      </div>
                    </>
                  ),
                },
              ]}
            />
          </Form>
        </Modal>

        {/* ---- Test Modal ---- */}
        <Modal
          title={
            <div className="flex items-center gap-2">
              <ThunderboltOutlined />
              <span>测试: {testTool?.name}</span>
              <Tag color={METHOD_META[testTool?.method || 'GET']?.color}>{testTool?.method}</Tag>
            </div>
          }
          open={testModalOpen}
          onCancel={() => setTestModalOpen(false)}
          footer={null}
          width={700}
        >
          <div className="mt-4 space-y-4">
            <div className="p-2 bg-muted/50 rounded-md">
              <Text type="secondary" className="text-xs font-mono">{testTool?.url_template}</Text>
            </div>

            <Form form={testForm} layout="vertical">
              {(() => {
                const props = (testTool?.parameters_schema as any)?.properties || {};
                const required = ((testTool?.parameters_schema as any)?.required || []) as string[];
                return Object.entries(props).map(([key, schema]: [string, any]) => (
                  <Form.Item
                    key={key}
                    name={key}
                    label={
                      <span>
                        <code className="font-mono">{key}</code>
                        {required.includes(key) && <span className="text-red-500 ml-1">*</span>}
                      </span>
                    }
                    tooltip={schema.description}
                    rules={required.includes(key) ? [{ required: true, message: `${key} 为必填` }] : []}
                  >
                    <Input placeholder={schema.description || schema.type || ''} />
                  </Form.Item>
                ));
              })()}
            </Form>

            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={testLoading}
              onClick={handleTest}
              block
            >
              发送测试请求
            </Button>

            {testResult && (
              <div className={cn(
                'p-4 rounded-lg border',
                testResult.success
                  ? 'bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900'
                  : 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900',
              )}>
                <div className="flex items-center gap-2 mb-2">
                  {testResult.success
                    ? <CheckCircleOutlined className="text-green-600" />
                    : <CloseCircleOutlined className="text-red-600" />
                  }
                  <Text strong={!testResult.success} type={testResult.success ? undefined : 'danger'}>
                    {testResult.success ? '请求成功' : '请求失败'}
                  </Text>
                </div>
                <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-60 overflow-y-auto bg-background/50 p-2 rounded">
                  {testResult.success ? testResult.body : testResult.error}
                </pre>
              </div>
            )}
          </div>
        </Modal>
      </div>
    </div>
  );
}
