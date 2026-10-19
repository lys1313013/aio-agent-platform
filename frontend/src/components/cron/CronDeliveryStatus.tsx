import { Tag, Tooltip } from 'antd';
import type { CronJobRun } from '@/lib/types';

const states: Record<string, { label: string; color?: string }> = {
  pending: { label: '待处理', color: 'processing' },
  sent: { label: '已发送', color: 'green' },
  suppressed: { label: '已静默' },
  not_requested: { label: '未推送' },
  failed: { label: '发送失败', color: 'red' },
  unknown: { label: '结果未知', color: 'orange' },
};

const reasons: Record<string, string> = {
  no_channel: '未绑定推送渠道',
  execution_failed: '执行未完成，未推送结果',
  silent_token: '本次明确要求静默',
};

type DeliveryRecord = Pick<CronJobRun, 'delivery_status' | 'delivery_reason' | 'delivery_error'>;

export default function CronDeliveryStatus({ run, showReason = false }: {
  run: DeliveryRecord;
  showReason?: boolean;
}) {
  const state = states[run.delivery_status] || states.unknown;
  const reason = run.delivery_error
    || (run.delivery_reason && (reasons[run.delivery_reason] || run.delivery_reason));
  return (
    <>
      <Tooltip title={reason}>
        <Tag color={state.color}>{state.label}</Tag>
      </Tooltip>
      {showReason && reason && <div className="mt-1 break-words text-sm">{reason}</div>}
    </>
  );
}
