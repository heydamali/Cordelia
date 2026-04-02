import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  Modal,
  TouchableOpacity,
  TextInput,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { startWhatsAppLink, getWhatsAppLinkStatus } from '../api/client';

type Screen = 'phone' | 'code' | 'success';

interface Props {
  visible: boolean;
  onClose: () => void;
}

export function WhatsAppLinkModal({ visible, onClose }: Props) {
  const [screen, setScreen] = useState<Screen>('phone');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [countryCode, setCountryCode] = useState('+1');
  const [pairingCode, setPairingCode] = useState('');
  const [countdown, setCountdown] = useState(60);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [linkedPhone, setLinkedPhone] = useState('');
  const [cooldown, setCooldown] = useState(0);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Reset state when modal opens
  useEffect(() => {
    if (visible) {
      setScreen('phone');
      setPhoneNumber('');
      setPairingCode('');
      setCountdown(60);
      setError('');
      setLoading(false);
      setCooldown(0);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, [visible]);

  const startCountdown = useCallback(() => {
    setCountdown(60);
    if (countdownRef.current) clearInterval(countdownRef.current);
    countdownRef.current = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          if (countdownRef.current) clearInterval(countdownRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const status = await getWhatsAppLinkStatus();
        if (status.status === 'connected') {
          if (pollRef.current) clearInterval(pollRef.current);
          if (countdownRef.current) clearInterval(countdownRef.current);
          setLinkedPhone(status.phone_number || '');
          setScreen('success');
        }
      } catch {
        // Keep polling
      }
    }, 3000);
  }, []);

  const handleContinue = async () => {
    const fullNumber = countryCode + phoneNumber.replace(/\s/g, '');
    if (phoneNumber.length < 7) {
      setError('Please enter a valid phone number');
      return;
    }

    setLoading(true);
    setError('');
    try {
      const result = await startWhatsAppLink(fullNumber);
      setPairingCode(formatCode(result.pairing_code));
      setScreen('code');
      startCountdown();
      startPolling();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to start linking';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const handleNewCode = async () => {
    if (cooldown > 0) return;

    setLoading(true);
    setError('');
    setCooldown(30);
    const cooldownTimer = setInterval(() => {
      setCooldown(prev => {
        if (prev <= 1) {
          clearInterval(cooldownTimer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    const fullNumber = countryCode + phoneNumber.replace(/\s/g, '');
    try {
      const result = await startWhatsAppLink(fullNumber);
      setPairingCode(formatCode(result.pairing_code));
      startCountdown();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to get new code';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const formatCode = (code: string): string => {
    const clean = code.replace(/[^A-Z0-9]/gi, '');
    if (clean.length <= 4) return clean;
    return clean.slice(0, 4) + '-' + clean.slice(4);
  };

  if (!visible) return null;

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <KeyboardAvoidingView
        style={styles.overlay}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <View style={styles.sheet}>
          {screen === 'phone' && (
            <>
              <View style={styles.header}>
                <Text style={styles.title}>Link WhatsApp</Text>
                <TouchableOpacity onPress={onClose}>
                  <Text style={styles.closeBtn}>Cancel</Text>
                </TouchableOpacity>
              </View>

              <Text style={styles.description}>
                Enter the phone number associated with your WhatsApp account.
              </Text>

              <View style={styles.phoneRow}>
                <TextInput
                  style={styles.countryCodeInput}
                  value={countryCode}
                  onChangeText={setCountryCode}
                  keyboardType="phone-pad"
                  maxLength={4}
                />
                <TextInput
                  style={styles.phoneInput}
                  value={phoneNumber}
                  onChangeText={setPhoneNumber}
                  placeholder="Phone number"
                  placeholderTextColor="#8E8E93"
                  keyboardType="phone-pad"
                  autoFocus
                />
              </View>

              {error ? <Text style={styles.error}>{error}</Text> : null}

              <TouchableOpacity
                style={[styles.primaryBtn, loading && styles.btnDisabled]}
                onPress={handleContinue}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator color="#FFFFFF" />
                ) : (
                  <Text style={styles.primaryBtnText}>Continue</Text>
                )}
              </TouchableOpacity>
            </>
          )}

          {screen === 'code' && (
            <>
              <View style={styles.header}>
                <Text style={styles.title}>Enter Code in WhatsApp</Text>
                <TouchableOpacity onPress={onClose}>
                  <Text style={styles.closeBtn}>Cancel</Text>
                </TouchableOpacity>
              </View>

              <Text style={styles.codeDisplay}>{pairingCode}</Text>

              <Text style={styles.countdown}>
                {countdown > 0 ? `Code expires in ${countdown}s` : 'Code expired'}
              </Text>

              <View style={styles.instructions}>
                <Text style={styles.step}>1. Open WhatsApp</Text>
                <Text style={styles.step}>2. Tap  &#8942;  &gt; Linked Devices</Text>
                <Text style={styles.step}>3. Tap "Link a Device"</Text>
                <Text style={styles.step}>4. Tap "Link with phone number instead"</Text>
                <Text style={styles.step}>5. Enter the code above</Text>
              </View>

              {error ? <Text style={styles.error}>{error}</Text> : null}

              <TouchableOpacity
                style={[styles.secondaryBtn, (cooldown > 0 || loading) && styles.btnDisabled]}
                onPress={handleNewCode}
                disabled={cooldown > 0 || loading}
              >
                <Text style={styles.secondaryBtnText}>
                  {cooldown > 0 ? `New Code (${cooldown}s)` : 'New Code'}
                </Text>
              </TouchableOpacity>

              <ActivityIndicator style={{ marginTop: 16 }} color="#25D366" />
              <Text style={styles.waitingText}>Waiting for connection...</Text>
            </>
          )}

          {screen === 'success' && (
            <>
              <View style={styles.successContent}>
                <Text style={styles.successIcon}>&#10003;</Text>
                <Text style={styles.successTitle}>WhatsApp Connected!</Text>
                {linkedPhone ? (
                  <Text style={styles.successPhone}>{linkedPhone}</Text>
                ) : null}
                <Text style={styles.successMsg}>
                  Your messages will start appearing as tasks.
                </Text>
              </View>

              <TouchableOpacity style={styles.primaryBtn} onPress={onClose}>
                <Text style={styles.primaryBtnText}>Done</Text>
              </TouchableOpacity>
            </>
          )}
        </View>
      </KeyboardAvoidingView>
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
    minHeight: 380,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
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
  description: {
    fontSize: 15,
    color: '#6C6C70',
    marginBottom: 20,
    lineHeight: 21,
  },
  phoneRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 16,
  },
  countryCodeInput: {
    width: 60,
    height: 48,
    borderWidth: 1,
    borderColor: '#E5E5EA',
    borderRadius: 10,
    paddingHorizontal: 12,
    fontSize: 16,
    color: '#1C1C1E',
    textAlign: 'center',
  },
  phoneInput: {
    flex: 1,
    height: 48,
    borderWidth: 1,
    borderColor: '#E5E5EA',
    borderRadius: 10,
    paddingHorizontal: 14,
    fontSize: 16,
    color: '#1C1C1E',
  },
  error: {
    fontSize: 13,
    color: '#FF3B30',
    marginBottom: 12,
  },
  primaryBtn: {
    backgroundColor: '#25D366',
    borderRadius: 12,
    height: 50,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 8,
  },
  primaryBtnText: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: '600',
  },
  btnDisabled: {
    opacity: 0.5,
  },
  // Code screen
  codeDisplay: {
    fontSize: 36,
    fontWeight: '800',
    color: '#1C1C1E',
    textAlign: 'center',
    letterSpacing: 4,
    marginVertical: 16,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  countdown: {
    fontSize: 14,
    color: '#8E8E93',
    textAlign: 'center',
    marginBottom: 20,
  },
  instructions: {
    backgroundColor: '#F5F5F7',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  step: {
    fontSize: 14,
    color: '#3A3A3C',
    lineHeight: 24,
  },
  secondaryBtn: {
    borderWidth: 1,
    borderColor: '#25D366',
    borderRadius: 12,
    height: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  secondaryBtnText: {
    color: '#25D366',
    fontSize: 15,
    fontWeight: '600',
  },
  waitingText: {
    fontSize: 13,
    color: '#8E8E93',
    textAlign: 'center',
    marginTop: 6,
  },
  // Success screen
  successContent: {
    alignItems: 'center',
    paddingVertical: 32,
  },
  successIcon: {
    fontSize: 48,
    color: '#25D366',
    marginBottom: 16,
  },
  successTitle: {
    fontSize: 22,
    fontWeight: '700',
    color: '#1C1C1E',
    marginBottom: 8,
  },
  successPhone: {
    fontSize: 16,
    color: '#8E8E93',
    marginBottom: 12,
  },
  successMsg: {
    fontSize: 15,
    color: '#6C6C70',
    textAlign: 'center',
    lineHeight: 21,
  },
});
