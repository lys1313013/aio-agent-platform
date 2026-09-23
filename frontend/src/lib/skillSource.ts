import type { NavigateFunction } from 'react-router-dom';
import { sessionsApi } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';

/**
 * Open the chat session a skill was created or modified in.
 *
 * The session API re-checks access first, so a deleted or foreign session never
 * navigates; callers report that with their own message.
 */
export async function openSourceSession(
  sessionId: string,
  role: string | null | undefined,
  navigate: NavigateFunction,
): Promise<boolean> {
  try {
    const session = await sessionsApi.get(sessionId);
    if (session.agent_id) {
      navigate(`${role === 'user' ? '/portal' : ''}/agents/${session.agent_id}/chat/${sessionId}`);
    } else {
      await useChatStore.getState().setActiveSession(sessionId);
      navigate('/chat');
    }
    return true;
  } catch {
    return false;
  }
}