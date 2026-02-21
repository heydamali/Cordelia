# Cordelia

Personal AI assistant powered by your inbox and LLMs.

Cordelia connects to everywhere information reaches you — email, work apps, social media, and more — and acts as the executive assistant you never had.

---

## Architecture

```
Gmail ──push──▶ Google Pub/Sub ──HTTP POST──▶ /webhooks/gmail
                                                     │
                                              Celery task queue
                                              (Redis lock per user)
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
                              upsert tasks with notify_at schedule
                                        (PostgreSQL storage)

Celery Beat (every 30 min) ──▶ process_task_deadlines
   │
   ├── Pass 1: re-surface tasks whose snoozed_until has passed
   ├── Pass 2: fire notify_at reminders (source refresh + LLM completion check first)
   └── Pass 3: expire overdue pending tasks → status=expired
```

**Stack:** FastAPI · PostgreSQL · Redis · Celery · Google OAuth2 · Gmail API · Google Pub/Sub · Anthropic API

---

## What's Built

### Connector pipeline (Gmail → DB)
- Gmail push notifications arrive via Google Pub/Sub → `/webhooks/gmail`
- A per-user Redis distributed lock prevents duplicate processing when two notifications arrive simultaneously with the same `historyId` cursor
- A Celery task fetches the full thread from the Gmail API
- The thread is normalised and written to PostgreSQL via the ingest service
- Duplicate notifications are handled safely — same message is never stored twice
- Gmail watches are renewed automatically every 6 days via Celery Beat

### Storage layer
Five tables store all data:

| Table | Purpose |
|---|---|
| `users` | Google OAuth credentials, Gmail watch state, APNs device token |
| `conversations` | One row per thread/chat — holds subject, snippet, last message time |
| `messages` | One row per individual message — sender, body, metadata |
| `tasks` | One row per extracted task — title, category, priority, status, deadline, notification schedule |

The schema is source-agnostic: `source` + `source_id` columns mean WhatsApp, Telegram, etc. can plug in without schema changes.

### LLM processing layer
After every ingest (from Gmail push or `POST /ingest`), a second Celery task is queued asynchronously:

- Loads the conversation + messages from PostgreSQL
- Injects `TODAY: <date>` as the first prompt line so the LLM can resolve relative dates correctly
- Sends everything to `claude-haiku-4-5-20251001` with a structured prompt
- Extracts a list of tasks: `reply`, `appointment`, `action`, `info`, or `ignored`
- Determines a `notify_at` schedule (0–3 ISO-8601 UTC datetimes per task) — timed by the LLM based on task urgency and deadline; ignored tasks always get `[]`
- Upserts tasks idempotently — priority only bumps up for pending tasks, done/snoozed tasks are not re-opened, ignored tasks are never re-touched
- Passes existing `task_key` values to the LLM for deduplication across follow-up emails

Failures are handled cleanly: transient API errors retry up to 3 times; JSON parse failures are dropped (logged, no retry).

### Deadline intelligence (Celery Beat — every 30 min)
Three ordered passes run on a 30-minute Beat schedule:

**Pass 1 — Re-surface snoozed tasks**
Tasks with `status=snoozed` and an expired `snoozed_until` are set back to `pending` automatically. Pass 1 runs before Pass 3 so a just-resurfaced overdue task is expired in the same Beat cycle.

**Pass 2 — Fire LLM-scheduled reminders**
For each pending task, any `notify_at` datetime that has passed but hasn't been sent yet triggers:
1. A source refresh — re-fetches the Gmail thread to ingest any new replies
2. A LLM completion check (`claude-haiku-4-5-20251001`) — judges whether the user has already completed the task even without tapping "done" (distinguishes "I'll be there Thursday" from "Which Thursday did you mean?")
3. If resolved: task is auto-closed (`status=done`), notification skipped
4. If not resolved: push notification fired via APNs stub (logs only until credentials wired)

`notifications_sent` is reassigned (never mutated) after each send to ensure SQLAlchemy detects the change.

**Pass 3 — Expire overdue tasks**
Tasks with `status=pending` and a `due_at` in the past are set to `status=expired`. This is a system-only transition — users cannot manually expire tasks via the API.

### Push notification infrastructure (APNs-ready)
- `POST /users/push-token` — mobile app registers its APNs device token
- `notify_task_reminder()` composes the notification body (e.g. "Reply to Alice — Due in 2h") and dispatches via `send_push_notification()`
- `send_push_notification()` is currently a logging stub — wire to APNs when credentials are available; the interface is stable

### Task management API
Full REST API for the mobile app to display and act on tasks:

- `GET /tasks` — list tasks with filtering by status (including `expired`), category, priority; sorted high→medium→low then by due date
- `PATCH /tasks/{id}` — update status; `snoozed` + `snoozed_until` stores a resurface time; `done`/`pending`/`ignored` clears it; `expired` is system-only and rejected from the PATCH body

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

This creates the full schema: `users`, `conversations`, `messages`, `tasks` (with `notify_at`, `notifications_sent`, `snoozed_until`, `push_token`).

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

# Tab 3 — Celery beat (schedules watch renewals + deadline processing)
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
| GET | `/tasks?user_id=<id>` | None | List tasks (filter by status/category/priority) |
| PATCH | `/tasks/{task_id}?user_id=<id>` | None | Update task status; `snoozed` accepts `snoozed_until` |
| POST | `/users/push-token` | None | Register APNs device token for push notifications |

### Task query examples

| Goal | URL |
|---|---|
| Pending tasks (default) | `/tasks?user_id=<id>` |
| High-priority only | `/tasks?user_id=<id>&priority=high` |
| Reply tasks only | `/tasks?user_id=<id>&category=reply` |
| All statuses including expired | `/tasks?user_id=<id>&status=all` |
| Expired tasks | `/tasks?user_id=<id>&status=expired` |

### Snooze a task until a specific time

```bash
curl -X PATCH "http://localhost:8000/tasks/<task-id>?user_id=<id>" \
  -H "Content-Type: application/json" \
  -d '{"status": "snoozed", "snoozed_until": "2026-03-01T09:00:00Z"}'
```

The Beat task will automatically resurface it to `pending` after that time.

### Register a push token

```bash
curl -X POST http://localhost:8000/users/push-token \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<id>", "push_token": "<apns-device-token>"}'
```

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

Tests use in-memory SQLite — no PostgreSQL or Redis needed. 160 tests, all green.

---

## Verify tasks in DB

```sql
-- Connect: psql postgresql://ea_agent:ea_agent_secret@localhost:5432/ea_agent_db

SELECT title, category, priority, status, due_at, snoozed_until,
       notify_at, notifications_sent
FROM tasks
ORDER BY created_at DESC;
```

Or trigger the deadline Beat task manually:

```python
from app.tasks.deadline_tasks import process_task_deadlines
process_task_deadlines()
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
| Redis lock for historyId race condition | ✅ |
| Task management API (GET/PATCH /tasks) | ✅ |
| LLM-determined reminder schedule (notify_at) | ✅ |
| TODAY injection for accurate relative date resolution | ✅ |
| Deadline Beat task (snooze resurface, notify, expire) | ✅ |
| Source refresh + LLM completion check before notifying | ✅ |
| APNs push notification infrastructure (stub) | ✅ |
| Device token registration (POST /users/push-token) | ✅ |
| APNs credentials + live push delivery | Future |
| Additional connectors (WhatsApp, Telegram) | Future |
| Phone call escalation for critical tasks | Future |

## Next Steps

1. **Wire APNs credentials** — `send_push_notification()` in `notification_service.py` is a logging stub. Plug in APNs credentials (p8 key + key ID + team ID) and swap the stub for a real HTTP/2 call to the APNs gateway. The rest of the stack (device token registration, reminder schedule, Beat dispatcher) is already live.

2. **LLM prompt tuning** — review real task output in the DB and iterate on the system prompt: category boundaries, priority thresholds, `task_key` slug quality, `notify_at` timing heuristics, handling of long threads.

3. **Task status feedback loop** — optionally trigger a follow-up action when a task is marked `done` (e.g. draft a reply, confirm a calendar invite). The upsert rules already protect done/snoozed tasks from being re-opened by subsequent LLM runs.

4. **Additional connectors** — WhatsApp, Telegram, Instagram etc. can call `POST /ingest` directly with their own `source` value. No schema changes needed.

5. **Mobile app** — the task API is ready: `GET /tasks` with filtering, `PATCH /tasks/{id}` for status updates including snooze-with-resurface, push token registration. Next is building the iOS client against these endpoints.
