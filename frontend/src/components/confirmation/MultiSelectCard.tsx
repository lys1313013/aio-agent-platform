import { useState } from 'react';
import { Button, Checkbox, Input } from 'antd';
import type { ConfirmationOption, ConfirmationResponse } from '@/lib/types';

interface Props {
  options: ConfirmationOption[];
  onSubmit: (response: ConfirmationResponse) => void;
}

export default function MultiSelectCard({ options, onSubmit }: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [userInput, setUserInput] = useState('');

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  };

  const selectAll = () => {
    setSelected(options.map((o) => o.id));
  };

  const selectNone = () => {
    setSelected([]);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          已选择 {selected.length} / {options.length} 项
        </span>
        <div className="flex gap-2">
          <Button type="link" size="small" onClick={selectAll}>
            全选
          </Button>
          <Button type="link" size="small" onClick={selectNone}>
            清空
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        {options.map((option) => {
          const isChecked = selected.includes(option.id);

          return (
            <div
              key={option.id}
              className={`rounded-lg border p-3 cursor-pointer transition-all ${
                isChecked
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30'
                  : 'border-border/60 hover:border-border'
              }`}
              onClick={() => toggle(option.id)}
            >
              <Checkbox
                checked={isChecked}
                className="w-full [&_.ant-checkbox-label]:min-w-0 [&_.ant-checkbox-label]:overflow-hidden"
              >
                <div className="ml-1 min-w-0 break-words">
                  <span className="font-medium break-words">{option.label}</span>
                  {option.description && (
                    <p className="text-sm text-muted-foreground mt-1 mb-0 break-words">
                      {option.description}
                    </p>
                  )}
                </div>
              </Checkbox>
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
          disabled={selected.length === 0}
          onClick={() =>
            onSubmit({
              status: 'approved',
              selected_options: selected,
              user_input: userInput.trim() || undefined,
            })
          }
        >
          确认选择 ({selected.length})
        </Button>
      </div>
    </div>
  );
}
