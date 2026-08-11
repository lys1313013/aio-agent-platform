import { create } from 'zustand';

export interface WebpagePreview {
  pageId: string;
  title: string;
}

interface WebpagePreviewState {
  /** 当前在对话页右侧内嵌预览的网页；null = 面板关闭 */
  preview: WebpagePreview | null;
  /** 页面是否挂载了预览面板（宠物浮窗等场景没有面板，点击卡片应改为新标签页打开） */
  panelAvailable: boolean;
  openPreview: (preview: WebpagePreview) => void;
  closePreview: () => void;
  setPanelAvailable: (available: boolean) => void;
}

export const useWebpagePreviewStore = create<WebpagePreviewState>((set) => ({
  preview: null,
  panelAvailable: false,
  openPreview: (preview) => set({ preview }),
  closePreview: () => set({ preview: null }),
  setPanelAvailable: (available) => set({ panelAvailable: available }),
}));
