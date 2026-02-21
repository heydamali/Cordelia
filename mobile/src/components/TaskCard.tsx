import React, { useRef } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import Swipeable from 'react-native-gesture-handler/Swipeable';
import * as Haptics from 'expo-haptics';
import { Task } from '../types/task';

const PRIORITY_COLOR: Record<string, string> = {
  high:   '#FF3B30',
  medium: '#FF9500',
  low:    '#34C759',
};

const CATEGORY_LABEL: Record<string, string> = {
  reply:       'Reply',
  appointment: 'Appt',
  action:      'Action',
  info:        'Info',
  ignored:     'Ignored',
};

function formatDue(dueAt: string | null): { text: string; overdue: boolean } | null {
  if (!dueAt) return null;
  const diffMins = Math.round((new Date(dueAt).getTime() - Date.now()) / 60_000);
  if (diffMins < 0)     return { text: 'Overdue',                            overdue: true };
  if (diffMins < 60)    return { text: `Due in ${diffMins}m`,                overdue: false };
  if (diffMins < 1440)  return { text: `Due in ${Math.round(diffMins / 60)}h`, overdue: false };
  return                       { text: `Due in ${Math.round(diffMins / 1440)}d`, overdue: false };
}

interface Props {
  task: Task;
  onDone: () => void;
  onSnooze: () => void;
  onIgnore: () => void;
}

export function TaskCard({ task, onDone, onSnooze, onIgnore }: Props) {
  const ref = useRef<Swipeable>(null);
  const due = formatDue(task.due_at);

  const close = () => ref.current?.close();

  const renderLeft = () => (
    <TouchableOpacity
      style={styles.doneAction}
      onPress={() => {
        close();
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        onDone();
      }}
    >
      <Text style={styles.actionIcon}>✓</Text>
      <Text style={styles.actionLabel}>Done</Text>
    </TouchableOpacity>
  );

  const renderRight = () => (
    <View style={styles.rightActions}>
      <TouchableOpacity
        style={styles.snoozeAction}
        onPress={() => {
          close();
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          onSnooze();
        }}
      >
        <Text style={styles.actionIcon}>💤</Text>
        <Text style={styles.actionLabel}>Snooze</Text>
      </TouchableOpacity>
      <TouchableOpacity
        style={styles.ignoreAction}
        onPress={() => {
          close();
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          onIgnore();
        }}
      >
        <Text style={styles.actionIcon}>✕</Text>
        <Text style={styles.actionLabel}>Ignore</Text>
      </TouchableOpacity>
    </View>
  );

  return (
    <Swipeable
      ref={ref}
      renderLeftActions={renderLeft}
      renderRightActions={renderRight}
      leftThreshold={60}
      rightThreshold={60}
    >
      <View style={styles.card}>
        <View style={[styles.dot, { backgroundColor: PRIORITY_COLOR[task.priority] }]} />
        <View style={styles.body}>
          <Text style={styles.title} numberOfLines={2}>{task.title}</Text>
          <View style={styles.meta}>
            <Text style={styles.category}>{CATEGORY_LABEL[task.category]}</Text>
            {due && (
              <Text style={[styles.due, due.overdue && styles.overdue]}>
                · {due.text}
              </Text>
            )}
          </View>
          {task.summary ? (
            <Text style={styles.summary} numberOfLines={2}>{task.summary}</Text>
          ) : null}
        </View>
      </View>
    </Swipeable>
  );
}

const ACTION_BASE: object = {
  justifyContent: 'center',
  alignItems: 'center',
  borderRadius: 12,
  marginVertical: 6,
  width: 76,
};

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: '#FFFFFF',
    marginHorizontal: 16,
    marginVertical: 6,
    borderRadius: 12,
    padding: 14,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.06,
    shadowRadius: 4,
    elevation: 2,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginTop: 4,
    marginRight: 12,
    flexShrink: 0,
  },
  body: {
    flex: 1,
  },
  title: {
    fontSize: 15,
    fontWeight: '600',
    color: '#1C1C1E',
    marginBottom: 4,
  },
  meta: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
  },
  category: {
    fontSize: 12,
    color: '#8E8E93',
    fontWeight: '500',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  due: {
    fontSize: 12,
    color: '#FF9500',
    fontWeight: '500',
    marginLeft: 4,
  },
  overdue: {
    color: '#FF3B30',
  },
  summary: {
    fontSize: 13,
    color: '#6C6C70',
    lineHeight: 18,
  },
  // Swipe actions
  doneAction: {
    ...(ACTION_BASE as object),
    backgroundColor: '#34C759',
    marginLeft: 16,
    marginRight: 0,
  },
  rightActions: {
    flexDirection: 'row',
    marginVertical: 6,
    marginRight: 16,
    gap: 8,
  },
  snoozeAction: {
    ...(ACTION_BASE as object),
    backgroundColor: '#FF9500',
  },
  ignoreAction: {
    ...(ACTION_BASE as object),
    backgroundColor: '#8E8E93',
  },
  actionIcon: {
    fontSize: 18,
    color: '#FFFFFF',
  },
  actionLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 2,
  },
});
