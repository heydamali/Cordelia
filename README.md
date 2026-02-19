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
                                              log (storage TBD)
```

**Stack:** FastAPI · PostgreSQL · Redis · Celery · Google OAuth2 · Gmail API · Google Pub/Sub

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

### 4. Run database migrations

```bash
cd backend
alembic upgrade head
```

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

---

## API Endpoints

| Method | URL | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/auth/google` | Start Google OAuth flow |
| GET | `/auth/google/callback?code=<code>` | OAuth callback — creates/updates user, registers Gmail watch |
| GET | `/gmail/threads?user_id=<id>` | List inbox threads (paginated, 20/page) |
| GET | `/gmail/threads/{thread_id}?user_id=<id>` | Fetch full thread with all messages |
| POST | `/webhooks/gmail?token=<secret>` | Gmail push notification receiver (called by Pub/Sub) |

### Gmail query examples

| Goal | URL |
|---|---|
| Primary tab only | `/gmail/threads?user_id=<id>&q=category:primary` |
| Unread only | `/gmail/threads?user_id=<id>&q=is:unread` |
| From a specific sender | `/gmail/threads?user_id=<id>&q=from:someone@example.com` |

---

## Running Tests

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -v
```

Tests use in-memory SQLite — no PostgreSQL or Redis needed.

---

## Where to Continue

### Current state (as of last commit)
- OAuth flow works end-to-end ✅
- Gmail watch is registered on login and renewed every 6 days via Celery Beat ✅
- Pub/Sub push notifications arrive at `/webhooks/gmail`, enqueue a Celery task ✅
- Task fetches the new thread via Gmail API and **logs it** ✅
- **No storage** — fetched threads are not persisted anywhere yet

### Next steps

1. **Thread storage model** — create an `emails` (or `threads`) table to persist fetched threads and messages. Add an Alembic migration.

2. **Idempotency** — before inserting a thread, check if it already exists (Gmail can deliver the same notification more than once).

3. **LLM processing** — once threads are stored, pipe them through the Anthropic API (`app/services/llm_processor.py` is already stubbed out) to extract tasks, priorities, draft replies, etc.

4. **Redis lock for historyId race condition** — two simultaneous notifications for the same user both use the same `start_history_id` cursor. Add a Redis lock in `process_gmail_notification` to serialize processing per user.

5. **Frontend / agent interface** — surface the processed emails to the user.
