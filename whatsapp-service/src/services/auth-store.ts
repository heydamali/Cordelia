import {
  AuthenticationCreds,
  AuthenticationState,
  SignalDataTypeMap,
  initAuthCreds,
  proto,
  BufferJSON,
} from "@whiskeysockets/baileys";
import Redis from "ioredis";
import { config } from "../config";

let redis: Redis;

export function initAuthStoreRedis(): void {
  redis = new Redis(config.redisUrl);
}

function keyPrefix(userId: string): string {
  return `whatsapp:auth:${userId}`;
}

/**
 * Build a Baileys AuthenticationState backed by Redis.
 * Keys: whatsapp:auth:{userId}:creds and whatsapp:auth:{userId}:{type}-{id}
 */
export async function useRedisAuthState(
  userId: string,
): Promise<{ state: AuthenticationState; saveCreds: () => Promise<void> }> {
  const prefix = keyPrefix(userId);

  const readData = async (key: string): Promise<unknown | null> => {
    const raw = await redis.get(`${prefix}:${key}`);
    if (!raw) return null;
    return JSON.parse(raw, BufferJSON.reviver);
  };

  const writeData = async (key: string, data: unknown): Promise<void> => {
    await redis.set(`${prefix}:${key}`, JSON.stringify(data, BufferJSON.replacer));
  };

  const removeData = async (key: string): Promise<void> => {
    await redis.del(`${prefix}:${key}`);
  };

  const creds: AuthenticationCreds =
    ((await readData("creds")) as AuthenticationCreds) || initAuthCreds();

  return {
    state: {
      creds,
      keys: {
        get: async <T extends keyof SignalDataTypeMap>(
          type: T,
          ids: string[],
        ): Promise<{ [id: string]: SignalDataTypeMap[T] }> => {
          const result: { [id: string]: SignalDataTypeMap[T] } = {};
          for (const id of ids) {
            const value = await readData(`${type}-${id}`);
            if (value) {
              if (type === "app-state-sync-key" && value) {
                result[id] = proto.Message.AppStateSyncKeyData.fromObject(
                  value as Record<string, unknown>,
                ) as unknown as SignalDataTypeMap[T];
              } else {
                result[id] = value as SignalDataTypeMap[T];
              }
            }
          }
          return result;
        },
        set: async (data: Record<string, Record<string, unknown>>): Promise<void> => {
          for (const [type, entries] of Object.entries(data)) {
            for (const [id, value] of Object.entries(entries)) {
              if (value) {
                await writeData(`${type}-${id}`, value);
              } else {
                await removeData(`${type}-${id}`);
              }
            }
          }
        },
      },
    },
    saveCreds: async () => {
      await writeData("creds", creds);
    },
  };
}

/** List all user IDs that have stored auth state. */
export async function listAuthenticatedUsers(): Promise<string[]> {
  const keys = await redis.keys("whatsapp:auth:*:creds");
  return keys.map((k) => {
    const parts = k.split(":");
    return parts[2]; // whatsapp:auth:{userId}:creds
  });
}

/** Remove all auth keys for a user. */
export async function clearAuthState(userId: string): Promise<void> {
  const prefix = keyPrefix(userId);
  const keys = await redis.keys(`${prefix}:*`);
  if (keys.length > 0) {
    await redis.del(...keys);
  }
}

export async function closeAuthStore(): Promise<void> {
  if (redis) await redis.quit();
}
