import { useState, useEffect, useCallback } from 'react';
import {
  ClockCircleOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import {
  Form,
  Input,
  Button,
  Card,
  Spin,
  App,
  Modal,
  Tag,
  Popconfirm,
  Switch,
  Empty,
  Tooltip,
  Typography,
  Select,
} from 'antd';
import { cronJobsApi, agentsApi, channelsApi } from '@/lib/api';
import type { CronJob, Agent, Channel } from '@/lib/types';
import { useAuthStore } from '@/stores/authStore';
import { Navigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { TextArea } = Input;

export default function CronJobsPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';

  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingJob, setEditingJob] = useState<CronJob | null>(null);
  const [form] = Form.useForm();

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [result, agentList, channelList] = await Promise.all([
        cronJobsApi.list(),
        agentsApi.adminList(),
        channelsApi.list(),
      ]);
      setJobs(result.items);
      setAgents(agentList);
      setChannels(channelList);
    } catch (err: any) {
      message.error(err.message || '加载数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const openModal = (job?: CronJob) => {
    if (job) {
      setEditingJob(job);
      form.setFieldsValue({
        name: job.name,
        agent_id: job.agent_id || undefined,
        cron_expr: job.cron_expr || '',
        run_at: job.run_at ? job.run_at.slice(0, 16) : '',
        message: job.message || '',
        task_config_json: job.task_config && Object.keys(job.task_config).length > 0
          ? JSON.stringify(job.task_config, null, 2)
          : '',
        channel_id: job.channel_id || undefined,
        is_active: job.is_active,
      });
    } else {
      setEditingJob(null);
      form.resetFields();
      form.setFieldsValue({ is_active: true });
    }
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      let taskConfig: Record<string, unknown> = {};
      if (values.task_config_json) {
        try {
          taskConfig = JSON.parse(values.task_config_json);
        } catch {
          message.error('高级配置 JSON 格式无效');
          return;
        }
      }

      const payload = {
        name: values.name,
        agent_id: values.agent_id || null,
        cron_expr: values.cron_expr || null,
        run_at: values.run_at ? new Date(values.run_at).toISOString() : null,
        message: values.message || null,
        task_config: taskConfig,
        channel_id: values.channel_id || null,
        is_active: values.is_active,
      };

      if (editingJob) {
        await cronJobsApi.update(editingJob.id, payload);
        message.success('任务已更新');
      } else {
        await cronJobsApi.create(payload);
        message.success('任务已创建');
      }
      setModalOpen(false);
      fetchData();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.message || '操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await cronJobsApi.delete(id);
      message.success('任务已删除');
      fetchData();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  const handleToggle = async (job: CronJob) => {
    try {
      await cronJobsApi.update(job.id, { is_active: !job.is_active });
      message.success(job.is_active ? '任务已暂停' : '任务已启用');
      fetchData();
    } catch (err: any) {
      message.error(err.message || '操作失败');
    }
  };

  const getAgentName = (agentId: string | null): string => {
    if (!agentId) return '';
    const agent = agents.find((a) => a.id === agentId);
    return agent ? agent.name : agentId.slice(0, 8) + '...';
  };

  const getChannelName = (channelId: string | null): string => {
    if (!channelId) return '';
    const channel = channels.find((c) => c.id === channelId);
    return channel ? channel.name : channelId.slice(0, 8) + '...';
  };

  const cronToLabel = (expr: string | null | undefined): string => {
    if (!expr) return '';
    const parts = expr.trim().split(/\s+/);
    if (parts.length !== 5) return expr;
    const [min, hour, day, month, dow] = parts;
    if (min === '*' && hour === '*' && day === '*' && month === '*' && dow === '*') {
      return '每分钟';
    }
    if (hour === '*' && day === '*' && month === '*' && dow === '*') {
      return `每 ${min} 分钟`;
    }
    if (day === '*' && month === '*' && dow === '*') {
      return `每天 ${hour}:${min.padStart(2, '0')}`;
    }
    if (month === '*' && dow === '*') {
      return `每月 ${day} 日 ${hour}:${min.padStart(2, '0')}`;
    }
    if (dow !== '*') {
      const days = ['日', '一', '二', '三', '四', '五', '六'];
      const dows = dow.split(',').map((d: string) => days[parseInt(d)] || d).join(',');
      return `每周${dows} ${hour}:${min.padStart(2, '0')}`;
    }
    return expr;
  };

  const formatTime = (t: string | null): string => {
    if (!t) return '从未执行';
    return new Date(t).toLocaleString('zh-CN');
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4">
        <div>
          <h2 className="text-lg font-bold text-foreground">定时任务</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            管理定时执行的自动化任务
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
          新建任务
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {jobs.length === 0 ? (
          <Card className="flex items-center justify-center py-16">
            <Empty description="暂无定时任务">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal()}>
                新建任务
              </Button>
            </Empty>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {jobs.map((job) => (
              <Card
                key={job.id}
                className={cn(
                  'transition-all hover:shadow-md',
                  !job.is_active && 'opacity-60',
                )}
                title={
                  <div className="flex items-center gap-2">
                    <ClockCircleOutlined
                      className={job.is_active ? 'text-green-500' : 'text-muted-foreground'}
                    />
                    <Text strong className="max-w-[180px] truncate">
                      {job.name}
                    </Text>
                  </div>
                }
                extra={
                  <div className="flex items-center gap-1">
                    <Tooltip title={job.is_active ? '暂停' : '启用'}>
                      <Button
                        size="small"
                        type="text"
                        icon={job.is_active ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                        onClick={() => handleToggle(job)}
                      />
                    </Tooltip>
                    <Button
                      size="small"
                      type="text"
                      icon={<EditOutlined />}
                      onClick={() => openModal(job)}
                    />
                    <Popconfirm
                      title="确定删除此任务？"
                      onConfirm={() => handleDelete(job.id)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                    >
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </div>
                }
              >
                <div className="space-y-2">
                  {/* Agent */}
                  {job.agent_id && (
                    <div className="flex items-center gap-1.5">
                      <RobotOutlined className="text-xs text-muted-foreground" />
                      <Text className="text-sm">{getAgentName(job.agent_id)}</Text>
                    </div>
                  )}

                  {/* Schedule info */}
                  <div>
                    {job.cron_expr ? (
                      <Tag color="blue">{cronToLabel(job.cron_expr)}</Tag>
                    ) : job.run_at ? (
                      <Tag color="orange">单次: {formatTime(job.run_at)}</Tag>
                    ) : (
                      <Tag>无调度</Tag>
                    )}
                    <Tag color={job.is_active ? 'green' : 'default'}>
                      {job.is_active ? '运行中' : '已暂停'}
                    </Tag>
                    {job.channel_id && (
                      <Tag color="purple">推送: {getChannelName(job.channel_id)}</Tag>
                    )}
                  </div>

                  {/* Cron expression raw */}
                  {job.cron_expr && (
                    <div>
                      <Text type="secondary" className="text-xs">{job.cron_expr}</Text>
                    </div>
                  )}

                  {/* Message preview */}
                  {job.message && (
                    <div>
                      <Text type="secondary" className="text-xs line-clamp-2">
                        {job.message}
                      </Text>
                    </div>
                  )}

                  {/* Last run */}
                  <div>
                    <Text type="secondary" className="text-xs">
                      上次执行: {formatTime(job.last_run_at)}
                    </Text>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Create/Edit Modal */}
      <Modal
        title={editingJob ? '编辑任务' : '新建任务'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
        width={600}
      >
        <Form form={form} layout="vertical" initialValues={{ is_active: true }}>
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="例如：每日早报推送" maxLength={256} />
          </Form.Item>

          <Form.Item
            name="agent_id"
            label="关联智能体"
            tooltip="选择此任务要触发的智能体"
          >
            <Select
              placeholder="选择智能体（可选）"
              allowClear
              showSearch
              optionFilterProp="label"
              notFoundContent={agents.length === 0 ? '暂无智能体，请先创建智能体' : '无匹配结果'}
              options={agents.map((a) => ({
                value: a.id,
                label: a.name,
              }))}
            />
          </Form.Item>

          <Form.Item
            name="message"
            label="发送消息"
            tooltip="任务触发时发送给智能体的消息内容"
          >
            <TextArea rows={3} placeholder="例如：请生成今日的日报总结" />
          </Form.Item>

          <Form.Item
            name="cron_expr"
            label="Cron 表达式"
            tooltip="标准 5 字段 cron: 分 时 日 月 周。留空则使用单次执行时间。"
          >
            <Input placeholder="0 9 * * * (每天 9:00)" />
          </Form.Item>

          <Form.Item
            name="run_at"
            label="单次执行时间"
            tooltip="仅在不填 cron 表达式时生效。到期执行一次后自动停用。"
          >
            <Input type="datetime-local" />
          </Form.Item>

          <Form.Item
            name="channel_id"
            label="推送渠道"
            tooltip="任务执行完成后，将结果推送到你在该渠道绑定的账号（需先在渠道中完成账号绑定）"
          >
            <Select
              placeholder="不推送（可选）"
              allowClear
              showSearch
              optionFilterProp="label"
              notFoundContent={channels.length === 0 ? '暂无渠道，请先在渠道管理中创建' : '无匹配结果'}
              options={channels.map((c) => ({
                value: c.id,
                label: `${c.name} (${c.channel_type})`,
                disabled: c.status !== 'enabled',
              }))}
            />
          </Form.Item>

          <Form.Item
            name="task_config_json"
            label="高级配置 (JSON)"
            tooltip="可选的额外 JSON 配置"
          >
            <TextArea rows={3} placeholder='留空即可，或输入 {"key": "value"}' />
          </Form.Item>

          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
