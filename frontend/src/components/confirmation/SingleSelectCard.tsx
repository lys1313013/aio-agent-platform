import { useState } from 'react';
import { Button, Input } from 'antd';
import { EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons';
import type { ConfirmationOption, ConfirmationResponse } from '@/lib/types';
import OptionPreview from './OptionPreview';

interface Props {
  options: ConfirmationOption[];
  onSubmit: (response: ConfirmationResponse) => void;
}

export default function SingleSelectCard({ options, onSubmit }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [expandedPreview, setExpandedPreview] = useState<string | null>(null);
  const [userInput, setUserInput] = useState('');

  const handleSelect = (id: string) => {
    setSelected(prev => prev === id ? null : id);
  };

  return (
    <div className="space-y-2">
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
              onClick={() => handleSelect(option.id)}
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

      {/* Optional text input for additional comments */}
      <Input.TextArea
        value={userInput}
        onChange={(e) => setUserInput(e.target.value)}
        placeholder="可选：补充说明（如选择原因、特殊要求等）"
        autoSize={{ minRows: 2, maxRows: 4 }}
        className="mt-2"
      />

      <div className="flex justify-end gap-2 mt-3 pt-2 border-t border-border/40">
        <Button
          size="small"
          onClick={() =>
            onSubmit({
              status: 'rejected',
              user_input: userInput.trim() || undefined,
            })
          }
        >
          跳过
        </Button>
        <Button
          type="primary"
          size="small"
          onClick={() =>
            onSubmit({
              status: 'approved',
              selected_options: selected ? [selected] : [],
              user_input: userInput.trim() || undefined,
            })
          }
        >
          确认选择
        </Button>
      </div>
    </div>
  );
}
