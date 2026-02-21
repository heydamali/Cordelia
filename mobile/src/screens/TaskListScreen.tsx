import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  ActivityIndicator,
  SafeAreaView,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { useTasks } from '../hooks/useTasks';
import { TaskCard } from '../components/TaskCard';
import { SnoozeModal } from '../components/SnoozeModal';
import { Task, TaskPriority } from '../types/task';

type Filter = 'all' | TaskPriority;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all',    label: 'All' },
  { key: 'high',   label: '🔴 High' },
  { key: 'medium', label: '🟠 Med' },
  { key: 'low',    label: '🟢 Low' },
];

export function TaskListScreen() {
  const { tasks, loading, refreshing, error, load, updateTaskStatus } = useTasks();
  const [snoozingTask, setSnoozingTask] = useState<Task | null>(null);
  const [filter, setFilter] = useState<Filter>('all');

  useEffect(() => { load(); }, [load]);

  const visible = filter === 'all' ? tasks : tasks.filter(t => t.priority === filter);

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

  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Cordelia</Text>
        {tasks.length > 0 && (
          <Text style={styles.badge}>{tasks.length}</Text>
        )}
      </View>

      {/* Priority filter */}
      <View style={styles.filterRow}>
        {FILTERS.map(f => (
          <TouchableOpacity
            key={f.key}
            style={[styles.filterTab, filter === f.key && styles.filterTabActive]}
            onPress={() => setFilter(f.key)}
          >
            <Text style={[styles.filterText, filter === f.key && styles.filterTextActive]}>
              {f.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Error banner */}
      {error ? (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity onPress={() => load()}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      {/* Content */}
      {loading && !refreshing ? (
        <ActivityIndicator style={styles.spinner} size="large" color="#007AFF" />
      ) : (
        <FlatList
          data={visible}
          keyExtractor={t => t.id}
          renderItem={({ item }) => (
            <TaskCard
              task={item}
              onDone={() => handleDone(item)}
              onSnooze={() => setSnoozingTask(item)}
              onIgnore={() => handleIgnore(item)}
            />
          )}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => load(true)}
              tintColor="#007AFF"
            />
          }
          contentContainerStyle={visible.length === 0 ? styles.emptyWrap : styles.list}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyEmoji}>✅</Text>
              <Text style={styles.emptyTitle}>All clear</Text>
              <Text style={styles.emptySub}>No pending tasks right now.</Text>
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
