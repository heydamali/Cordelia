export const config = {
  redisUrl: process.env.REDIS_URL || "redis://localhost:6379",
  backendUrl: process.env.BACKEND_URL || "http://localhost:8000",
  serviceApiKey: process.env.SERVICE_API_KEY || "",
  port: parseInt(process.env.PORT || "3000", 10),
};
