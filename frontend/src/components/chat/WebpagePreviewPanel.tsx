import { useEffect, useState } from 'react';
import { CloseOutlined, ExportOutlined, GlobalOutlined } from '@ant-design/icons';
import { App, Button, Spin, Tooltip } from 'antd';
import { webpagesApi } from '@/lib/api';
import { useWebpagePreviewStore } from '@/stores/webpagePreviewStore';

/**
 * 对话页右侧的网页内嵌预览面板。
 *
 * 网页是智能体生成的不可信 HTML：通过限时令牌 URL 加载，且 iframe 使用
 * sandbox="allow-scripts"（不授 allow-same-origin），与主站登录态双重隔离。
 */
export default function WebpagePreviewPanel() {
  const { preview, closePreview } = useWebpagePreviewStore();
  const { message } = App.useApp();
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!preview) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    webpagesApi
      .getAccess(preview.pageId)
      .then(({ url: freshUrl }) => {
        if (!cancelled) setUrl(freshUrl);
      })
      .catch(() => {
        if (!cancelled) message.error('网页加载失败，可能已被删除');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [preview, message]);

  if (!preview) return null;

  return (
    <div className="flex w-[45%] min-w-[360px] max-w-[720px] flex-col border-l border-border bg-card">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <GlobalOutlined className="text-primary flex-shrink-0" />
        <span className="flex-1 truncate text-sm font-medium" title={preview.title}>
          {preview.title}
        </span>
        <Tooltip title="新标签页打开">
          <Button
            size="small"
            type="text"
            icon={<ExportOutlined />}
            disabled={!url}
            onClick={() => url && window.open(url, '_blank', 'noopener')}
          />
        </Tooltip>
        <Tooltip title="关闭预览">
          <Button size="small" type="text" icon={<CloseOutlined />} onClick={closePreview} />
        </Tooltip>
      </div>
      <div className="relative flex-1">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Spin />
          </div>
        )}
        {url && (
          <iframe
            key={url}
            src={url}
            title={preview.title}
            sandbox="allow-scripts"
            className="h-full w-full border-0 bg-white"
          />
        )}
      </div>
    </div>
  );
}
