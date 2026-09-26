import { Link } from 'react-router-dom';

export function sourceIds(metadata: Record<string, unknown>): string[] {
  const raw = metadata.source_session;
  return [...new Set((Array.isArray(raw) ? raw : raw ? [raw] : [])
    .filter((value): value is string => typeof value === 'string'))];
}

export default function MemorySources({ metadata, sessions = [] }: {
  metadata: Record<string, unknown>;
  sessions?: Array<{ id: string; title: string | null }>;
}) {
  const ids = sourceIds(metadata);
  return <div className="mt-2 text-xs text-muted-foreground break-all">
    来源：{ids.length === 0 ? '未记录来源会话（手工记录或旧数据）' : ids.map((id, index) => {
      const session = sessions.find((item) => item.id === id);
      return <span key={id}>{index > 0 && '、'}{session
        ? <Link to={`/chat/${id}`} target="_blank" rel="noreferrer">{session.title || id}</Link>
        : <span title={id}>{id}</span>}</span>;
    })}
  </div>;
}
