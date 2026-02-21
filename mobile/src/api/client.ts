import { BASE_URL, USER_ID } from '../config';
import { Task, TaskListResponse, UpdateTaskBody } from '../types/task';

// ngrok-skip-browser-warning bypasses the ngrok browser interstitial page
const BASE_HEADERS = {
  'Content-Type': 'application/json',
  'ngrok-skip-browser-warning': 'true',
};

export async function fetchTasks(status = 'pending'): Promise<Task[]> {
  const res = await fetch(
    `${BASE_URL}/tasks?user_id=${USER_ID}&status=${status}`,
    { headers: BASE_HEADERS },
  );
  if (!res.ok) throw new Error(`fetchTasks failed: ${res.status}`);
  const data: TaskListResponse = await res.json();
  return data.tasks;
}

export async function updateTask(taskId: string, body: UpdateTaskBody): Promise<Task> {
  const res = await fetch(
    `${BASE_URL}/tasks/${taskId}?user_id=${USER_ID}`,
    {
      method: 'PATCH',
      headers: BASE_HEADERS,
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(`updateTask failed: ${res.status}`);
  return res.json();
}

export async function registerPushToken(pushToken: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/users/push-token`, {
    method: 'POST',
    headers: BASE_HEADERS,
    body: JSON.stringify({ user_id: USER_ID, push_token: pushToken }),
  });
  if (!res.ok) throw new Error(`registerPushToken failed: ${res.status}`);
}
