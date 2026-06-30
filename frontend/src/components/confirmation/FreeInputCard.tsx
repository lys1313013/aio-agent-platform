import { useState } from 'react';
import { Button, Input } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import type { ConfirmationResponse } from '@/lib/types';

interface Props {
  onSubmit: (response: ConfirmationResponse) => void;
}

export default function FreeInputCard({ onSubmit }: Props) {
  const [value, setValue] = useState('');

  return (
    <div className="space-y-3">
      <Input.TextArea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="请输入你的回答..."
        autoSize={{ minRows: 3, maxRows: 8 }}
        onPressEnter={(e) => {
          // Only submit on Ctrl+Enter or Cmd+Enter
          if (e.ctrlKey || e.metaKey) {
            if (value.trim()) {
              onSubmit({
                status: 'approved',
                user_input: value.trim(),
              });
            }
          }
        }}
      />

      <div className="flex justify-end gap-2">
        <Button size="small" onClick={() => onSubmit({ status: 'rejected' })}>
          跳过
        </Button>
        <Button
          type="primary"
          size="small"
          icon={<SendOutlined />}
          disabled={!value.trim()}
          onClick={() =>
            onSubmit({
              status: 'approved',
              user_input: value.trim(),
            })
          }
        >
          提交
        </Button>
      </div>
    </div>
  );
}
