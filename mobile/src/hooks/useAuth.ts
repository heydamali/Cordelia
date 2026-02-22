import { useState, useEffect, useCallback } from 'react';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { BASE_URL } from '../config';

// Required for Android: completes the auth session if the app was restarted
WebBrowser.maybeCompleteAuthSession();

const STORAGE_KEY = 'auth_user';

export interface AuthUser {
  userId: string;
  email: string;
}

export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Restore session from storage on mount
  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY)
      .then(raw => {
        if (raw) setUser(JSON.parse(raw));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(async () => {
    setError(null);
    setSigningIn(true);
    try {
      // This URL is what the backend will redirect to after OAuth completes.
      // In Expo Go: exp://<ip>:<port>/--/auth/callback
      // In standalone: cordelia://auth/callback
      const redirectUrl = Linking.createURL('auth/callback');
      const authUrl = `${BASE_URL}/auth/google?app_redirect=${encodeURIComponent(redirectUrl)}`;

      const result = await WebBrowser.openAuthSessionAsync(authUrl, redirectUrl);

      if (result.type === 'cancel' || result.type === 'dismiss') {
        return; // User cancelled — do nothing
      }

      if (result.type !== 'success') {
        setError('Sign-in was not completed.');
        return;
      }

      const parsed = Linking.parse(result.url);
      const userId = parsed.queryParams?.user_id as string | undefined;
      const email = parsed.queryParams?.email as string | undefined;

      if (!userId || !email) {
        setError('Sign-in failed: missing user data in response.');
        return;
      }

      const authUser: AuthUser = { userId, email };
      await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(authUser));
      setUser(authUser);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign-in failed. Please try again.');
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    await AsyncStorage.removeItem(STORAGE_KEY);
    setUser(null);
    setError(null);
  }, []);

  return { user, loading, signingIn, error, signIn, signOut };
}
