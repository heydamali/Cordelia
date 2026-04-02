import Redis from "ioredis";
import { v4 as uuidv4 } from "uuid";
import { config } from "../config";

let redis: Redis;

export function initCeleryPublisher(): void {
  redis = new Redis(config.redisUrl);
}

interface CeleryMessage {
  id: string;
  task: string;
  args: unknown[];
  kwargs: Record<string, unknown>;
  retries: number;
  eta: null;
  expires: null;
}

/**
 * Publish a Celery-compatible task message to the default Redis queue.
 *
 * Celery expects messages as JSON with specific headers. We LPUSH to the
 * "celery" list, which is the default queue Celery workers consume from.
 */
export async function publishCeleryTask(
  taskName: string,
  kwargs: Record<string, unknown>,
): Promise<string> {
  const taskId = uuidv4();

  const message: CeleryMessage = {
    id: taskId,
    task: taskName,
    args: [],
    kwargs,
    retries: 0,
    eta: null,
    expires: null,
  };

  // Celery expects the message body wrapped in a specific envelope
  const envelope = {
    body: Buffer.from(JSON.stringify([[], kwargs, { callbacks: null, errbacks: null, chain: null }])).toString("base64"),
    "content-encoding": "utf-8",
    "content-type": "application/json",
    headers: {
      lang: "py",
      task: taskName,
      id: taskId,
      root_id: taskId,
      parent_id: null,
      group: null,
      retries: 0,
      eta: null,
      expires: null,
      argsrepr: "[]",
      kwargsrepr: JSON.stringify(kwargs).slice(0, 200),
    },
    properties: {
      correlation_id: taskId,
      reply_to: "",
      delivery_mode: 2,
      delivery_info: { exchange: "", routing_key: "celery" },
      priority: 0,
      body_encoding: "base64",
      delivery_tag: uuidv4(),
    },
  };

  await redis.lpush("celery", JSON.stringify(envelope));
  return taskId;
}

export async function closeCeleryPublisher(): Promise<void> {
  if (redis) await redis.quit();
}
