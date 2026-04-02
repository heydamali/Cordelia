import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  WASocket,
} from "@whiskeysockets/baileys";
import pino from "pino";
import { config } from "../config";
import { useRedisAuthState, clearAuthState, listAuthenticatedUsers } from "./auth-store";
import { registerMessageHandler } from "./message-handler";

const logger = pino({ name: "baileys-manager" });

type SessionStatus = "disconnected" | "pairing" | "connected";

interface SessionInfo {
  sock: WASocket;
  status: SessionStatus;
  phoneNumber?: string;
  pairingAttempts: number;
  lastPairingTime: number;
}

const sessions = new Map<string, SessionInfo>();

export function getSessionStatus(
  userId: string,
): { status: SessionStatus; phoneNumber?: string } {
  const session = sessions.get(userId);
  if (!session) return { status: "disconnected" };
  return { status: session.status, phoneNumber: session.phoneNumber };
}

/**
 * Create a Baileys socket and request a pairing code for the given phone number.
 * Returns the 8-digit pairing code.
 */
export async function startSession(
  userId: string,
  phoneNumber: string,
): Promise<string> {
  // Check cooldown
  const existing = sessions.get(userId);
  if (existing) {
    const elapsed = Date.now() - existing.lastPairingTime;
    if (elapsed < 30_000) {
      throw new Error("Please wait 30 seconds between pairing attempts");
    }
    if (existing.pairingAttempts >= 5) {
      throw new Error("Maximum pairing attempts reached. Please try again later");
    }
    // Clean up old socket
    existing.sock.end(undefined);
  }

  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useRedisAuthState(userId);

  const sock = makeWASocket({
    version,
    logger: pino({ level: "silent" }) as any,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, pino({ level: "silent" }) as any),
    },
    printQRInTerminal: false,
    mobile: false,
  });

  const sessionInfo: SessionInfo = {
    sock,
    status: "pairing",
    phoneNumber,
    pairingAttempts: (existing?.pairingAttempts || 0) + 1,
    lastPairingTime: Date.now(),
  };
  sessions.set(userId, sessionInfo);

  // Save credentials on update
  sock.ev.on("creds.update", saveCreds);

  // Handle connection updates
  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect } = update;

    if (connection === "open") {
      sessionInfo.status = "connected";
      logger.info({ userId }, "WhatsApp connected");

      // Notify backend
      try {
        await fetch(`${config.backendUrl}/whatsapp/webhook/connected`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Service-Key": config.serviceApiKey,
          },
          body: JSON.stringify({ user_id: userId, phone_number: phoneNumber }),
        });
      } catch (err) {
        logger.error({ err, userId }, "Failed to notify backend of connection");
      }
    }

    if (connection === "close") {
      const statusCode = (lastDisconnect?.error as any)?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

      if (statusCode === DisconnectReason.loggedOut) {
        logger.info({ userId }, "WhatsApp logged out, cleaning up");
        await clearAuthState(userId);
        sessions.delete(userId);

        // Notify backend
        try {
          await fetch(`${config.backendUrl}/whatsapp/webhook/disconnected`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Service-Key": config.serviceApiKey,
            },
            body: JSON.stringify({ user_id: userId, reason: "logged_out" }),
          });
        } catch (err) {
          logger.error({ err, userId }, "Failed to notify backend of disconnection");
        }
      } else if (shouldReconnect) {
        logger.info({ userId, statusCode }, "Connection lost, reconnecting...");
        // Reconnect with exponential backoff
        reconnectWithBackoff(userId, phoneNumber, 1);
      }
    }
  });

  // Register message handler
  registerMessageHandler(sock, userId);

  // Request pairing code (strip + and spaces from phone number)
  const cleanPhone = phoneNumber.replace(/[+\s-]/g, "");
  const code = await sock.requestPairingCode(cleanPhone);
  logger.info({ userId, code }, "Pairing code generated");

  return code;
}

async function reconnectWithBackoff(
  userId: string,
  phoneNumber: string,
  attempt: number,
): Promise<void> {
  const delay = Math.min(1000 * Math.pow(2, attempt - 1), 60_000);
  const maxAttemptsPerMinute = 3;

  if (attempt > maxAttemptsPerMinute * 10) {
    logger.error({ userId }, "Max reconnection attempts reached");
    sessions.delete(userId);

    try {
      await fetch(`${config.backendUrl}/whatsapp/webhook/disconnected`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Service-Key": config.serviceApiKey,
        },
        body: JSON.stringify({ user_id: userId, reason: "reconnect_failed" }),
      });
    } catch (err) {
      logger.error({ err, userId }, "Failed to notify backend of reconnect failure");
    }
    return;
  }

  setTimeout(async () => {
    try {
      const { state, saveCreds } = await useRedisAuthState(userId);
      const { version } = await fetchLatestBaileysVersion();

      const sock = makeWASocket({
        version,
        logger: pino({ level: "silent" }) as any,
        auth: {
          creds: state.creds,
          keys: makeCacheableSignalKeyStore(state.keys, pino({ level: "silent" }) as any),
        },
        printQRInTerminal: false,
        mobile: false,
      });

      const sessionInfo: SessionInfo = {
        sock,
        status: "connected",
        phoneNumber,
        pairingAttempts: 0,
        lastPairingTime: 0,
      };
      sessions.set(userId, sessionInfo);

      sock.ev.on("creds.update", saveCreds);

      sock.ev.on("connection.update", async (update) => {
        const { connection, lastDisconnect } = update;
        if (connection === "open") {
          sessionInfo.status = "connected";
          logger.info({ userId }, "Reconnected successfully");
        }
        if (connection === "close") {
          const code = (lastDisconnect?.error as any)?.output?.statusCode;
          if (code === DisconnectReason.loggedOut) {
            await clearAuthState(userId);
            sessions.delete(userId);
            try {
              await fetch(`${config.backendUrl}/whatsapp/webhook/disconnected`, {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "X-Service-Key": config.serviceApiKey,
                },
                body: JSON.stringify({ user_id: userId, reason: "logged_out" }),
              });
            } catch {}
          } else {
            reconnectWithBackoff(userId, phoneNumber, attempt + 1);
          }
        }
      });

      registerMessageHandler(sock, userId);
    } catch (err) {
      logger.error({ err, userId, attempt }, "Reconnection attempt failed");
      reconnectWithBackoff(userId, phoneNumber, attempt + 1);
    }
  }, delay);
}

export async function stopSession(userId: string): Promise<void> {
  const session = sessions.get(userId);
  if (session) {
    session.sock.end(undefined);
    sessions.delete(userId);
  }
  await clearAuthState(userId);
}

/**
 * On startup, restore sessions for all users with saved auth state.
 */
export async function restoreAllSessions(): Promise<void> {
  const userIds = await listAuthenticatedUsers();
  logger.info({ count: userIds.length }, "Restoring sessions on startup");

  for (const userId of userIds) {
    try {
      const { state, saveCreds } = await useRedisAuthState(userId);
      const { version } = await fetchLatestBaileysVersion();

      const sock = makeWASocket({
        version,
        logger: pino({ level: "silent" }) as any,
        auth: {
          creds: state.creds,
          keys: makeCacheableSignalKeyStore(state.keys, pino({ level: "silent" }) as any),
        },
        printQRInTerminal: false,
        mobile: false,
      });

      const sessionInfo: SessionInfo = {
        sock,
        status: "disconnected",
        pairingAttempts: 0,
        lastPairingTime: 0,
      };
      sessions.set(userId, sessionInfo);

      sock.ev.on("creds.update", saveCreds);

      sock.ev.on("connection.update", async (update) => {
        const { connection, lastDisconnect } = update;
        if (connection === "open") {
          sessionInfo.status = "connected";
          logger.info({ userId }, "Session restored and connected");
        }
        if (connection === "close") {
          const code = (lastDisconnect?.error as any)?.output?.statusCode;
          if (code === DisconnectReason.loggedOut) {
            await clearAuthState(userId);
            sessions.delete(userId);
            try {
              await fetch(`${config.backendUrl}/whatsapp/webhook/disconnected`, {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "X-Service-Key": config.serviceApiKey,
                },
                body: JSON.stringify({ user_id: userId, reason: "logged_out" }),
              });
            } catch {}
          }
        }
      });

      registerMessageHandler(sock, userId);
    } catch (err) {
      logger.error({ err, userId }, "Failed to restore session");
    }
  }
}
