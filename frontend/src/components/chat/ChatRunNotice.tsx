import { Alert, Button } from 'antd';
import type { ChatRunInfo } from '@/lib/api';

export default function ChatRunNotice({ run, stopping, onResume }: {
  run: ChatRunInfo | null;
  stopping: boolean;
  onResume: () => void;
}) {
  if (!run || run.status === 'completed') return null;
  const pending = run.uncertain_tools.length > 0;
  const message = stopping ? '正在停止任务并保存进度…'
    : run.status === 'running' ? '任务在后台执行，刷新或离开页面后仍会继续。'
    : run.status === 'interrupted' ? '任务因服务中断而停止，已恢复保存的进度。'
    : run.status === 'stopped' ? '任务已停止，已保存执行进度。' : '任务执行失败，已保存执行进度。';
  return <div className="mx-auto max-w-3xl w-full px-4 pb-2">
    <Alert type={run.status === 'running' ? 'info' : 'warning'} showIcon message={message}
      description={run.status !== 'running' && pending
        ? `以下工具没有确定的成功结果：${[...new Set(run.uncertain_tools)].join('、')}。请先核对外部操作结果，再发送消息说明下一步；不能自动重试。`
        : undefined}
      action={run.can_resume && !stopping ? <Button size="small" onClick={onResume}>继续任务</Button> : undefined}
    />
  </div>;
}
