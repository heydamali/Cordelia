import React from 'react';
import {
  Modal,
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from 'react-native';

interface Props {
  visible: boolean;
  onClose: () => void;
  onSnooze: (until: Date) => void;
}

function addHours(h: number): Date {
  return new Date(Date.now() + h * 3_600_000);
}

function tomorrowAt(hour: number): Date {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(hour, 0, 0, 0);
  return d;
}

function nextMonday(): Date {
  const d = new Date();
  const daysUntil = ((8 - d.getDay()) % 7) || 7;
  d.setDate(d.getDate() + daysUntil);
  d.setHours(9, 0, 0, 0);
  return d;
}

const PRESETS = [
  { label: 'In 1 hour',          getDate: () => addHours(1) },
  { label: 'In 3 hours',         getDate: () => addHours(3) },
  { label: 'Tonight at 8pm',     getDate: () => { const d = new Date(); d.setHours(20, 0, 0, 0); return d; } },
  { label: 'Tomorrow morning',   getDate: () => tomorrowAt(9) },
  { label: 'Next Monday',        getDate: nextMonday },
];

export function SnoozeModal({ visible, onClose, onSnooze }: Props) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <TouchableOpacity style={styles.backdrop} activeOpacity={1} onPress={onClose}>
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Snooze until…</Text>
          {PRESETS.map(preset => (
            <TouchableOpacity
              key={preset.label}
              style={styles.option}
              onPress={() => onSnooze(preset.getDate())}
            >
              <Text style={styles.optionText}>{preset.label}</Text>
            </TouchableOpacity>
          ))}
          <TouchableOpacity style={styles.cancelBtn} onPress={onClose}>
            <Text style={styles.cancelText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </TouchableOpacity>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingBottom: 34,
    paddingHorizontal: 16,
  },
  handle: {
    width: 36,
    height: 4,
    backgroundColor: '#D1D1D6',
    borderRadius: 2,
    alignSelf: 'center',
    marginTop: 12,
    marginBottom: 20,
  },
  title: {
    fontSize: 13,
    fontWeight: '600',
    color: '#8E8E93',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 8,
    paddingHorizontal: 4,
  },
  option: {
    paddingVertical: 16,
    paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E5E5EA',
  },
  optionText: {
    fontSize: 17,
    color: '#1C1C1E',
  },
  cancelBtn: {
    marginTop: 16,
    paddingVertical: 16,
    alignItems: 'center',
    backgroundColor: '#F2F2F7',
    borderRadius: 12,
  },
  cancelText: {
    fontSize: 17,
    fontWeight: '600',
    color: '#FF3B30',
  },
});
