import { WAMessage, WASocket, jidNormalizedUser } from "@whiskeysockets/baileys";
import { publishCeleryTask } from "./celery-publisher";
import pino from "pino";

const logger = pino({ name: "message-handler" });

const CELERY_TASK_NAME = "app.tasks.whatsapp_tasks.ingest_whatsapp_messages";
const DEBOUNCE_MS = 10_000;
const MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes — skip old messages on reconnect

interface BufferedMessage {
  source_id: string;
  sender_name: string | null;
  sender_handle: string | null;
  body_text: string | null;
  sent_at: string;
  is_from_user: boolean;
  raw_metadata: Record<string, unknown>;
}

// Per-(userId, remoteJid) debounce buffer
const buffers = new Map<
  string,
  { messages: BufferedMessage[]; timer: NodeJS.Timeout; subject: string | null }
>();

function bufferKey(userId: string, remoteJid: string): string {
  return `${userId}:${remoteJid}`;
}

function extractBodyText(msg: WAMessage): string | null {
  const m = msg.message;
  if (!m) return null;

  // Text message
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;

  // Voice note / audio
  if (m.audioMessage) {
    const seconds = m.audioMessage.seconds || 0;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    const duration = `${mins}:${secs.toString().padStart(2, "0")}`;
    return `[Voice note - ${duration}]`;
  }

  // Image
  if (m.imageMessage) {
    const caption = m.imageMessage.caption;
    return caption ? `[Image: ${caption}]` : "[Image]";
  }

  // Video
  if (m.videoMessage) {
    const caption = m.videoMessage.caption;
    return caption ? `[Video: ${caption}]` : "[Video]";
  }

  // Document
  if (m.documentMessage) {
    const filename = m.documentMessage.fileName || "file";
    return `[Document: ${filename}]`;
  }

  // Location
  if (m.locationMessage) {
    const lat = m.locationMessage.degreesLatitude;
    const lng = m.locationMessage.degreesLongitude;
    return `[Location: ${lat}, ${lng}]`;
  }

  // Contact
  if (m.contactMessage) {
    const name = m.contactMessage.displayName || "Unknown";
    return `[Contact: ${name}]`;
  }

  // Stickers, GIFs — skip
  if (m.stickerMessage) return null;

  return null;
}

function extractRawMetadata(msg: WAMessage, isGroup: boolean, groupName: string | null): Record<string, unknown> {
  const m = msg.message;
  const meta: Record<string, unknown> = {
    message_type: "text",
    is_group: isGroup,
  };

  if (groupName) meta.group_name = groupName;

  if (m?.audioMessage) {
    meta.message_type = "audio";
    meta.has_audio = true;
    meta.audio_duration = m.audioMessage.seconds || 0;
  } else if (m?.imageMessage) {
    meta.message_type = "image";
  } else if (m?.videoMessage) {
    meta.message_type = "video";
  } else if (m?.documentMessage) {
    meta.message_type = "document";
  } else if (m?.locationMessage) {
    meta.message_type = "location";
  } else if (m?.contactMessage) {
    meta.message_type = "contact";
  } else if (m?.stickerMessage) {
    meta.message_type = "sticker";
  }

  return meta;
}

function phoneFromJid(jid: string): string {
  return jid.replace(/@.*$/, "");
}

async function flushBuffer(userId: string, remoteJid: string): Promise<void> {
  const key = bufferKey(userId, remoteJid);
  const buf = buffers.get(key);
  if (!buf || buf.messages.length === 0) {
    buffers.delete(key);
    return;
  }

  const payload = {
    source: "whatsapp",
    user_id: userId,
    conversation_source_id: remoteJid,
    subject: buf.subject,
    messages: buf.messages,
  };

  buffers.delete(key);

  try {
    const taskId = await publishCeleryTask(CELERY_TASK_NAME, { payload_dict: payload });
    logger.info({ taskId, userId, remoteJid, count: payload.messages.length }, "Published Celery task");
  } catch (err) {
    logger.error({ err, userId, remoteJid }, "Failed to publish Celery task");
  }
}

/**
 * Register message handler on a Baileys socket for a given userId.
 */
export function registerMessageHandler(sock: WASocket, userId: string): void {
  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return; // Only process real-time messages

    for (const msg of messages) {
      const remoteJid = msg.key.remoteJid;
      if (!remoteJid) continue;

      // Skip status broadcasts
      if (remoteJid === "status@broadcast") continue;

      // Skip old messages (avoids flood on reconnect)
      const timestamp = typeof msg.messageTimestamp === "number"
        ? msg.messageTimestamp
        : Number(msg.messageTimestamp);
      const ageMs = Date.now() - timestamp * 1000;
      if (ageMs > MAX_AGE_MS) continue;

      const bodyText = extractBodyText(msg);
      // Skip messages with no extractable content (stickers, etc.)
      if (bodyText === null) continue;

      const isGroup = remoteJid.endsWith("@g.us");
      const senderJid = isGroup ? msg.key.participant || remoteJid : remoteJid;
      const groupName = isGroup ? (msg.key as Record<string, unknown>).remoteJid as string | null : null;

      const bufferedMsg: BufferedMessage = {
        source_id: msg.key.id || `${timestamp}-${remoteJid}`,
        sender_name: msg.pushName || null,
        sender_handle: phoneFromJid(senderJid),
        body_text: bodyText,
        sent_at: new Date(timestamp * 1000).toISOString(),
        is_from_user: msg.key.fromMe === true,
        raw_metadata: extractRawMetadata(msg, isGroup, groupName),
      };

      const key = bufferKey(userId, remoteJid);
      const existing = buffers.get(key);

      if (existing) {
        clearTimeout(existing.timer);
        existing.messages.push(bufferedMsg);
        existing.timer = setTimeout(() => flushBuffer(userId, remoteJid), DEBOUNCE_MS);
      } else {
        // Determine subject: pushName for DMs, will be enriched for groups
        const subject = msg.pushName || phoneFromJid(remoteJid);
        const timer = setTimeout(() => flushBuffer(userId, remoteJid), DEBOUNCE_MS);
        buffers.set(key, { messages: [bufferedMsg], timer, subject });
      }
    }
  });
}
