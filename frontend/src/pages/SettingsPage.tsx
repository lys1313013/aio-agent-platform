import { useState, useEffect } from 'react';
import {
  SettingOutlined,
  UserOutlined,
  SafetyOutlined,
  BulbOutlined,
  SaveOutlined,
  KeyOutlined,
} from '@ant-design/icons';
import {
  Tabs,
  Form,
  Input,
  Slider,
  Radio,
  Button,
  Card,
  Typography,
  InputNumber,
  Spin,
  App,
} from 'antd';
import type { TabsProps } from 'antd';
import { settingsApi } from '@/lib/api';

const { Text } = Typography;

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState('profile');

  const tabItems: TabsProps['items'] = [
    { key: 'profile', label: <span><UserOutlined /> 个人信息</span> },
    { key: 'security', label: <span><SafetyOutlined /> 安全设置</span> },
    { key: 'memory', label: <span><BulbOutlined /> 记忆配置</span> },
  ];

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <SettingOutlined className="text-primary" />
            设置
          </h1>
          <Text type="secondary">
            管理您的个人信息和 Agent 行为。
          </Text>
        </div>

        {/* Tabs */}
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          className="mb-6"
        />

        {/* Tab content */}
        {activeTab === 'profile' && <ProfileSettings />}
        {activeTab === 'security' && <SecuritySettings />}
        {activeTab === 'memory' && <MemorySettings />}
      </div>
    </div>
  );
}

// ============================================================
// Profile Settings
// ============================================================

function ProfileSettings() {
  const { message } = App.useApp();
  const [profileForm] = Form.useForm();
  const [passwordForm] = Form.useForm();
  const [profileLoading, setProfileLoading] = useState(false);
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [initLoading, setInitLoading] = useState(true);

  useEffect(() => {
    settingsApi
      .getProfile()
      .then((data) => {
        profileForm.setFieldsValue({
          username: data.username,
          email: data.email,
          displayName: data.display_name,
        });
      })
      .catch((err) => message.error(`加载个人信息失败：${err.message}`))
      .finally(() => setInitLoading(false));
  }, [profileForm]);

  const handleProfileSave = async (values: { username?: string; email?: string; displayName?: string }) => {
    setProfileLoading(true);
    try {
      await settingsApi.updateProfile({
        username: values.username,
        email: values.email,
        display_name: values.displayName,
      });
      message.success('个人信息已保存');
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setProfileLoading(false);
    }
  };

  const handlePasswordSave = async (values: { currentPassword: string; newPassword: string; confirmPassword: string }) => {
    if (values.newPassword !== values.confirmPassword) {
      message.error('两次输入的新密码不一致');
      return;
    }
    setPasswordLoading(true);
    try {
      await settingsApi.updatePassword({
        current_password: values.currentPassword,
        new_password: values.newPassword,
      });
      message.success('密码已更新');
      passwordForm.resetFields();
    } catch (err: any) {
      message.error(err.message || '更新密码失败');
    } finally {
      setPasswordLoading(false);
    }
  };

  if (initLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Card title="账户信息" extra={<Text type="secondary">更新您的账户详情。</Text>}>
        <Form form={profileForm} layout="vertical" onFinish={handleProfileSave}>
          <Form.Item name="username" label="用户名">
            <Input placeholder="alice" />
          </Form.Item>
          <Form.Item name="displayName" label="显示名称">
            <Input placeholder="Alice" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="alice@example.com" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={profileLoading}>
              保存更改
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="修改密码" extra={<Text type="secondary">更新您的密码。</Text>}>
        <Form form={passwordForm} layout="vertical" onFinish={handlePasswordSave}>
          <Form.Item name="currentPassword" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '密码至少 8 个字符' },
            ]}
          >
            <Input.Password placeholder="至少 8 个字符" />
          </Form.Item>
          <Form.Item
            name="confirmPassword"
            label="确认新密码"
            rules={[{ required: true, message: '请确认新密码' }]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<KeyOutlined />} loading={passwordLoading}>
              更新密码
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}

// ============================================================
// Security Settings
// ============================================================

function SecuritySettings() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [initLoading, setInitLoading] = useState(true);

  useEffect(() => {
    settingsApi
      .getSecurityConfig()
      .then((data) => {
        form.setFieldsValue({ trust: data.trust_level });
      })
      .catch((err) => message.error(`加载安全设置失败：${err.message}`))
      .finally(() => setInitLoading(false));
  }, [form]);

  const handleSave = async (values: { trust: string }) => {
    setLoading(true);
    try {
      await settingsApi.updateSecurityConfig(values.trust);
      message.success('安全设置已保存');
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setLoading(false);
    }
  };

  if (initLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <Card title="信任级别" extra={<Text type="secondary">控制 Agent 在执行工具前何时请求确认。</Text>}>
      <Form form={form} layout="vertical" onFinish={handleSave} initialValues={{ trust: 'ask_dangerous' }}>
        <Form.Item name="trust">
          <Radio.Group className="space-y-3">
            <Radio value="ask_always" className="block">
              <div>
                <div className="font-medium">始终询问</div>
                <Text type="secondary" className="text-xs">
                  每次执行工具前都需要确认。
                </Text>
              </div>
            </Radio>
            <Radio value="ask_dangerous" className="block">
              <div>
                <div className="font-medium">仅询问危险操作</div>
                <Text type="secondary" className="text-xs">
                  仅在可能具有破坏性的操作前确认。
                </Text>
              </div>
            </Radio>
            <Radio value="auto_all" className="block">
              <div>
                <div className="font-medium">全部自动</div>
                <Text type="secondary" className="text-xs">
                  执行所有工具均无需确认。
                </Text>
              </div>
            </Radio>
          </Radio.Group>
        </Form.Item>

        <Form.Item>
          <Button type="primary" htmlType="submit" icon={<SafetyOutlined />} loading={loading}>
            保存
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}

// ============================================================
// Memory Settings
// ============================================================

function MemorySettings() {
  const { message } = App.useApp();
  const [configForm] = Form.useForm();
  const [configLoading, setConfigLoading] = useState(false);
  const [initLoading, setInitLoading] = useState(true);

  useEffect(() => {
    settingsApi
      .getMemoryConfig()
      .then((data) => {
        configForm.setFieldsValue({
          topK: data.top_k,
          compressThreshold: data.compress_threshold,
        });
      })
      .catch((err) => message.error(`加载记忆配置失败：${err.message}`))
      .finally(() => setInitLoading(false));
  }, [configForm]);

  const handleConfigSave = async (values: { topK: number; compressThreshold: number }) => {
    setConfigLoading(true);
    try {
      await settingsApi.updateMemoryConfig({
        top_k: values.topK,
        compress_threshold: values.compressThreshold,
      });
      message.success('记忆配置已保存');
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setConfigLoading(false);
    }
  };

  if (initLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Card title="记忆配置" extra={<Text type="secondary">配置 Agent 如何存储和检索记忆。</Text>}>
        <Form
          form={configForm}
          layout="vertical"
          onFinish={handleConfigSave}
          initialValues={{ topK: 5, compressThreshold: 50 }}
        >
          <Form.Item
            name="topK"
            label="Top-K 检索"
            extra="每次对话注入的相关记忆数量。"
          >
            <Slider min={1} max={20} step={1} marks={{ 1: '1', 10: '10', 20: '20' }} />
          </Form.Item>

          <Form.Item
            name="compressThreshold"
            label="自动压缩阈值"
            extra="当对话历史超过此数量的消息时进行压缩。"
          >
            <InputNumber min={10} max={200} className="w-full" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={configLoading}>
              保存
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
