import React, { useEffect, useRef } from 'react';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StatusBar } from 'expo-status-bar';
import { useAuth } from './src/hooks/useAuth';
import { LoginScreen } from './src/screens/LoginScreen';
import { TaskListScreen } from './src/screens/TaskListScreen';
import { registerForPushNotifications } from './src/notifications/pushToken';

export default function App() {
  const { user, loading, signingIn, error, signIn, signOut } = useAuth();

  // Register push token whenever a user signs in (or on first load if already signed in)
  const registeredForRef = useRef<string | null>(null);
  useEffect(() => {
    if (user && registeredForRef.current !== user.userId) {
      registeredForRef.current = user.userId;
      registerForPushNotifications(user.userId).catch(console.warn);
    }
  }, [user]);

  if (loading) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator size="large" color="#007AFF" />
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      {user ? (
        <TaskListScreen userId={user.userId} onSignOut={signOut} />
      ) : (
        <LoginScreen onSignIn={signIn} signingIn={signingIn} error={error} />
      )}
      <StatusBar style="dark" />
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
  },
});
