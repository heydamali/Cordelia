import React, { useEffect } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StatusBar } from 'expo-status-bar';
import { registerForPushNotifications } from './src/notifications/pushToken';
import { TaskListScreen } from './src/screens/TaskListScreen';

export default function App() {
  useEffect(() => {
    // Request push permission + send device token to backend on first launch
    registerForPushNotifications().catch(console.warn);
  }, []);

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <TaskListScreen />
      <StatusBar style="dark" />
    </GestureHandlerRootView>
  );
}
