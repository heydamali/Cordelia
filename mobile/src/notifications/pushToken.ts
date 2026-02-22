import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import { registerPushToken } from '../api/client';

export async function registerForPushNotifications(userId: string): Promise<void> {
  // Simulators can't receive push — skip silently
  if (Platform.OS === 'web') return;

  const { status: existing } = await Notifications.getPermissionsAsync();
  let status = existing;

  if (existing !== 'granted') {
    const { status: requested } = await Notifications.requestPermissionsAsync();
    status = requested;
  }

  if (status !== 'granted') {
    console.log('[push] Permission denied — notifications disabled');
    return;
  }

  try {
    const token = await Notifications.getDevicePushTokenAsync();
    console.log('[push] Device token:', token.data);
    await registerPushToken(userId, token.data);
    console.log('[push] Token registered with backend');
  } catch (e) {
    // Non-fatal — app works fine without push
    console.warn('[push] Failed to register token:', e);
  }
}
