import { Router, Request, Response } from "express";
import { config } from "../config";
import { startSession, stopSession, getSessionStatus } from "../services/baileys-manager";

export const sessionsRouter = Router();

// Auth middleware: verify X-Service-Key header
function requireServiceKey(req: Request, res: Response, next: () => void): void {
  const key = req.headers["x-service-key"];
  if (key !== config.serviceApiKey) {
    res.status(401).json({ error: "Invalid service key" });
    return;
  }
  next();
}

sessionsRouter.use(requireServiceKey);

/**
 * POST /sessions/start
 * Body: { userId: string, phoneNumber: string }
 * Returns: { pairingCode: string }
 */
sessionsRouter.post("/start", async (req: Request, res: Response) => {
  const { userId, phoneNumber } = req.body;

  if (!userId || !phoneNumber) {
    res.status(400).json({ error: "userId and phoneNumber are required" });
    return;
  }

  try {
    const pairingCode = await startSession(userId, phoneNumber);
    res.json({ pairingCode });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Failed to start session";
    res.status(400).json({ error: message });
  }
});

/**
 * GET /sessions/:userId/status
 * Returns: { status: "disconnected"|"pairing"|"connected", phoneNumber?: string }
 */
sessionsRouter.get("/:userId/status", (req: Request, res: Response) => {
  const userId = req.params.userId as string;
  const status = getSessionStatus(userId);
  res.json(status);
});

/**
 * DELETE /sessions/:userId
 * Disconnects and cleans up auth state for a user.
 */
sessionsRouter.delete("/:userId", async (req: Request, res: Response) => {
  const userId = req.params.userId as string;

  try {
    await stopSession(userId);
    res.json({ ok: true });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Failed to stop session";
    res.status(500).json({ error: message });
  }
});
