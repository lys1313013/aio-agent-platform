import { useMemo } from 'react';
import CodeBlock from '../chat/CodeBlock';

interface Props {
  content: string;
}

/**
 * Renders option preview content — supports code blocks (```) and plain markdown text.
 */
export default function OptionPreview({ content }: Props) {
  const { codeBlock, plainText } = useMemo(() => {
    // Check if content is a fenced code block
    const match = content.match(/^```(\w*)\n?([\s\S]*?)```$/);
    if (match) {
      return { codeBlock: { language: match[1] || 'text', code: match[2] }, plainText: null };
    }
    return { codeBlock: null, plainText: content };
  }, [content]);

  if (codeBlock) {
    return (
      <div className="mt-2 rounded-md overflow-hidden border border-border/40">
        <CodeBlock code={codeBlock.code} language={codeBlock.language} />
      </div>
    );
  }

  return (
    <div className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap bg-muted/30 rounded-md p-2">
      {plainText}
    </div>
  );
}
