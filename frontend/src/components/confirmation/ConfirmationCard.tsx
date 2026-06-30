import { useState, useCallback } from 'react';
import { QuestionCircleOutlined } from '@ant-design/icons';
import { Tag } from 'antd';
import type {
  ConfirmationOption,
  ConfirmationContext,
  ConfirmationMode,
  ConfirmationResponse,
  ConfirmationResolvedInfo,
  RiskLevel,
  TableSchema,
} from '@/lib/types';
import { confirmationsApi } from '@/lib/api';
import SingleSelectCard from './SingleSelectCard';
import MultiSelectCard from './MultiSelectCard';
import FreeInputCard from './FreeInputCard';
import ApproveCard from './ApproveCard';
import TableInputCard from './TableInputCard';
import ConfirmationResolved from './ConfirmationResolved';

interface Props {
  confirmationId: string;
  question: string;
  mode: ConfirmationMode;
  options?: ConfirmationOption[];
  tableSchema?: TableSchema;
  context: ConfirmationContext;
  resolved?: ConfirmationResolvedInfo;
}

const RISK_COLORS: Record<RiskLevel, string> = {
  low: 'border-blue-300 dark:border-blue-700',
  medium: 'border-amber-400 dark:border-amber-600',
  high: 'border-orange-500 dark:border-orange-600',
  critical: 'border-red-500 dark:border-red-600',
};

export default function ConfirmationCard({
  confirmationId,
  question,
  mode,
  options,
  tableSchema,
  context,
  resolved,
}: Props) {
  const [isSubmitting, setIsSubmitting] = useState(false);

  const riskLevel = (context.risk_level || 'low') as RiskLevel;
  const borderColor = RISK_COLORS[riskLevel];

  // Agent source badge (for multi-agent delegation)
  const agentBadge = context.agent_name ? (
    <Tag color="purple" className="text-xs">
      {context.agent_name}
    </Tag>
  ) : null;

  const handleSubmit = useCallback(
    async (response: ConfirmationResponse) => {
      setIsSubmitting(true);
      try {
        await confirmationsApi.respond(confirmationId, response);
      } catch (err) {
        console.error('Failed to submit confirmation:', err);
      } finally {
        setIsSubmitting(false);
      }
    },
    [confirmationId],
  );

  // Already resolved -> show summary
  if (resolved) {
    return (
      <div className={`rounded-lg border-2 ${borderColor} bg-muted/20 p-3 my-2 opacity-75`}>
        <div className="flex items-start gap-2 mb-1">
          <QuestionCircleOutlined className="text-muted-foreground flex-shrink-0 mt-1" />
          <span className="text-sm font-medium flex-1 min-w-0 whitespace-pre-wrap break-words">{question}</span>
        </div>
        <ConfirmationResolved mode={mode} options={options} tableSchema={tableSchema} resolved={resolved} />
      </div>
    );
  }

  return (
    <div
      className={`rounded-lg border-2 ${borderColor} bg-card p-4 my-2 shadow-sm`}
      style={{ pointerEvents: isSubmitting ? 'none' : 'auto', opacity: isSubmitting ? 0.6 : 1 }}
    >
      {/* Header */}
      <div className="flex items-start gap-2 mb-3 flex-wrap">
        <QuestionCircleOutlined className="text-blue-500 flex-shrink-0 mt-1" />
        <span className="font-medium flex-1 min-w-0 whitespace-pre-wrap break-words">{question}</span>
        {agentBadge}
      </div>

      {/* Interaction mode */}
      {mode === 'single_select' && options && (
        <SingleSelectCard options={options} onSubmit={handleSubmit} />
      )}
      {mode === 'multi_select' && options && (
        <MultiSelectCard options={options} onSubmit={handleSubmit} />
      )}
      {mode === 'free_input' && <FreeInputCard onSubmit={handleSubmit} />}
      {mode === 'approve' && options && (
        <ApproveCard options={options} onSubmit={handleSubmit} />
      )}
      {mode === 'table_input' && tableSchema && (
        <TableInputCard schema={tableSchema} onSubmit={handleSubmit} />
      )}
    </div>
  );
}
