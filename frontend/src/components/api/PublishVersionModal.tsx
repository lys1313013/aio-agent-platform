import { useState } from 'react';
import { Modal, Input, Form, App } from 'antd';
import { RocketOutlined } from '@ant-design/icons';
import { agentApiApi } from '@/lib/api';
import type { AgentVersion } from '@/lib/types';

interface Props {
  open: boolean;
  agentId: string;
  onClose: () => void;
  onPublished: (version: AgentVersion) => void;
}

export default function PublishVersionModal({ open, agentId, onClose, onPublished }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      setLoading(true);

      const version = await agentApiApi.publishVersion(agentId, {
        version: values.version || undefined,
        changelog: values.changelog || undefined,
      });

      message.success(`版本 ${version.version} 发布成功`);
      onPublished(version);
      form.resetFields();
      onClose();
    } catch (err: any) {
      if (err?.errorFields) return; // form validation error
      message.error(`发布失败: ${err?.message || '未知错误'}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    onClose();
  };

  return (
    <Modal
      title={
        <span className="flex items-center gap-2">
          <RocketOutlined className="text-primary" />
          发布新版本
        </span>
      }
      open={open}
      onOk={handleOk}
      onCancel={handleCancel}
      okText="发布"
      cancelText="取消"
      confirmLoading={loading}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        className="mt-4"
      >
        <Form.Item
          name="version"
          label="版本号"
          extra="留空将自动生成 UUID 前 8 位作为版本号"
        >
          <Input
            placeholder="如 v1.0.0 或留空自动生成"
            maxLength={64}
          />
        </Form.Item>

        <Form.Item
          name="changelog"
          label="版本说明"
          extra="描述本次发布包含的变更（可选）"
        >
          <Input.TextArea
            rows={4}
            placeholder="本次版本的变更说明..."
            maxLength={2000}
            showCount
          />
        </Form.Item>
      </Form>

      <div className="text-xs text-gray-500 bg-gray-50 dark:bg-gray-800/50 rounded-lg p-3 mt-2">
        <p className="font-medium mb-1">发布说明：</p>
        <ul className="list-disc list-inside space-y-0.5">
          <li>发布后将快照当前智能体的完整配置</li>
          <li>新版本将自动成为当前生效版本</li>
          <li>旧版本将自动停用（历史记录保留）</li>
          <li>外部 API 调用将基于新版本配置执行</li>
        </ul>
      </div>
    </Modal>
  );
}
