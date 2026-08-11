import { create } from 'zustand';

export interface WebpagePreview {
  pageId: string;
  title: string;
}

interface WebpagePreviewState {
  /** 当前在对话页右侧内嵌预览的网页；null = 面板关闭 */
  preview: WebpagePreview | null;
  openPreview: (preview: WebpagePreview) => void;
  closePreview: () => void;
}

export const useWebpagePreviewStore = create<WebpagePreviewState>((set) => ({
  preview: null,
  openPreview: (preview) => set({ preview }),
  closePreview: () => set({ preview: null }),
}));
