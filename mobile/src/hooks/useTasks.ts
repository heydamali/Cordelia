import { useState, useCallback, useRef } from 'react';
import { fetchTasks, updateTask } from '../api/client';
import { Task, UpdateTaskBody } from '../types/task';

type TabKey = 'high' | 'medium' | 'low' | 'missed';

interface TabData {
  tasks: Task[];
  offset: number;
  hasMore: boolean;
  loaded: boolean;
}

type Pools = Record<TabKey, TabData>;

const EMPTY_TAB: TabData = { tasks: [], offset: 0, hasMore: true, loaded: false };

const INITIAL_POOLS: Pools = {
  high:   { ...EMPTY_TAB },
  medium: { ...EMPTY_TAB },
  low:    { ...EMPTY_TAB },
  missed: { ...EMPTY_TAB },
};

function tabFetchArgs(tab: TabKey): { status: string; priority?: string } {
  if (tab === 'missed') return { status: 'missed' };
  return { status: 'pending', priority: tab };
}

export function useTasks(userId: string) {
  const [pools, setPools] = useState<Pools>(INITIAL_POOLS);
  const [tabLoading, setTabLoading] = useState(false);
  const [tabRefreshing, setTabRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Track in-flight requests to avoid duplicate fetches
  const loadingRef = useRef<Set<string>>(new Set());

  const loadTab = useCallback(async (tab: TabKey, pageSize: number) => {
    const key = `load:${tab}`;
    if (loadingRef.current.has(key)) return;
    loadingRef.current.add(key);
    setTabLoading(true);
    setError(null);

    try {
      const { status, priority } = tabFetchArgs(tab);
      const result = await fetchTasks(userId, status, { limit: pageSize, offset: 0, priority });
      setPools(prev => ({
        ...prev,
        [tab]: {
          tasks: result.tasks,
          offset: result.tasks.length,
          hasMore: result.has_more,
          loaded: true,
        },
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks');
    } finally {
      loadingRef.current.delete(key);
      setTabLoading(false);
    }
  }, [userId]);

  const loadMoreTab = useCallback(async (tab: TabKey, pageSize: number) => {
    const key = `more:${tab}`;
    if (loadingRef.current.has(key)) return;

    const current = pools[tab];
    if (!current.hasMore) return;

    loadingRef.current.add(key);
    setTabLoading(true);

    try {
      const { status, priority } = tabFetchArgs(tab);
      const result = await fetchTasks(userId, status, { limit: pageSize, offset: current.offset, priority });
      setPools(prev => {
        const prevTab = prev[tab];
        return {
          ...prev,
          [tab]: {
            tasks: [...prevTab.tasks, ...result.tasks],
            offset: prevTab.offset + result.tasks.length,
            hasMore: result.has_more,
            loaded: true,
          },
        };
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load more tasks');
    } finally {
      loadingRef.current.delete(key);
      setTabLoading(false);
    }
  }, [userId, pools]);

  const loadMoreAll = useCallback(async (pageSize: number) => {
    const tabs: TabKey[] = ['high', 'medium', 'low', 'missed'];
    const needMore = tabs.filter(t => pools[t].hasMore && pools[t].loaded);
    if (needMore.length === 0) return;
    await Promise.all(needMore.map(t => loadMoreTab(t, pageSize)));
  }, [pools, loadMoreTab]);

  const refreshTab = useCallback(async (tab: TabKey, pageSize: number) => {
    setTabRefreshing(true);
    setError(null);
    setPools(prev => ({ ...prev, [tab]: { ...EMPTY_TAB } }));
    try {
      const { status, priority } = tabFetchArgs(tab);
      const result = await fetchTasks(userId, status, { limit: pageSize, offset: 0, priority });
      setPools(prev => ({
        ...prev,
        [tab]: {
          tasks: result.tasks,
          offset: result.tasks.length,
          hasMore: result.has_more,
          loaded: true,
        },
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to refresh tasks');
    } finally {
      setTabRefreshing(false);
    }
  }, [userId]);

  const updateTaskStatus = useCallback(
    async (taskId: string, body: UpdateTaskBody) => {
      // Optimistically remove from all pools
      setPools(prev => {
        const next = { ...prev } as Pools;
        for (const tab of Object.keys(next) as TabKey[]) {
          next[tab] = { ...next[tab], tasks: next[tab].tasks.filter(t => t.id !== taskId) };
        }
        return next;
      });
      try {
        await updateTask(userId, taskId, body);
      } catch (_e) {
        // On failure, mark pools as needing reload
        setPools(prev => {
          const next = { ...prev } as Pools;
          for (const tab of Object.keys(next) as TabKey[]) {
            next[tab] = { ...next[tab], loaded: false };
          }
          return next;
        });
      }
    },
    [userId],
  );

  return {
    pools,
    tabLoading,
    tabRefreshing,
    error,
    loadTab,
    loadMoreTab,
    loadMoreAll,
    refreshTab,
    updateTaskStatus,
  };
}
