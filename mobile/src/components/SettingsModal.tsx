import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  Modal,
  TouchableOpacity,
  Switch,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { fetchSources, toggleSource } from '../api/client';
import { SourceSetting } from '../types/task';

const SOURCE_ICONS: Record<string, string> = {
  mail: '\u2709',      // ✉
  calendar: '\uD83D\uDCC5', // 📅
};

interface Props {
  visible: boolean;
  onClose: () => void;
  userId: string;
  onSignOut: () => void;
  onSourceToggled: () => void;
}

export function SettingsModal({ visible, onClose, userId, onSignOut, onSourceToggled }: Props) {
  const [sources, setSources] = useState<SourceSetting[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchSources(userId);
      setSources(data);
    } catch {
      // silently fail — user can retry by reopening
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  const handleToggle = async (source: string, newValue: boolean) => {
    // Optimistic update
    setSources(prev =>
      prev.map(s => (s.source === source ? { ...s, enabled: newValue } : s)),
    );
    try {
      await toggleSource(userId, source, newValue);
      onSourceToggled();
    } catch {
      // Revert on failure
      setSources(prev =>
        prev.map(s => (s.source === source ? { ...s, enabled: !newValue } : s)),
      );
    }
  };

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Settings</Text>
            <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
              <Text style={styles.closeBtn}>Done</Text>
            </TouchableOpacity>
          </View>

          <Text style={styles.sectionLabel}>Data Sources</Text>

          {loading ? (
            <ActivityIndicator style={{ marginTop: 20 }} color="#007AFF" />
          ) : (
            sources.map(s => (
              <View key={s.source} style={styles.sourceRow}>
                <Text style={styles.sourceIcon}>
                  {SOURCE_ICONS[s.icon] || s.icon}
                </Text>
                <Text style={styles.sourceName}>{s.display_name}</Text>
                <Switch
                  value={s.enabled}
                  onValueChange={(val) => handleToggle(s.source, val)}
                  trackColor={{ true: '#007AFF', false: '#E5E5EA' }}
                />
              </View>
            ))
          )}

          <View style={styles.divider} />

          <TouchableOpacity style={styles.signOutRow} onPress={onSignOut}>
            <Text style={styles.signOutText}>Sign Out</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 40,
    minHeight: 300,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1C1C1E',
  },
  closeBtn: {
    fontSize: 16,
    fontWeight: '600',
    color: '#007AFF',
  },
  sectionLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: '#8E8E93',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 12,
  },
  sourceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
  },
  sourceIcon: {
    fontSize: 20,
    marginRight: 12,
    width: 28,
    textAlign: 'center',
  },
  sourceName: {
    flex: 1,
    fontSize: 16,
    color: '#1C1C1E',
  },
  divider: {
    height: 1,
    backgroundColor: '#E5E5EA',
    marginVertical: 20,
  },
  signOutRow: {
    paddingVertical: 12,
  },
  signOutText: {
    fontSize: 16,
    color: '#FF3B30',
    fontWeight: '500',
  },
});
