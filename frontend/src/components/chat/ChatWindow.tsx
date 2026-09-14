import type { ReactNode } from 'react';
import { Spin } from 'antd';
import type { Message } from '@/lib/types';
import ChatInput, { type ChatInputProps } from './ChatInput';
import MessageList, { type MessageListProps } from './MessageList';

interface Props<T extends Message> {
  messages: MessageListProps<T>;
  input: ChatInputProps;
  loading?: boolean;
  status?: ReactNode;
}

/** Shared conversation surface; pages own transport and participant scheduling. */
export default function ChatWindow<T extends Message>({ messages, input, loading, status }: Props<T>) {
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
    {loading && messages.messages.length === 0
      ? <div className="flex flex-1 items-center justify-center"><Spin size="large" /></div>
      : <MessageList {...messages} />}
    {status}
    <ChatInput {...input} />
  </div>;
}
