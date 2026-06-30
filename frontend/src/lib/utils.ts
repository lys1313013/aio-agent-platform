import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge Tailwind classes without conflicts */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Parse <think>...</think> blocks from LLM output.
 * Returns the thinking content (concatenated) and the cleaned text.
 * Handles incomplete tags during streaming when `isStreaming` is true.
 */
export function parseThinkBlocks(
  text: string,
  isStreaming = false,
): { thinking: string; content: string } {
  const thinkRegex = /<think>([\s\S]*?)<\/think>/gi;
  const thinkingParts: string[] = [];
  let cleaned = text.replace(thinkRegex, (_match, inner: string) => {
    thinkingParts.push(inner.trim());
    return '';
  });

  // Handle incomplete <think> tag at the end during streaming
  if (isStreaming) {
    const openIdx = cleaned.lastIndexOf('<think>');
    if (openIdx !== -1) {
      const afterOpen = cleaned.slice(openIdx + '<think>'.length);
      // Only treat as incomplete if there's no closing tag after it
      if (!/<\/think>/i.test(afterOpen)) {
        thinkingParts.push(afterOpen.trim());
        cleaned = cleaned.slice(0, openIdx);
      }
    }
  }

  return {
    thinking: thinkingParts.join('\n\n'),
    content: cleaned.trim(),
  };
}

/** Format a date string to a human-readable relative time */
export function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin}分钟前`;
  if (diffHour < 24) return `${diffHour}小时前`;
  if (diffDay < 7) return `${diffDay}天前`;
  return date.toLocaleDateString();
}
