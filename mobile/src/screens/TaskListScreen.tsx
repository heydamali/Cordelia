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
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withSequence,
  withDelay,
  Easing,
} from 'react-native-reanimated';
import { useTasks } from '../hooks/useTasks';
import { useSwipeHint } from '../hooks/useSwipeHint';
import { TaskCard } from '../components/TaskCard';
import { SnoozeModal } from '../components/SnoozeModal';
import { SettingsModal } from '../components/SettingsModal';
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
    silentRefreshTab,
    updateTaskStatus,
  } = useTasks(userId);

  const { shouldShow, markShown, checkHint } = useSwipeHint();

  const [snoozingTask, setSnoozingTask] = useState<Task | null>(null);
  const [settingsVisible, setSettingsVisible] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>('high');
  const [hasRefreshed, setHasRefreshed] = useState(false);
  const pullHintPlayed = useRef(false);
  const pullHintY = useSharedValue(0);
  const arrowOpacity = useSharedValue(1);

  // Auto sign-out when the session is invalid (user deleted from DB)
  useEffect(() => {
    if (error && error.includes('404')) {
      onSignOut();
    }
  }, [error, onSignOut]);

  // Load data when tab changes.
  // pageSize and pools are intentionally excluded from deps:
  //   - pools: would re-trigger on every data load causing an infinite loop
  //   - pageSize: derived from useWindowDimensions and can fluctuate across renders,
  //               re-triggering the load on every dimension change
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
  }, [activeTab]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Pull-to-refresh teaching animation — two slow tugs when the list is empty
  useEffect(() => {
    if (visible.length > 0 || hasRefreshed || pullHintPlayed.current) return;
    pullHintPlayed.current = true;

    const slow = { duration: 600, easing: Easing.inOut(Easing.ease) };
    const hold = { duration: 200 };
    const retract = { duration: 500, easing: Easing.inOut(Easing.ease) };

    pullHintY.value = withDelay(800,
      withSequence(
        withTiming(40, slow),       // tug 1 down
        withTiming(40, hold),       // hold
        withTiming(0, retract),     // release
        withDelay(400,
          withSequence(
            withTiming(40, slow),   // tug 2 down
            withTiming(40, hold),   // hold
            withTiming(0, retract), // release
          ),
        ),
      ),
    );

    // Fade out the arrow after the animation
    arrowOpacity.value = withDelay(3600, withTiming(0, { duration: 400 }));
  }, [visible.length, hasRefreshed]); // eslint-disable-line react-hooks/exhaustive-deps

  const pullHintStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: pullHintY.value }],
  }));

  const arrowAnimStyle = useAnimatedStyle(() => ({
    opacity: arrowOpacity.value,
  }));

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
    setHasRefreshed(true);
    if (activeTab === 'all') {
      const poolTabs: PoolTabKey[] = ['high', 'medium', 'low', 'missed'];
      for (const tab of poolTabs) {
        refreshTab(tab, pageSize);
      }
    } else {
      refreshTab(activeTab as PoolTabKey, pageSize);
    }
  }, [activeTab, pageSize, refreshTab]);

  const handleSourceToggled = useCallback(() => {
    // Reset all pools so tasks refresh with new source filter
    const poolTabs: PoolTabKey[] = ['high', 'medium', 'low', 'missed'];
    for (const tab of poolTabs) {
      refreshTab(tab, pageSize);
    }
  }, [refreshTab, pageSize]);

  const isMissedView = activeTab === 'missed';

  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Cordelia</Text>
        {totalBadge > 0 && (
          <Text style={styles.badge}>{totalBadge}</Text>
        )}
        <View style={{ flex: 1 }} />
        <TouchableOpacity onPress={() => setSettingsVisible(true)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <Text style={styles.gearIcon}>{'\u2699'}</Text>
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
      <Animated.View style={[{ flex: 1 }, visible.length === 0 && !hasRefreshed ? pullHintStyle : undefined]}>
        <FlatList
          data={visible}
          keyExtractor={t => t.id}
          renderItem={({ item, index }) => (
            <TaskCard
              task={item}
              onDone={() => handleDone(item)}
              onSnooze={() => setSnoozingTask(item)}
              onIgnore={() => handleIgnore(item)}
              showHint={shouldShow && index === 0 && !tabLoading}
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
              {!hasRefreshed && !isMissedView ? (
                <>
                  <Animated.View style={[styles.arrowWrap, arrowAnimStyle]}>
                    <Text style={styles.pullArrow}>{'\u2193'}</Text>
                  </Animated.View>
                  <Text style={styles.emptyTitle}>Pull down to see your tasks</Text>
                  <Text style={styles.emptySub}>We're gathering your emails and calendar events.</Text>
                </>
              ) : (
                <>
                  <Text style={styles.emptyEmoji}>{isMissedView ? '📅' : '✅'}</Text>
                  <Text style={styles.emptyTitle}>{isMissedView ? 'Nothing missed' : 'All clear'}</Text>
                  <Text style={styles.emptySub}>{isMissedView ? 'No missed appointments.' : 'No pending tasks right now.'}</Text>
                </>
              )}
            </View>
          }
        />
      </Animated.View>

      <SnoozeModal
        visible={snoozingTask !== null}
        onClose={() => setSnoozingTask(null)}
        onSnooze={until => snoozingTask && handleSnooze(snoozingTask, until)}
      />

      <SettingsModal
        visible={settingsVisible}
        onClose={() => setSettingsVisible(false)}
        userId={userId}
        onSignOut={onSignOut}
        onSourceToggled={handleSourceToggled}
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
  gearIcon: {
    fontSize: 22,
    color: '#8E8E93',
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
  arrowWrap: {
    marginBottom: 12,
  },
  pullArrow: {
    fontSize: 32,
    color: '#8E8E93',
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
