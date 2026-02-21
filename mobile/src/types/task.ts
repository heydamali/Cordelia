export type TaskCategory = 'reply' | 'appointment' | 'action' | 'info' | 'ignored';
export type TaskPriority = 'high' | 'medium' | 'low';
export type TaskStatus = 'pending' | 'done' | 'snoozed' | 'ignored' | 'expired';

export interface Task {
  id: string;
  conversation_id: string;
  task_key: string;
  title: string;
  category: TaskCategory;
  priority: TaskPriority;
  summary: string | null;
  due_at: string | null;
  status: TaskStatus;
  ignore_reason: string | null;
  snoozed_until: string | null;
  notify_at: string[];
  notifications_sent: string[];
  created_at: string;
  updated_at: string;
}

export interface TaskListResponse {
  tasks: Task[];
  total: number;
}

export interface UpdateTaskBody {
  status: 'pending' | 'done' | 'snoozed' | 'ignored';
  snoozed_until?: string;
}
