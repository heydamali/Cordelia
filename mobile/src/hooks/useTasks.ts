import React, { useState, useCallback } from 'react';
import { fetchTasks, updateTask } from '../api/client';
import { Task, UpdateTaskBody } from '../types/task';

export function useTasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lastStatusRef = React.useRef<string | string[]>('pending');

  const load = useCallback(async (status: string | string[] = 'pending', isRefresh = false) => {
    lastStatusRef.current = status;
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    try {
      const statusList = Array.isArray(status) ? status : [status];
      const results = await Promise.all(statusList.map(s => fetchTasks(s)));
      setTasks(results.flat()); // order preserved: first status's results come first
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const updateTaskStatus = useCallback(
    async (taskId: string, body: UpdateTaskBody) => {
      setTasks(prev => prev.filter(t => t.id !== taskId));
      try {
        await updateTask(taskId, body);
      } catch (e) {
        load(lastStatusRef.current);
      }
    },
    [load],
  );

  return { tasks, loading, refreshing, error, load, updateTaskStatus };
}
