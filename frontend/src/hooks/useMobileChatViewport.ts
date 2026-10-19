import { useEffect, useState, type RefObject } from 'react';

// Keep this breakpoint aligned with PortalChatPage.css.
const MOBILE_QUERY = '(max-width: 767px)';

export function useMobileChatViewport(ref: RefObject<HTMLDivElement>) {
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);

  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const viewport = window.visualViewport;
    const element = ref.current;
    const update = () => {
      setIsMobile(media.matches);
      // visualViewport also shrinks for the iOS keyboard, unlike 100dvh.
      // Leave pinch zoom to the browser instead of resizing the page around it.
      if (media.matches && viewport && viewport.scale === 1) {
        element?.style.setProperty('--chat-viewport-height', `${viewport.height}px`);
        element?.style.setProperty('--chat-viewport-top', `${viewport.offsetTop}px`);
      } else {
        element?.style.removeProperty('--chat-viewport-height');
        element?.style.removeProperty('--chat-viewport-top');
      }
    };
    update();
    media.addEventListener('change', update);
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    return () => {
      media.removeEventListener('change', update);
      viewport?.removeEventListener('resize', update);
      viewport?.removeEventListener('scroll', update);
      element?.style.removeProperty('--chat-viewport-height');
      element?.style.removeProperty('--chat-viewport-top');
    };
  }, [ref]);

  return isMobile;
}
