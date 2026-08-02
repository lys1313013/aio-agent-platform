import { PlusOutlined, RobotOutlined } from '@ant-design/icons';
import type { Agent, Message, StreamingState } from '@/lib/types';
import ChatMessage from './ChatMessage';
import StreamingMessage from './StreamingMessage';

interface Props {
  messages: Message[];
  streaming: StreamingState;
  agent?: Agent | null;
  onNewChat: () => void;
  onEditResend?: (content: string) => void;
}

export default function MessageList({ messages, streaming, agent, onNewChat, onEditResend }: Props) {
  if (messages.length === 0 && !streaming.isStreaming) {
    const title = agent ? `欢迎使用 ${agent.name}` : '欢迎使用智能体平台';
    const subtitle = agent?.description || '发送消息开始对话';

    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="flex max-w-md flex-col items-center text-center px-6">
          {/* Logo */}
          <div className="mb-6">
            <RobotOutlined className="text-5xl text-primary" />
          </div>

          {/* Title */}
          <h2 className="mb-2 text-xl font-bold tracking-tight text-foreground">
            {title}
          </h2>
          <p className="mb-8 text-sm text-muted-foreground">
            {subtitle}
          </p>

          {/* Welcome message bubble */}
          {agent?.welcome_message && (
            <div className="mb-6 max-w-sm rounded-2xl bg-muted/60 px-5 py-3 text-sm leading-relaxed text-foreground/80 shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
              {agent.welcome_message}
            </div>
          )}

          {/* New chat button */}
          <button
            onClick={onNewChat}
            className="mt-8 flex items-center gap-2 rounded-lg bg-brand-gradient px-5 py-2.5 text-sm font-semibold text-white shadow-brand transition-all hover:shadow-brand-lg hover:-translate-y-[1px] active:translate-y-0"
          >
            <PlusOutlined />
            <span>开始新对话</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} onEditResend={onEditResend} />
        ))}
        {streaming.isStreaming && <StreamingMessage streaming={streaming} />}
      </div>
    </div>
  );
}
