import AsyncStorage from '@react-native-async-storage/async-storage';
import { BASE_URL } from '../config';
import { Task, TaskListResponse, UpdateTaskBody, SourceSetting } from '../types/task';

// ngrok-skip-browser-warning bypasses the ngrok browser interstitial page
const BASE_HEADERS: Record<string, string> = {
  'Content-Type': 'application/json',
  'ngrok-skip-browser-warning': 'true',
};

async function getAuthHeaders(): Promise<Record<string, string>> {
  const raw = await AsyncStorage.getItem('auth_user');
  if (!raw) return {};
  const { token } = JSON.parse(raw);
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const auth = await getAuthHeaders();
  const headers = { ...BASE_HEADERS, ...auth, ...(init.headers as Record<string, string> || {}) };
  const res = await fetch(url, { ...init, headers });

  if (res.status === 401) {
    // Token expired or invalid — clear session so the app forces re-login
    await AsyncStorage.removeItem('auth_user');
  }

  return res;
}

interface FetchTasksOptions {
  limit?: number;
  offset?: number;
  priority?: string;
}

export async function fetchTasks(
  status = 'pending',
  options: FetchTasksOptions = {},
): Promise<{ tasks: Task[]; has_more: boolean; total: number }> {
  const { limit = 20, offset = 0, priority } = options;
  const params = new URLSearchParams({
    status,
    limit: String(limit),
    offset: String(offset),
  });
  if (priority) params.set('priority', priority);

  const res = await authFetch(`${BASE_URL}/tasks?${params.toString()}`);
  if (!res.ok) throw new Error(`fetchTasks failed: ${res.status}`);
  const data: TaskListResponse = await res.json();
  return { tasks: data.tasks, has_more: data.has_more, total: data.total };
}

export async function updateTask(
  taskId: string,
  body: UpdateTaskBody,
): Promise<Task> {
  const res = await authFetch(
    `${BASE_URL}/tasks/${taskId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(`updateTask failed: ${res.status}`);
  return res.json();
}

export async function registerPushToken(pushToken: string): Promise<void> {
  const res = await authFetch(`${BASE_URL}/users/push-token`, {
    method: 'POST',
    body: JSON.stringify({ push_token: pushToken }),
  });
  if (!res.ok) throw new Error(`registerPushToken failed: ${res.status}`);
}

export async function fetchSources(): Promise<SourceSetting[]> {
  const res = await authFetch(`${BASE_URL}/sources`);
  if (!res.ok) throw new Error(`fetchSources failed: ${res.status}`);
  return res.json();
}

export async function toggleSource(
  source: string,
  enabled: boolean,
): Promise<SourceSetting> {
  const res = await authFetch(`${BASE_URL}/sources/${source}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`toggleSource failed: ${res.status}`);
  return res.json();
}

// WhatsApp linking

export async function startWhatsAppLink(
  phoneNumber: string,
): Promise<{ pairing_code: string; expires_in: number }> {
  const res = await authFetch(`${BASE_URL}/whatsapp/link/start`, {
    method: 'POST',
    body: JSON.stringify({ phone_number: phoneNumber }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `startWhatsAppLink failed: ${res.status}`);
  }
  return res.json();
}

export async function getWhatsAppLinkStatus(): Promise<{
  status: string;
  phone_number?: string;
}> {
  const res = await authFetch(`${BASE_URL}/whatsapp/link/status`);
  if (!res.ok) throw new Error(`getWhatsAppLinkStatus failed: ${res.status}`);
  return res.json();
}

export async function unlinkWhatsApp(): Promise<void> {
  const res = await authFetch(`${BASE_URL}/whatsapp/unlink`, { method: 'POST' });
  if (!res.ok) throw new Error(`unlinkWhatsApp failed: ${res.status}`);
}
