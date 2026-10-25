import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authApi, commandsApi, petsApi, sessionsApi, settingsApi, workspacesApi } from '@/lib/api';
import { tokenStorage } from '@/lib/auth';
import { useAuthStore } from './authStore';
import { useChatStore } from './chatStore';
import { usePetStore } from './petStore';
import { useCommandStore } from './commandStore';
import { useUiActionStore } from './uiActionStore';
import { useWebpagePreviewStore } from './webpagePreviewStore';
import type { Session, UserPet } from '@/lib/types';

vi.mock('@/lib/api', () => ({
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  settingsApi: { getProfile: vi.fn() },
  sessionsApi: { list: vi.fn(), get: vi.fn(), create: vi.fn(), delete: vi.fn(), rename: vi.fn(), pin: vi.fn(), archive: vi.fn() },
  workspacesApi: { list: vi.fn() },
  petsApi: { active: vi.fn(), interact: vi.fn() },
  commandsApi: { list: vi.fn() },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function token(user: string) { return `x.${btoa(JSON.stringify({ sub: user, role: 'admin' }))}.x`; }
const oldPet = { id: 'pet-A', package: { row_mapping: {} } } as UserPet;
const newPet = { id: 'pet-B', package: { row_mapping: {} } } as UserPet;
const sessionA = { id: 'session-A', title: 'private' } as Session;

beforeEach(() => {
  vi.resetAllMocks();
  tokenStorage.clear();
  tokenStorage.set(token('A'), 'refresh-A');
  vi.mocked(settingsApi.getProfile).mockResolvedValue({ username: 'B' } as Awaited<ReturnType<typeof settingsApi.getProfile>>);
});

describe('account boundary', () => {
  it('clears private stores immediately even when logout has not returned', async () => {
    const logout = deferred<void>();
    vi.mocked(authApi.logout).mockReturnValue(logout.promise);
    useChatStore.setState({ sessions: [sessionA], messages: { 'session-A': [{ id: 'm', role: 'user', content: 'secret', created_at: '' }] }, activeSessionId: 'session-A' });
    usePetStore.setState({ activePet: oldPet, loaded: true, tasks: { 'session-A': { label: 'private task', tool: null, updatedAt: Date.now() } } });
    useCommandStore.setState({ loaded: true });
    useUiActionStore.setState({ seen: { 'old-action': true }, resolved: { 'old-action': { action_id: 'old-action', tool_call_id: 'tc', status: 'ok' } } });
    useWebpagePreviewStore.getState().openPreview({ pageId: 'private-page', title: 'secret' });
    const pending = useAuthStore.getState().logout();
    expect(useChatStore.getState().messages).toEqual({});
    expect(useChatStore.getState().sessions).toEqual([]);
    expect(usePetStore.getState()).toMatchObject({ loaded: false, activePet: null, tasks: {} });
    expect(useCommandStore.getState().loaded).toBe(false);
    expect(useUiActionStore.getState()).toMatchObject({ seen: {}, resolved: {}, queue: [], active: null });
    expect(useWebpagePreviewStore.getState().preview).toBeNull();
    tokenStorage.set(token('B'), 'refresh-B');
    logout.resolve();
    await pending;
    expect(tokenStorage.getRefresh()).toBe('refresh-B');
  });

  it('does not publish an earlier successful login after a later login', async () => {
    const first = deferred<Awaited<ReturnType<typeof authApi.login>>>();
    vi.mocked(authApi.login).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ access_token: token('B'), refresh_token: 'refresh-B', token_type: 'bearer' });
    const loginA = useAuthStore.getState().login('A', 'password');
    expect(await useAuthStore.getState().login('B', 'password')).toBe(true);
    first.resolve({ access_token: token('A'), refresh_token: 'refresh-A', token_type: 'bearer' });
    expect(await loginA).toBe(false);
    expect(tokenStorage.getRefresh()).toBe('refresh-B');
    expect(useAuthStore.getState().username).toBe('B');
  });

  it('discards old chat, workspace, profile, command and pet loads after reset', async () => {
    const list = deferred<Session[]>();
    const detail = deferred<Awaited<ReturnType<typeof sessionsApi.get>>>();
    const workspaces = deferred<Awaited<ReturnType<typeof workspacesApi.list>>>();
    const profile = deferred<Awaited<ReturnType<typeof settingsApi.getProfile>>>();
    const commands = deferred<Awaited<ReturnType<typeof commandsApi.list>>>();
    const pet = deferred<UserPet | null>();
    vi.mocked(sessionsApi.list).mockReturnValue(list.promise);
    vi.mocked(sessionsApi.get).mockReturnValue(detail.promise);
    vi.mocked(workspacesApi.list).mockReturnValue(workspaces.promise);
    vi.mocked(settingsApi.getProfile).mockReturnValue(profile.promise);
    vi.mocked(commandsApi.list).mockReturnValue(commands.promise);
    vi.mocked(petsApi.active).mockReturnValue(pet.promise);
    const pending = [useChatStore.getState().loadSessions(), useChatStore.getState().refreshSessions(), useChatStore.getState().loadSessionMessages('session-A'), useChatStore.getState().loadWorkspaces(), useAuthStore.getState().loadProfile(), useCommandStore.getState().load(), usePetStore.getState().loadActive()];
    tokenStorage.clear();
    useChatStore.setState({ sessions: [{ id: 'session-B' } as Session], isSessionsLoading: true });
    usePetStore.setState({ activePet: newPet, loaded: true });
    list.resolve([sessionA]);
    detail.resolve({ ...sessionA, messages: [{ id: 'm', role: 'user', content: 'secret', created_at: '' }] } as Awaited<ReturnType<typeof sessionsApi.get>>);
    workspaces.resolve([{ id: 'workspace-A' } as Awaited<ReturnType<typeof workspacesApi.list>>[number]]);
    profile.resolve({ username: 'A' } as Awaited<ReturnType<typeof settingsApi.getProfile>>);
    commands.resolve([{ name: 'private-A' } as Awaited<ReturnType<typeof commandsApi.list>>[number]]);
    pet.resolve(oldPet);
    await Promise.all(pending);
    expect(useChatStore.getState()).toMatchObject({ sessions: [{ id: 'session-B' }], messages: {}, workspaces: [], isSessionsLoading: true });
    expect(usePetStore.getState().activePet).toEqual(newPet);
    expect(useAuthStore.getState().username).toBeNull();
    expect(useCommandStore.getState().commands).toEqual([]);
  });

  it('does not clear the new account pet when an old load fails', async () => {
    const pet = deferred<UserPet | null>();
    vi.mocked(petsApi.active).mockReturnValue(pet.promise);
    const pending = usePetStore.getState().loadActive();
    tokenStorage.clear();
    usePetStore.setState({ activePet: newPet, loaded: true });
    pet.reject(new Error('old failure'));
    await pending;
    expect(usePetStore.getState().activePet).toEqual(newPet);
  });

  it.each(['delete', 'rename', 'pin', 'archive'] as const)('ignores delayed session %s after changing accounts', async (operation) => {
    const late = deferred<never>();
    vi.mocked(sessionsApi[operation]).mockReturnValue(late.promise);
    useChatStore.setState({ sessions: [sessionA] });
    const state = useChatStore.getState();
    const pending = operation === 'delete' ? state.deleteSession('session-A')
      : operation === 'rename' ? state.renameSession('session-A', 'old title')
        : operation === 'pin' ? state.pinSession('session-A', true)
          : state.archiveSession('session-A', true);
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    tokenStorage.clear();
    const current = { ...sessionA, title: 'new identity sentinel' };
    useChatStore.setState({ sessions: [current] });
    late.resolve(undefined as never);
    await rejected;
    expect(useChatStore.getState().sessions).toEqual([current]);
  });

  it('discards an old pet interaction and chat creation', async () => {
    const pet = deferred<UserPet>();
    const session = deferred<Session>();
    vi.mocked(petsApi.interact).mockReturnValue(pet.promise);
    vi.mocked(sessionsApi.create).mockReturnValue(session.promise);
    usePetStore.setState({ activePet: oldPet });
    const interaction = usePetStore.getState().playRow(1);
    const creation = useChatStore.getState().createSession('private');
    const rejected = expect(creation).rejects.toMatchObject({ name: 'AbortError' });
    tokenStorage.clear();
    usePetStore.setState({ activePet: newPet });
    pet.resolve(oldPet);
    session.resolve(sessionA);
    await interaction;
    await rejected;
    expect(usePetStore.getState().activePet).toEqual(newPet);
    expect(useChatStore.getState().sessions).toEqual([]);
  });
});
