import "dotenv/config";
import express from "express";
import pino from "pino";
import { config } from "./config";
import { sessionsRouter } from "./routes/sessions";
import { initCeleryPublisher, closeCeleryPublisher } from "./services/celery-publisher";
import { initAuthStoreRedis, closeAuthStore } from "./services/auth-store";
import { restoreAllSessions } from "./services/baileys-manager";

const logger = pino({ name: "whatsapp-service" });
const app = express();

app.use(express.json());

// Health check
app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

// Session management routes
app.use("/sessions", sessionsRouter);

async function start(): Promise<void> {
  // Initialize Redis connections
  initCeleryPublisher();
  initAuthStoreRedis();

  // Restore existing sessions from Redis auth state
  await restoreAllSessions();

  app.listen(config.port, () => {
    logger.info({ port: config.port }, "WhatsApp service started");
  });
}

// Graceful shutdown
process.on("SIGTERM", async () => {
  logger.info("Shutting down...");
  await closeCeleryPublisher();
  await closeAuthStore();
  process.exit(0);
});

start().catch((err) => {
  logger.fatal({ err }, "Failed to start service");
  process.exit(1);
});
