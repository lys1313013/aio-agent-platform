import { useState } from 'react';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';
import { Button, App } from 'antd';

interface Props {
  language: string;
  code: string;
}

export default function CodeBlock({ language, code }: Props) {
  const { message } = App.useApp();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    message.success('已复制代码');
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group relative my-2 rounded-lg overflow-hidden bg-gray-900 dark:bg-gray-950">
      <div className="flex items-center justify-between px-4 py-2 bg-gray-800 dark:bg-gray-900 border-b border-gray-700">
        <span className="text-xs text-gray-400">{language}</span>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          onClick={handleCopy}
          className="text-gray-400 hover:text-white"
        >
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
      <pre className="p-4 overflow-x-auto text-sm text-gray-100">
        <code className={`language-${language}`}>{code}</code>
      </pre>
    </div>
  );
}
