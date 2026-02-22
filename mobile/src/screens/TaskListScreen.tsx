import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  ActivityIndicator,
  SafeAreaView,
  TouchableOpacity,
  RefreshControl,
  AppState,
  AppStateStatus,
  useWindowDimensions,
} from 'react-native';
import { useTasks } from '../hooks/useTasks';
import { useSwipeHint } from '../hooks/useSwipeHint';
import { TaskCard } from '../components/TaskCard';
import { SnoozeModal } from '../components/SnoozeModal';
import { Task, TaskPriority } from '../types/task';

interface Props {
  userId: string;
  onSignOut: () => void;
}

type TabKey = 'all' | TaskPriority | 'missed';
type PoolTabKey = 'high' | 'medium' | 'low' | 'missed';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'all',    label: 'All' },
  { key: 'high',   label: '🔴 High' },
  { key: 'medium', label: '🟠 Med' },
  { key: 'low',    label: '🟢 Low' },
  { key: 'missed', label: '⚫ Missed' },
];

const CARD_HEIGHT = 90;
const UI_CHROME = 174; // header + filter row + safe area

export function TaskListScreen({ userId, onSignOut }: Props) {
  const { height } = useWindowDimensions();
  const pageSize = Math.max(5, Math.ceil((height - UI_CHROME) / CARD_HEIGHT));

  const {
    pools,
    tabLoading,
    tabRefreshing,
    error,
    loadTab,
    loadMoreTab,
    loadMoreAll,
    refreshTab,
    updateTaskStatus,
  } = useTasks(userId);

  const { shouldShow, markShown, checkHint } = useSwipeHint();

  const [snoozingTask, setSnoozingTask] = useState<Task | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>('high');

  // Load data when tab changes
  useEffect(() => {
    if (activeTab === 'all') {
      const poolTabs: PoolTabKey[] = ['high', 'medium', 'low', 'missed'];
      for (const tab of poolTabs) {
        if (!pools[tab].loaded) {
          loadTab(tab, pageSize);
        }
      }
    } else {
      const tab = activeTab as PoolTabKey;
      if (!pools[tab].loaded) {
        loadTab(tab, pageSize);
      }
    }
  // We intentionally only re-run when activeTab or pageSize changes (pools would cause infinite loop)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, pageSize]);

  // AppState listener: re-evaluate hint on foreground
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next: AppStateStatus) => {
      if (appStateRef.current.match(/inactive|background/) && next === 'active') {
        checkHint();
      }
      appStateRef.current = next;
    });
    return () => sub.remove();
  }, [checkHint]);

  const visible = useMemo<Task[]>(() => {
    if (activeTab === 'all') {
      return [
        ...pools.missed.tasks,
        ...pools.high.tasks,
        ...pools.medium.tasks,
        ...pools.low.tasks,
      ];
    }
    return pools[activeTab as PoolTabKey].tasks;
  }, [activeTab, pools]);

  const totalBadge = useMemo(() => {
    return (
      pools.high.tasks.length +
      pools.medium.tasks.length +
      pools.low.tasks.length +
      pools.missed.tasks.length
    );
  }, [pools]);

  const handleDone = useCallback(
    (task: Task) => updateTaskStatus(task.id, { status: 'done' }),
    [updateTaskStatus],
  );

  const handleIgnore = useCallback(
    (task: Task) => updateTaskStatus(task.id, { status: 'ignored' }),
    [updateTaskStatus],
  );

  const handleSnooze = useCallback(
    (task: Task, until: Date) => {
      setSnoozingTask(null);
      updateTaskStatus(task.id, { status: 'snoozed', snoozed_until: until.toISOString() });
    },
    [updateTaskStatus],
  );

  const handleEndReached = useCallback(() => {
    if (activeTab === 'all') {
      loadMoreAll(pageSize);
    } else {
      loadMoreTab(activeTab as PoolTabKey, pageSize);
    }
  }, [activeTab, pageSize, loadMoreAll, loadMoreTab]);

  const handleRefresh = useCallback(() => {
    if (activeTab === 'all') {
      const poolTabs: PoolTabKey[] = ['high', 'medium', 'low', 'missed'];
      for (const tab of poolTabs) {
        refreshTab(tab, pageSize);
      }
    } else {
      refreshTab(activeTab as PoolTabKey, pageSize);
    }
  }, [activeTab, pageSize, refreshTab]);

  const isMissedView = activeTab === 'missed';
  const isLoading = tabLoading && visible.length === 0;

  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Cordelia</Text>
        {totalBadge > 0 && (
          <Text style={styles.badge}>{totalBadge}</Text>
        )}
        <View style={{ flex: 1 }} />
        <TouchableOpacity onPress={onSignOut} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <Text style={styles.signOutText}>Sign out</Text>
        </TouchableOpacity>
      </View>

      {/* Tabs */}
      <View style={styles.filterRow}>
        {TABS.map(t => (
          <TouchableOpacity
            key={t.key}
            style={[styles.filterTab, activeTab === t.key && styles.filterTabActive]}
            onPress={() => setActiveTab(t.key)}
          >
            <Text style={[styles.filterText, activeTab === t.key && styles.filterTextActive]}>
              {t.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Error banner */}
      {error ? (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity onPress={handleRefresh}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      {/* Content */}
      {isLoading ? (
        <ActivityIndicator style={styles.spinner} size="large" color="#007AFF" />
      ) : (
        <FlatList
          data={visible}
          keyExtractor={t => t.id}
          renderItem={({ item, index }) => (
            <TaskCard
              task={item}
              onDone={() => handleDone(item)}
              onSnooze={() => setSnoozingTask(item)}
              onIgnore={() => handleIgnore(item)}
              showHint={shouldShow && index === 0}
              onHintShown={markShown}
            />
          )}
          refreshControl={
            <RefreshControl
              refreshing={tabRefreshing}
              onRefresh={handleRefresh}
              tintColor="#007AFF"
            />
          }
          onEndReached={handleEndReached}
          onEndReachedThreshold={0.3}
          ListFooterComponent={
            tabLoading && visible.length > 0 ? (
              <ActivityIndicator style={styles.footerSpinner} color="#007AFF" />
            ) : null
          }
          contentContainerStyle={visible.length === 0 ? styles.emptyWrap : styles.list}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyEmoji}>{isMissedView ? '📅' : '✅'}</Text>
              <Text style={styles.emptyTitle}>{isMissedView ? 'Nothing missed' : 'All clear'}</Text>
              <Text style={styles.emptySub}>{isMissedView ? 'No missed appointments.' : 'No pending tasks right now.'}</Text>
            </View>
          }
        />
      )}

      <SnoozeModal
        visible={snoozingTask !== null}
        onClose={() => setSnoozingTask(null)}
        onSnooze={until => snoozingTask && handleSnooze(snoozingTask, until)}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F2F2F7',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 8,
    gap: 8,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#1C1C1E',
  },
  badge: {
    backgroundColor: '#007AFF',
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '700',
    borderRadius: 10,
    paddingHorizontal: 7,
    paddingVertical: 2,
    overflow: 'hidden',
  },
  signOutText: {
    fontSize: 13,
    color: '#8E8E93',
    fontWeight: '500',
  },
  filterRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingVertical: 8,
    gap: 8,
  },
  filterTab: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 20,
    backgroundColor: '#E5E5EA',
  },
  filterTabActive: {
    backgroundColor: '#007AFF',
  },
  filterText: {
    fontSize: 13,
    fontWeight: '500',
    color: '#3C3C43',
  },
  filterTextActive: {
    color: '#FFFFFF',
  },
  errorBanner: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#FFE5E5',
    marginHorizontal: 16,
    marginBottom: 8,
    padding: 12,
    borderRadius: 10,
  },
  errorText: {
    fontSize: 13,
    color: '#FF3B30',
    flex: 1,
  },
  retryText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#007AFF',
    marginLeft: 8,
  },
  spinner: {
    marginTop: 60,
  },
  footerSpinner: {
    marginVertical: 16,
  },
  list: {
    paddingBottom: 32,
  },
  emptyWrap: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  empty: {
    alignItems: 'center',
    paddingHorizontal: 32,
  },
  emptyEmoji: {
    fontSize: 48,
    marginBottom: 16,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: '600',
    color: '#1C1C1E',
    marginBottom: 6,
  },
  emptySub: {
    fontSize: 15,
    color: '#8E8E93',
    textAlign: 'center',
  },
});
