import React from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  SafeAreaView,
} from 'react-native';

interface Props {
  onSignIn: () => void;
  signingIn: boolean;
  error: string | null;
}

export function LoginScreen({ onSignIn, signingIn, error }: Props) {
  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.content}>
        {/* Brand */}
        <View style={styles.brand}>
          <Text style={styles.logo}>✉️</Text>
          <Text style={styles.appName}>Cordelia</Text>
          <Text style={styles.tagline}>Your email, turned into action</Text>
        </View>

        {/* Error */}
        {error ? (
          <View style={styles.errorBox}>
            <Text style={styles.errorText}>{error}</Text>
          </View>
        ) : null}

        {/* Sign-in button */}
        <TouchableOpacity
          style={[styles.googleButton, signingIn && styles.googleButtonDisabled]}
          onPress={onSignIn}
          disabled={signingIn}
          activeOpacity={0.8}
        >
          {signingIn ? (
            <ActivityIndicator color="#3C4043" size="small" />
          ) : (
            <Text style={styles.googleG}>G</Text>
          )}
          <Text style={styles.googleLabel}>
            {signingIn ? 'Opening Google…' : 'Continue with Google'}
          </Text>
        </TouchableOpacity>

        <Text style={styles.disclaimer}>
          Cordelia reads your Gmail to surface tasks.{'\n'}
          Your data is never sold or shared.
        </Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  content: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 32,
    gap: 24,
  },
  brand: {
    alignItems: 'center',
    marginBottom: 16,
    gap: 8,
  },
  logo: {
    fontSize: 56,
    marginBottom: 8,
  },
  appName: {
    fontSize: 36,
    fontWeight: '700',
    color: '#1C1C1E',
    letterSpacing: -0.5,
  },
  tagline: {
    fontSize: 16,
    color: '#8E8E93',
    textAlign: 'center',
  },
  errorBox: {
    backgroundColor: '#FFF0F0',
    borderRadius: 10,
    padding: 12,
    width: '100%',
  },
  errorText: {
    fontSize: 13,
    color: '#FF3B30',
    textAlign: 'center',
  },
  googleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#DADCE0',
    borderRadius: 24,
    paddingVertical: 13,
    paddingHorizontal: 24,
    gap: 12,
    width: '100%',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 3,
    elevation: 2,
  },
  googleButtonDisabled: {
    opacity: 0.6,
  },
  googleG: {
    fontSize: 18,
    fontWeight: '700',
    color: '#4285F4',
    width: 22,
    textAlign: 'center',
  },
  googleLabel: {
    fontSize: 15,
    fontWeight: '500',
    color: '#3C4043',
    flex: 1,
    textAlign: 'center',
  },
  disclaimer: {
    fontSize: 12,
    color: '#AEAEB2',
    textAlign: 'center',
    lineHeight: 18,
    marginTop: 8,
  },
});
