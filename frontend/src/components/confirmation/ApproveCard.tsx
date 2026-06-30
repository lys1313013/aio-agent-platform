import { useState } from 'react';
import { Button, Input } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  EditOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons';
import type { ConfirmationOption, ConfirmationResponse } from '@/lib/types';
import OptionPreview from './OptionPreview';

interface Props {
  options: ConfirmationOption[];
  onSubmit: (response: ConfirmationResponse) => void;
}

export default function ApproveCard({ options, onSubmit }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [showModify, setShowModify] = useState(false);
  const [modifyText, setModifyText] = useState('');
  const [userInput, setUserInput] = useState('');
  const [expandedPreview, setExpandedPreview] = useState<string | null>(null);

  return (
    <div>
      {/* Radio-selectable options */}
      <div className="space-y-2">
        {options.map((option) => {
          const isSelected = selected === option.id;
          const isExpanded = expandedPreview === option.id;

          return (
            <div
              key={option.id}
              className={`rounded-lg border p-3 cursor-pointer transition-all ${
                isSelected
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30'
                  : 'border-border/60 hover:border-border'
              }`}
              onClick={() => setSelected(prev => prev === option.id ? null : option.id)}
            >
              <div className="flex items-start gap-2">
                <span className={`mt-[3px] h-4 w-4 rounded-full border-2 flex-shrink-0 flex items-center justify-center transition-colors ${
                  isSelected ? 'border-blue-500' : 'border-gray-300'
                }`}>
                  {isSelected && <span className="h-2 w-2 rounded-full bg-blue-500" />}
                </span>
                <div className="min-w-0 break-words">
                  <span className="font-medium break-words">{option.label}</span>
                  {option.description && (
                    <p className="text-sm text-muted-foreground mt-1 mb-0 break-words">
                      {option.description}
                    </p>
                  )}
                </div>
              </div>

              {option.preview && (
                <div className="mt-2 ml-6">
                  <Button
                    type="link"
                    size="small"
                    icon={isExpanded ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedPreview(isExpanded ? null : option.id);
                    }}
                  >
                    {isExpanded ? '收起预览' : '查看预览'}
                  </Button>
                  {isExpanded && <OptionPreview content={option.preview} />}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Optional text input for additional comments (always visible) */}
      {!showModify && (
        <Input.TextArea
          value={userInput}
          onChange={(e) => setUserInput(e.target.value)}
          placeholder="可选：补充说明（如补充要求、注意事项等）"
          autoSize={{ minRows: 2, maxRows: 4 }}
          className="mt-2"
        />
      )}

      {/* Action area */}
      {showModify ? (
        <div className="mt-3 space-y-2">
          <Input.TextArea
            value={modifyText}
            onChange={(e) => setModifyText(e.target.value)}
            placeholder="请描述你希望修改的内容..."
            autoSize={{ minRows: 2, maxRows: 6 }}
          />
          <div className="flex justify-end gap-2">
            <Button size="small" onClick={() => setShowModify(false)}>
              取消
            </Button>
            <Button
              type="primary"
              size="small"
              disabled={!modifyText.trim()}
              onClick={() =>
                onSubmit({
                  status: 'modified',
                  user_input: modifyText.trim(),
                })
              }
            >
              提交修改意见
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex justify-end gap-2 mt-3 pt-2 border-t border-border/40">
          <Button
            size="small"
            danger
            icon={<CloseCircleOutlined />}
            onClick={() =>
              onSubmit({
                status: 'rejected',
                user_input: userInput.trim() || undefined,
              })
            }
          >
            拒绝
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => setShowModify(true)}
          >
            提出修改
          </Button>
          <Button
            type="primary"
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={() =>
              onSubmit({
                status: 'approved',
                selected_options: selected ? [selected] : [],
                user_input: userInput.trim() || undefined,
              })
            }
          >
            批准执行
          </Button>
        </div>
      )}
    </div>
  );
}
