# Cordelia

Personal AI assistant powered by your inbox and LLMs.

Cordelia connects to everywhere information reaches you — email, work apps, social media, and more — and acts as the executive assistant you never had.

---

## Architecture

```
Gmail ──push──▶ Google Pub/Sub ──HTTP POST──▶ /webhooks/gmail
                                                     │
                                              Celery task queue
                                                     │
                                         fetch thread via Gmail API
                                                     │
                                          POST /ingest (internal)
                                                     │
                                   upsert conversations + messages
                                        (PostgreSQL storage)
                                                     │
                              process_conversation_with_llm.delay()
                                                     │
                                    [Celery worker — async]
                                                     │
                                  claude-haiku-4-5 (Anthropic API)
                                                     │
                                    upsert tasks table (PostgreSQL)
```

**Stack:** FastAPI · PostgreSQL · Redis · Celery · Google OAuth2 · Gmail API · Google Pub/Sub · Anthropic API

---

## What's Built

### Connector pipeline (Gmail → DB)
- Gmail push notifications arrive via Google Pub/Sub → `/webhooks/gmail`
- A Celery task fetches the full thread from the Gmail API
- The thread is normalised and written to PostgreSQL via the ingest service
- Duplicate notifications are handled safely — same message is never stored twice
- Gmail watches are renewed automatically every 6 days via Celery Beat

### Storage layer
Three tables store all data:

| Table | Purpose |
|---|---|
| `conversations` | One row per thread/chat — holds subject, snippet, last message time |
| `messages` | One row per individual message — sender, body, metadata |
| `tasks` | One row per extracted task — title, category, priority, status, LLM output |

The schema is source-agnostic: `source` + `source_id` columns mean WhatsApp, Telegram, etc. can plug in without schema changes.

### LLM processing layer
After every ingest (from Gmail push or `POST /ingest`), a second Celery task is queued asynchronously:

- Loads the conversation + messages from PostgreSQL
- Sends them to `claude-haiku-4-5-20251001` with a structured prompt
- Extracts a list of tasks: `reply`, `appointment`, `action`, `info`, or `ignored`
- Upserts tasks idempotently — priority only bumps up for pending tasks, done/snoozed tasks are not re-opened, ignored tasks are never re-touched
- Passes existing `task_key` values to the LLM for deduplication across follow-up emails

Failures are handled cleanly: transient API errors retry up to 3 times; JSON parse failures are dropped (logged, no retry).

### Ingest API (`POST /ingest`)
An internal HTTP endpoint that any connector service can call to write normalised messages into the storage layer. Authenticated via a shared `X-Ingest-Key` header.

```bash
curl -X POST http://localhost:8000/ingest \
  -H "X-Ingest-Key: <INGEST_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "gmail",
    "user_id": "<user-id>",
    "conversation_source_id": "<thread-id>",
    "subject": "Hello",
    "messages": [{
      "source_id": "<message-id>",
      "sender_name": "Alice",
      "sender_handle": "alice@example.com",
      "body_text": "Hi there",
      "sent_at": "2026-02-19T10:00:00Z",
      "is_from_user": false
    }]
  }'
```

---

## Prerequisites

- Python 3.12
- PostgreSQL running locally
- Redis running locally
- A Google Cloud project with the Gmail API enabled
- `gcloud` CLI installed and authenticated

---

## First-Time Setup (from scratch)

### 1. Clone and create virtualenv

```bash
git clone <repo-url>
cd ea-agent/backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the database and user

```bash
psql postgres -c "CREATE USER ea_agent WITH PASSWORD 'ea_agent_secret';"
psql postgres -c "CREATE DATABASE ea_agent_db OWNER ea_agent;"
```

### 3. Set up environment variables

```bash
cp .env.example .env   # file lives at repo root (ea-agent/.env)
```

Fill in each value:

| Variable | How to get it |
|---|---|
| `DATABASE_URL` | `postgresql://ea_agent:ea_agent_secret@localhost:5432/ea_agent_db` |
| `REDIS_URL` | `redis://localhost:6379` |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → APIs & Services → Credentials → your OAuth 2.0 Client ID |
| `GOOGLE_CLIENT_SECRET` | Same place as above |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/google/callback` |
| `ENCRYPTION_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `GCP_PROJECT_ID` | Google Cloud Console → project selector (e.g. `my-project-123456`) |
| `PUBSUB_TOPIC` | `projects/<GCP_PROJECT_ID>/topics/gmail-push` (create the topic first, see step 5) |
| `PUBSUB_VERIFICATION_TOKEN` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` — pick any long random string |
| `INGEST_API_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` — shared secret for the ingest endpoint |

### 4. Run database migrations

```bash
cd backend
alembic upgrade head
```

This creates four tables: `users`, `conversations`, `messages`, `tasks`.

### 5. Google Cloud one-time setup

```bash
# Create the Pub/Sub topic
gcloud pubsub topics create gmail-push --project=$GCP_PROJECT_ID

# Grant Gmail permission to publish to it
gcloud pubsub topics add-iam-policy-binding gmail-push --project=$GCP_PROJECT_ID --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" --role="roles/pubsub.publisher"

# Start ngrok, then create the push subscription (run as ONE line)
ngrok http 8000

gcloud pubsub subscriptions create gmail-push-sub --topic=gmail-push --project=$GCP_PROJECT_ID --push-endpoint="https://<ngrok-id>.ngrok.app/webhooks/gmail?token=$PUBSUB_VERIFICATION_TOKEN" --ack-deadline=30
```

> **Note:** The free ngrok tier gives a new URL each restart. Update the subscription each time:
> ```bash
> gcloud pubsub subscriptions modify-push-config gmail-push-sub --project=$GCP_PROJECT_ID --push-endpoint="https://<new-ngrok-id>.ngrok.app/webhooks/gmail?token=$PUBSUB_VERIFICATION_TOKEN"
> ```

---

## Daily Dev Workflow

Open four terminal tabs from `ea-agent/backend`:

```bash
# Tab 1 — API server
source venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Tab 2 — Celery worker (executes tasks)
source venv/bin/activate && celery -A app.celery_app:celery_app worker -l info

# Tab 3 — Celery beat (schedules watch renewals every 6 days)
source venv/bin/activate && celery -A app.celery_app:celery_app beat -l info

# Tab 4 — ngrok tunnel
ngrok http 8000
```

After ngrok starts, update the Pub/Sub subscription push endpoint with the new URL (see step 5 above).

> **Note:** Always restart the Celery worker after code changes — it does not hot-reload like the API server.

---

## API Endpoints

| Method | URL | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Health check |
| GET | `/auth/google` | None | Start Google OAuth flow |
| GET | `/auth/google/callback?code=<code>` | None | OAuth callback — creates/updates user, registers Gmail watch |
| GET | `/gmail/threads?user_id=<id>` | None | List inbox threads (paginated, 20/page) |
| GET | `/gmail/threads/{thread_id}?user_id=<id>` | None | Fetch full thread with all messages |
| POST | `/webhooks/gmail?token=<secret>` | Pub/Sub token | Gmail push notification receiver |
| POST | `/ingest` | `X-Ingest-Key` header | Write normalised messages from any connector |

### Gmail query examples

| Goal | URL |
|---|---|
| Primary tab only | `/gmail/threads?user_id=<id>&q=category:primary` |
| Unread only | `/gmail/threads?user_id=<id>&q=is:unread` |
| From a specific sender | `/gmail/threads?user_id=<id>&q=from:someone@example.com` |

### Verify what's stored

```sql
-- Connect: psql postgresql://ea_agent:ea_agent_secret@localhost:5432/ea_agent_db

SELECT source, subject, snippet, last_message_at FROM conversations ORDER BY last_message_at DESC;

SELECT c.subject, m.sender_handle, left(m.body_text, 80), m.sent_at
FROM messages m JOIN conversations c ON m.conversation_id = c.id
ORDER BY m.sent_at DESC;
```

---

## Running Tests

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -v
```

Tests use in-memory SQLite — no PostgreSQL or Redis needed.

---

## Verify tasks in DB

```sql
-- Connect: psql postgresql://ea_agent:ea_agent_secret@localhost:5432/ea_agent_db

SELECT title, category, priority, status, summary
FROM tasks
ORDER BY created_at DESC;
```

Or with Python:

```bash
python3 - << 'EOF'
from app.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    for row in conn.execute(text(
        "SELECT title, category, priority, status, summary FROM tasks ORDER BY created_at DESC"
    )):
        print(dict(row._mapping))
EOF
```

---

## Current State

| Capability | Status |
|---|---|
| Google OAuth login | ✅ |
| Gmail watch registration + auto-renewal | ✅ |
| Real-time push notifications via Pub/Sub | ✅ |
| Fetch threads from Gmail API | ✅ |
| Persist conversations + messages to PostgreSQL | ✅ |
| Idempotent ingest (safe to re-process) | ✅ |
| Source-agnostic ingest API | ✅ |
| LLM task extraction (Haiku, async Celery) | ✅ |
| Idempotent task upsert with priority rules | ✅ |
| Additional connectors (WhatsApp, Telegram) | Future |
| Mobile app / API to serve tasks | Next |
| Redis lock for historyId race condition | Next |

## Next Steps

1. **Mobile app API** — expose `GET /tasks?user_id=<id>` (with filtering by status/priority) so the Cordelia iOS app can display the task list. Also needs `PATCH /tasks/<id>` to mark tasks as done, snoozed, or ignored.

2. **Redis lock for historyId race condition** — two simultaneous Gmail push notifications for the same user will both use the same `start_history_id` cursor, potentially processing the same thread twice. Add a Redis distributed lock in `process_gmail_notification` to serialise per-user processing.

3. **Task status feedback loop** — when a user marks a task `done` or `snoozed` in the app, that status should survive subsequent LLM re-runs (already implemented in the upsert rules) and optionally trigger a follow-up action (e.g. draft a reply).

4. **LLM prompt tuning** — review real task output in the DB and iterate on the system prompt: category boundaries, priority thresholds, `task_key` slug quality, handling of long threads.

5. **Additional connectors** — WhatsApp, Telegram, Instagram etc. can call `POST /ingest` directly with their own `source` value. No schema changes needed.

6. **`due_at` extraction quality** — the LLM currently outputs ISO-8601 strings for `due_at`; consider adding the current date to the prompt context so relative references ("Thursday", "next week") resolve correctly.
