import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  EditOutlined,
  MessageOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type {
  ConfirmationOption,
  ConfirmationResolvedInfo,
  TableSchema,
} from '@/lib/types';

interface Props {
  mode: string;
  options?: ConfirmationOption[];
  tableSchema?: TableSchema;
  resolved: ConfirmationResolvedInfo;
}

export default function ConfirmationResolved({ mode, options, tableSchema, resolved }: Props) {
  const { status, selected_options, user_input, table_data } = resolved;

  // Get labels for selected options
  const selectedLabels =
    selected_options
      ?.map((id) => options?.find((o) => o.id === id)?.label || id)
      .join('、') || '';

  const config: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    approved: {
      icon: <CheckCircleOutlined className="text-green-500" />,
      color: 'success',
      label:
        mode === 'free_input'
          ? '已回答'
          : mode === 'approve'
            ? '已批准'
            : mode === 'table_input'
              ? '已填写'
              : '已选择',
    },
    rejected: {
      icon: <CloseCircleOutlined className="text-red-500" />,
      color: 'error',
      label: mode === 'approve' ? '已拒绝' : '已跳过',
    },
    modified: {
      icon: <EditOutlined className="text-blue-500" />,
      color: 'processing',
      label: '已提出修改',
    },
    timeout: {
      icon: <ClockCircleOutlined className="text-orange-500" />,
      color: 'warning',
      label: '已超时',
    },
  };

  const c = config[status] || config.timeout;

  return (
    <div className="flex items-start gap-2 text-sm text-muted-foreground py-1">
      {c.icon}
      <div className="flex-1 min-w-0">
        <Tag color={c.color} className="mr-1">
          {c.label}
        </Tag>
        {status === 'approved' && selectedLabels && (
          <span className="font-medium">{selectedLabels}</span>
        )}
        {status === 'approved' && mode === 'table_input' && table_data && table_data.length > 0 && (
          <div className="mt-1 overflow-x-auto">
            <table className="border-collapse text-xs">
              <thead>
                <tr>
                  {(tableSchema?.columns ?? Object.keys(table_data[0]).map((k) => ({ key: k, title: k }))).map((col) => (
                    <th
                      key={col.key}
                      className="border border-gray-200 px-2 py-0.5 text-left font-medium dark:border-gray-700"
                    >
                      {col.title}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table_data.map((row, i) => (
                  <tr key={i}>
                    {(tableSchema?.columns ?? Object.keys(table_data[0]).map((k) => ({ key: k, title: k }))).map((col) => (
                      <td
                        key={col.key}
                        className="border border-gray-200 px-2 py-0.5 dark:border-gray-700"
                      >
                        {String(row[col.key] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {status === 'approved' && mode === 'free_input' && user_input && (
          <span className="flex items-start gap-1 mt-1">
            <MessageOutlined className="mt-0.5 text-xs" />
            <span className="italic">"{user_input}"</span>
          </span>
        )}
        {status === 'approved' && mode !== 'free_input' && user_input && (
          <span className="flex items-start gap-1 mt-1">
            <MessageOutlined className="mt-0.5 text-xs" />
            <span className="italic text-muted-foreground">"{user_input}"</span>
          </span>
        )}
        {status === 'modified' && user_input && (
          <span className="flex items-start gap-1 mt-1">
            <EditOutlined className="mt-0.5 text-xs" />
            <span className="italic">"{user_input}"</span>
          </span>
        )}
        {status === 'rejected' && user_input && (
          <span className="flex items-start gap-1 mt-1">
            <MessageOutlined className="mt-0.5 text-xs" />
            <span className="italic text-muted-foreground">"{user_input}"</span>
          </span>
        )}
        {status === 'timeout' && (
          <span>等待用户响应中</span>
        )}
      </div>
    </div>
  );
}
