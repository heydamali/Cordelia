import { useState, useCallback, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = 'hint_last_shown';

function todayISO(): string {
  return new Date().toISOString().slice(0, 10); // 'YYYY-MM-DD'
}

export function useSwipeHint(): {
  shouldShow: boolean;
  markShown: () => void;
  checkHint: () => void;
} {
  const [shouldShow, setShouldShow] = useState(false);

  const checkHint = useCallback(async () => {
    try {
      const stored = await AsyncStorage.getItem(STORAGE_KEY);
      const today = todayISO();
      if (stored !== today) {
        setShouldShow(true);
      }
    } catch {
      // Storage error: show hint as fallback
      setShouldShow(true);
    }
  }, []);

  const markShown = useCallback(async () => {
    setShouldShow(false);
    try {
      await AsyncStorage.setItem(STORAGE_KEY, todayISO());
    } catch {
      // Ignore storage errors
    }
  }, []);

  useEffect(() => {
    checkHint();
  }, [checkHint]);

  return { shouldShow, markShown, checkHint };
}
