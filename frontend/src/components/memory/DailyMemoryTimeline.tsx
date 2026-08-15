import { useCallback, useEffect, useState } from 'react';
import { App, Button, DatePicker, Empty, Popconfirm, Spin, Tag, Typography } from 'antd';
import {
  CalendarOutlined,
  DeleteOutlined,
  ReloadOutlined,
  RightOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import { dailyMemoriesApi } from '@/lib/api';
import type { DailyHighlightType, DailyMemory } from '@/lib/types';

const { Text } = Typography;

const HIGHLIGHT_CONFIG: Record<DailyHighlightType, { label: string; color: string }> = {
  decision: { label: '决定', color: 'blue' },
  todo: { label: '待办', color: 'orange' },
  fact: { label: '事实', color: 'green' },
  event: { label: '事件', color: 'purple' },
};

function formatDateLabel(iso: string): { primary: string; secondary: string } {
  const d = dayjs(iso);
  const today = dayjs().startOf('day');
  const diffDays = today.diff(d.startOf('day'), 'day');
  const primary = d.format('M月D日 dddd').replace('dddd', ['周日','周一','周二','周三','周四','周五','周六'][d.day()]);
  if (diffDays === 0) return { primary: `今天 · ${primary}`, secondary: d.format('YYYY') };
  if (diffDays === 1) return { primary: `昨天 · ${primary}`, secondary: d.format('YYYY') };
  return { primary, secondary: d.format('YYYY') };
}

export default function DailyMemoryTimeline() {
  const { message } = App.useApp();
  const [items, setItems] = useState<DailyMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [jumpDate, setJumpDate] = useState<Dayjs | null>(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const list = await dailyMemoriesApi.list({ limit: 90 });
      setItems(list);
      setSelectedDate((prev) =>
        prev && list.some((m) => m.date === prev) ? prev : (list[0]?.date ?? null),
      );
    } catch {
      message.error('加载每日记忆失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  const selected = items.find((m) => m.date === selectedDate) ?? null;

  const handleJump = async (value: Dayjs | null) => {
    setJumpDate(value);
    if (!value) return;
    const day = value.format('YYYY-MM-DD');
    if (items.some((m) => m.date === day)) {
      setSelectedDate(day);
      return;
    }
    // 列表里没有(可能超出最近 90 条),精确查一次
    setLoading(true);
    try {
      const found = await dailyMemoriesApi.list({ date: day });
      if (found.length > 0) {
        setItems((prev) =>
          [...prev, found[0]].sort((a, b) => (a.date < b.date ? 1 : -1)),
        );
        setSelectedDate(day);
      } else {
        message.info('这一天没有记忆记录');
      }
    } catch {
      message.error('查询失败');
    } finally {
      setLoading(false);
    }
  };

  const handleRegenerate = async () => {
    if (!selectedDate) return;
    setRegenerating(true);
    try {
      const regenerated = await dailyMemoriesApi.regenerate(selectedDate);
      setItems((prev) =>
        prev.some((m) => m.date === regenerated.date)
          ? prev.map((m) => (m.date === regenerated.date ? regenerated : m))
          : [regenerated, ...prev].sort((a, b) => (a.date < b.date ? 1 : -1)),
      );
      message.success('已重新生成');
    } catch {
      message.error('重新生成失败:该日期可能没有可合并的会话内容');
    } finally {
      setRegenerating(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedDate) return;
    try {
      await dailyMemoriesApi.delete(selectedDate);
      setItems((prev) => {
        const next = prev.filter((m) => m.date !== selectedDate);
        setSelectedDate(next[0]?.date ?? null);
        return next;
      });
      message.success('已删除(原始会话摘要不受影响,可重新生成)');
    } catch {
      message.error('删除失败');
    }
  };

  return (
    <div className="flex gap-6">
      {/* 左侧时间线 */}
      <div className="w-56 shrink-0">
        <div className="mb-3">
          <DatePicker
            value={jumpDate}
            onChange={handleJump}
            placeholder="跳转到某一天"
            className="w-full"
            allowClear
          />
        </div>
        <Spin spinning={loading}>
          {items.length === 0 && !loading ? (
            <div className="rounded-xl border border-border bg-card py-8">
              <Empty
                image={<CalendarOutlined className="text-4xl text-muted-foreground/30" />}
                styles={{ image: { height: 40 } }}
                description={
                  <Text type="secondary" className="text-xs">
                    暂无每日记忆,
                    <br />
                    对话后自动生成
                  </Text>
                }
              />
            </div>
          ) : (
            <div className="relative space-y-1 pl-4 before:absolute before:left-[5px] before:top-2 before:bottom-2 before:w-px before:bg-border">
              {items.map((item) => {
                const active = item.date === selectedDate;
                const label = formatDateLabel(item.date);
                return (
                  <button
                    key={item.date}
                    type="button"
                    onClick={() => setSelectedDate(item.date)}
                    className={`relative flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors ${
                      active
                        ? 'bg-primary/10 text-primary'
                        : 'text-foreground hover:bg-primary/5'
                    }`}
                  >
                    <span
                      className={`absolute -left-[15px] h-2.5 w-2.5 rounded-full border-2 ${
                        active
                          ? 'border-primary bg-primary'
                          : 'border-border bg-card'
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{label.primary}</span>
                      <span className="block text-xs text-muted-foreground">{label.secondary}</span>
                    </span>
                    {active && <RightOutlined className="text-xs" />}
                  </button>
                );
              })}
            </div>
          )}
        </Spin>
      </div>

      {/* 右侧详情 */}
      <div className="min-w-0 flex-1">
        {selected ? (
          <div className="rounded-xl border border-border bg-card p-6">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">{selected.date} 的记忆</h2>
                <Text type="secondary" className="text-xs">
                  更新于 {dayjs(selected.updated_at).format('M月D日 HH:mm')}
                  {selected.source_session_ids.length > 0 &&
                    ` · 来自 ${selected.source_session_ids.length} 个会话`}
                </Text>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={regenerating}
                  onClick={handleRegenerate}
                >
                  重新生成
                </Button>
                <Popconfirm
                  title="确定删除这一天的记忆吗?"
                  description="原始会话摘要不受影响,之后可重新生成。"
                  onConfirm={handleDelete}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                >
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </div>
            </div>

            {selected.highlights.length > 0 && (
              <div className="mb-4 flex flex-wrap gap-2">
                {selected.highlights.map((h, i) => {
                  const cfg = HIGHLIGHT_CONFIG[h.type] ?? { label: h.type, color: 'default' };
                  return (
                    <Tag key={i} color={cfg.color} className="!mr-0">
                      {cfg.label} · {h.text}
                    </Tag>
                  );
                })}
              </div>
            )}

            <div className="prose prose-sm max-w-none text-foreground [&_li]:my-0.5 [&_ul]:pl-5">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.content}</ReactMarkdown>
            </div>
          </div>
        ) : (
          !loading && (
            <div className="rounded-xl border border-border bg-card py-16">
              <Empty description="选择左侧日期查看当天的记忆" />
            </div>
          )
        )}
      </div>
    </div>
  );
}
