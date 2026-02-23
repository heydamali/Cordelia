import { BASE_URL } from '../config';
import { Task, TaskListResponse, UpdateTaskBody, SourceSetting } from '../types/task';

// ngrok-skip-browser-warning bypasses the ngrok browser interstitial page
const BASE_HEADERS = {
  'Content-Type': 'application/json',
  'ngrok-skip-browser-warning': 'true',
};

interface FetchTasksOptions {
  limit?: number;
  offset?: number;
  priority?: string;
}

export async function fetchTasks(
  userId: string,
  status = 'pending',
  options: FetchTasksOptions = {},
): Promise<{ tasks: Task[]; has_more: boolean; total: number }> {
  const { limit = 20, offset = 0, priority } = options;
  const params = new URLSearchParams({
    user_id: userId,
    status,
    limit: String(limit),
    offset: String(offset),
  });
  if (priority) params.set('priority', priority);

  const res = await fetch(`${BASE_URL}/tasks?${params.toString()}`, { headers: BASE_HEADERS });
  if (!res.ok) throw new Error(`fetchTasks failed: ${res.status}`);
  const data: TaskListResponse = await res.json();
  return { tasks: data.tasks, has_more: data.has_more, total: data.total };
}

export async function updateTask(
  userId: string,
  taskId: string,
  body: UpdateTaskBody,
): Promise<Task> {
  const res = await fetch(
    `${BASE_URL}/tasks/${taskId}?user_id=${userId}`,
    {
      method: 'PATCH',
      headers: BASE_HEADERS,
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(`updateTask failed: ${res.status}`);
  return res.json();
}

export async function registerPushToken(userId: string, pushToken: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/users/push-token`, {
    method: 'POST',
    headers: BASE_HEADERS,
    body: JSON.stringify({ user_id: userId, push_token: pushToken }),
  });
  if (!res.ok) throw new Error(`registerPushToken failed: ${res.status}`);
}

export async function fetchSources(userId: string): Promise<SourceSetting[]> {
  const res = await fetch(`${BASE_URL}/sources?user_id=${userId}`, { headers: BASE_HEADERS });
  if (!res.ok) throw new Error(`fetchSources failed: ${res.status}`);
  return res.json();
}

export async function toggleSource(
  userId: string,
  source: string,
  enabled: boolean,
): Promise<SourceSetting> {
  const res = await fetch(`${BASE_URL}/sources/${source}?user_id=${userId}`, {
    method: 'PATCH',
    headers: BASE_HEADERS,
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`toggleSource failed: ${res.status}`);
  return res.json();
}
