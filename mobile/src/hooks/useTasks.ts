import { useState, useCallback } from 'react';
import { fetchTasks, updateTask } from '../api/client';
import { Task, UpdateTaskBody } from '../types/task';

export function useTasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    try {
      const data = await fetchTasks('pending');
      setTasks(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const updateTaskStatus = useCallback(
    async (taskId: string, body: UpdateTaskBody) => {
      // Optimistic remove from list immediately
      setTasks(prev => prev.filter(t => t.id !== taskId));
      try {
        await updateTask(taskId, body);
      } catch (e) {
        // Reload on failure to restore accurate state
        load();
      }
    },
    [load],
  );

  return { tasks, loading, refreshing, error, load, updateTaskStatus };
}
