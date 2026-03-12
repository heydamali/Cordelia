import AsyncStorage from '@react-native-async-storage/async-storage';
import { fetchTasks, updateTask, registerPushToken, fetchSources, toggleSource } from '../client';

// Mock fetch globally
const mockFetch = jest.fn();
global.fetch = mockFetch;

const AUTH_USER = {
  userId: 'user-123',
  email: 'test@example.com',
  token: 'test-jwt-token',
};

beforeEach(() => {
  jest.clearAllMocks();
  (AsyncStorage as any)._store['auth_user'] = JSON.stringify(AUTH_USER);
});

afterEach(async () => {
  await AsyncStorage.clear();
});

// ---------------------------------------------------------------------------
// Auth header injection
// ---------------------------------------------------------------------------

describe('auth headers', () => {
  it('sends Bearer token from AsyncStorage', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ tasks: [], has_more: false, total: 0 }),
    });

    await fetchTasks('pending');

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers['Authorization']).toBe('Bearer test-jwt-token');
  });

  it('sends request without auth when no stored session', async () => {
    await AsyncStorage.clear();

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ tasks: [], has_more: false, total: 0 }),
    });

    await fetchTasks('pending');

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers['Authorization']).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 401 auto-logout
// ---------------------------------------------------------------------------

describe('401 handling', () => {
  it('clears session on 401 response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Token expired' }),
    });

    await expect(fetchTasks('pending')).rejects.toThrow('fetchTasks failed: 401');
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith('auth_user');
  });

  it('does not clear session on non-401 errors', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'Server error' }),
    });

    await expect(fetchTasks('pending')).rejects.toThrow('fetchTasks failed: 500');
    expect(AsyncStorage.removeItem).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// fetchTasks
// ---------------------------------------------------------------------------

describe('fetchTasks', () => {
  it('builds correct URL with status and pagination', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ tasks: [{ id: '1' }], has_more: true, total: 5 }),
    });

    const result = await fetchTasks('pending', { limit: 10, offset: 5, priority: 'high' });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/tasks?');
    expect(url).toContain('status=pending');
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=5');
    expect(url).toContain('priority=high');
    // Should NOT contain user_id
    expect(url).not.toContain('user_id');
    expect(result).toEqual({ tasks: [{ id: '1' }], has_more: true, total: 5 });
  });

  it('does not include user_id in query params', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ tasks: [], has_more: false, total: 0 }),
    });

    await fetchTasks('pending');

    const [url] = mockFetch.mock.calls[0];
    expect(url).not.toContain('user_id');
  });
});

// ---------------------------------------------------------------------------
// updateTask
// ---------------------------------------------------------------------------

describe('updateTask', () => {
  it('sends PATCH with auth and body', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ id: 'task-1', status: 'done' }),
    });

    const result = await updateTask('task-1', { status: 'done' });

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain('/tasks/task-1');
    expect(url).not.toContain('user_id');
    expect(init.method).toBe('PATCH');
    expect(init.headers['Authorization']).toBe('Bearer test-jwt-token');
    expect(JSON.parse(init.body)).toEqual({ status: 'done' });
  });
});

// ---------------------------------------------------------------------------
// registerPushToken
// ---------------------------------------------------------------------------

describe('registerPushToken', () => {
  it('sends push_token without user_id in body', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok' }),
    });

    await registerPushToken('expo-push-token-abc');

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain('/users/push-token');
    const body = JSON.parse(init.body);
    expect(body).toEqual({ push_token: 'expo-push-token-abc' });
    expect(body.user_id).toBeUndefined();
    expect(init.headers['Authorization']).toBe('Bearer test-jwt-token');
  });
});

// ---------------------------------------------------------------------------
// fetchSources / toggleSource
// ---------------------------------------------------------------------------

describe('fetchSources', () => {
  it('fetches sources without user_id', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => [{ source: 'gmail', enabled: true }],
    });

    const result = await fetchSources();

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/sources');
    expect(url).not.toContain('user_id');
    expect(result).toEqual([{ source: 'gmail', enabled: true }]);
  });
});

describe('toggleSource', () => {
  it('sends PATCH without user_id', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ source: 'gmail', enabled: false }),
    });

    await toggleSource('gmail', false);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain('/sources/gmail');
    expect(url).not.toContain('user_id');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ enabled: false });
  });
});
