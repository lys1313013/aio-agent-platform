import { useCallback, useEffect, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { App, Button, Drawer, Empty, Select, Segmented, Table, Tag, Typography } from 'antd';
import { cronJobsApi } from '@/lib/api';
import type { CronJob, CronJobRun } from '@/lib/types';
import { useAuthStore } from '@/stores/authStore';
import { Navigate } from 'react-router-dom';

const { Text, Paragraph } = Typography;

const PAGE_SIZE = 20;

const formatDuration = (ms: number | null): string => {
  if (ms == null) return '-';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
};

const formatTime = (t: string | null): string => {
  if (!t) return '-';
  return new Date(t).toLocaleString('zh-CN');
};

const runStatusTag = (status: string) => {
  if (status === 'success') return <Tag color="green">成功</Tag>;
  if (status === 'failed') return <Tag color="red">失败</Tag>;
  return <Tag color="processing">运行中</Tag>;
};

export default function CronJobRunsPage() {
  const { message } = App.useApp();
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === 'admin' || role === 'superadmin';

  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [runs, setRuns] = useState<CronJobRun[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [jobId, setJobId] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string>('all');
  const [detail, setDetail] = useState<CronJobRun | null>(null);

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    try {
      const result = await cronJobsApi.runsAll({
        job_id: jobId,
        status: status === 'all' ? undefined : status,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setRuns(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(err.message || '加载执行记录失败');
    } finally {
      setLoading(false);
    }
  }, [jobId, status, page]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  useEffect(() => {
    cronJobsApi
      .list({ limit: 200 })
      .then((res) => setJobs(res.items))
      .catch(() => {});
  }, []);

  const changeJob = (v: string | undefined) => {
    setJobId(v);
    setPage(1);
  };

  const changeStatus = (v: string) => {
    setStatus(v);
    setPage(1);
  };

  const renderResult = (record: CronJobRun) => {
    if (record.status === 'success') {
      return <span className="line-clamp-2 text-xs">{record.output || '(空输出)'}</span>;
    }
    if (record.status === 'failed') {
      return <span className="line-clamp-2 text-xs text-red-500">{record.error || '未知错误'}</span>;
    }
    return <span className="text-xs text-muted-foreground">执行中…</span>;
  };

  const columns = [
    {
      title: '任务',
      dataIndex: 'job_name',
      key: 'job_name',
      width: 220,
      render: (name: string | null | undefined) =>
        name ? (
          <Text className="text-sm">{name}</Text>
        ) : (
          <Text type="secondary" className="text-sm">已删除</Text>
        ),
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 170,
      render: formatTime,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: runStatusTag,
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 90,
      render: formatDuration,
    },
    {
      title: '结果',
      key: 'result',
      render: (_: unknown, record: CronJobRun) => renderResult(record),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, record: CronJobRun) => (
        <Button size="small" type="link" onClick={() => setDetail(record)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header + filters */}
      <div className="flex flex-wrap items-center gap-3 px-6 py-4">
        <div className="mr-2">
          <h2 className="text-lg font-bold text-foreground">执行记录</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            查看所有定时任务的每次执行情况
          </p>
        </div>
        <Select
          placeholder="按任务筛选"
          allowClear
          showSearch
          optionFilterProp="label"
          className="w-52"
          value={jobId}
          onChange={changeJob}
          notFoundContent="无匹配任务"
          options={jobs.map((j) => ({ value: j.id, label: j.name }))}
        />
        <Segmented
          value={status}
          onChange={changeStatus}
          options={[
            { label: '全部', value: 'all' },
            { label: '成功', value: 'success' },
            { label: '失败', value: 'failed' },
            { label: '运行中', value: 'running' },
          ]}
        />
        <Button icon={<ReloadOutlined />} onClick={() => fetchRuns()}>
          刷新
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        <Table<CronJobRun>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={runs}
          size="middle"
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
            onChange: setPage,
          }}
          locale={{ emptyText: <Empty description="暂无执行记录" /> }}
        />
      </div>

      {/* Detail Drawer */}
      <Drawer
        title="执行详情"
        open={!!detail}
        onClose={() => setDetail(null)}
        width={640}
        destroyOnHidden
      >
        {detail && (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Text strong className="text-base">
                {detail.job_name || '（任务已删除）'}
              </Text>
              {runStatusTag(detail.status)}
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <Text type="secondary">开始时间</Text>
                <div>{formatTime(detail.started_at)}</div>
              </div>
              <div>
                <Text type="secondary">结束时间</Text>
                <div>{formatTime(detail.finished_at)}</div>
              </div>
              <div>
                <Text type="secondary">耗时</Text>
                <div>{formatDuration(detail.duration_ms)}</div>
              </div>
              <div>
                <Text type="secondary">会话 ID</Text>
                <div className="break-all font-mono text-xs">{detail.session_id || '-'}</div>
              </div>
            </div>

            {detail.status === 'success' && (
              <div>
                <Text type="secondary">输出</Text>
                <Paragraph
                  className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-muted p-3 text-sm"
                  copyable={{ text: detail.output || '' }}
                >
                  {detail.output || '(空输出)'}
                </Paragraph>
              </div>
            )}

            {detail.status === 'failed' && detail.error && (
              <div>
                <Text type="secondary">错误</Text>
                <Paragraph className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-muted p-3 text-sm text-red-500">
                  {detail.error}
                </Paragraph>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}
