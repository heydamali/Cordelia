# Cordelia

Your personal AI executive assistant.

Cordelia connects to your Gmail and Google Calendar, reads through your emails and upcoming events, and extracts the things you actually need to do — replies to send, meetings to prepare for, deadlines to hit. It shows up as a simple task list on your phone, sorted by priority, with swipe actions to mark done, snooze, or ignore.

No manual entry. No tagging. You just open the app and see what needs your attention.

---

## What It Does

### Turns your inbox into a task list
Cordelia watches your Gmail in real time. When a new email arrives, it runs through Claude (Anthropic's AI) to decide: is this something you need to act on, or is it a newsletter/receipt/notification you can ignore? Actionable emails become tasks with a title, priority, category, and deadline.

### Reads your calendar and flags what matters
Not every meeting needs prep, but some do. Cordelia syncs your Google Calendar and uses AI to distinguish routine standups from important reviews that need preparation. It creates tasks like "Prepare for Q1 Review" or "RSVP to dinner" with appropriate deadlines.

### Prioritizes and reminds you
Each task gets a priority (high / medium / low) and an AI-determined reminder schedule. High-priority items with near deadlines get reminders sooner. Low-priority FYIs get a gentle nudge the next morning. Overdue appointments auto-transition to "missed."

### Smart completion detection
Before sending a reminder, Cordelia re-checks the source — if you already replied to that email or the event passed, it quietly marks the task done instead of nagging you.

### Snooze and resurface
Swipe right to snooze a task. Pick "later today," "tomorrow morning," or "next Monday." It disappears from your list and comes back automatically when the time arrives.

---

## How It Works

```
Gmail / Calendar
      |
   push notification (real-time)
      |
   Celery worker fetches full content
      |
   Stores conversation + messages (PostgreSQL)
      |
   Claude Haiku analyzes and extracts tasks
      |
   Tasks appear in the mobile app
      |
   Every 30 min: resurface snoozed tasks,
   fire reminders, expire overdue items
```

**Data sources:** Gmail (via Pub/Sub push) and Google Calendar (via webhook push). Both sync in real time — you don't need to manually refresh.

**AI layer:** Claude Haiku 4.5 decides what deserves a task and what to ignore. Emails get categorized as reply / appointment / action / info / ignored. Calendar events get filtered for prep tasks, RSVPs, and follow-ups — routine recurring meetings are skipped.

**Source toggle:** Each data source can be enabled or disabled independently from the settings screen (gear icon). Disabling a source hides its tasks and stops processing new notifications from that source.

---

## The Mobile App

A React Native (Expo) app with a focused, swipeable task list.

- **Tabs** — filter by priority (High / Medium / Low) or see Missed appointments
- **Swipe left** — mark done (with haptic feedback)
- **Swipe right** — snooze (pick a time) or ignore
- **Pull to refresh** — fetch the latest tasks
- **Source icons** — each task shows whether it came from email or calendar
- **Settings** (gear icon) — toggle data sources on/off, sign out

On first launch after sign-in, the app shows a pull-down hint animation to teach the refresh gesture while your tasks are being processed in the background.

---

## Setup

### Prerequisites

- Python 3.12
- PostgreSQL and Redis (Docker Compose provided)
- A Google Cloud project with Gmail API and Calendar API enabled
- An Anthropic API key
- Node.js + Expo CLI (for the mobile app)

### 1. Clone and install

```bash
git clone <repo-url>
cd ea-agent

# Backend
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Mobile
cd ../mobile
npm install
```

### 2. Start Postgres and Redis

```bash
docker-compose up -d
```

### 3. Configure environment

```bash
cp .env.example .env
```

Fill in each value:

| Variable | How to get it |
|---|---|
| `DATABASE_URL` | `postgresql://ea_agent:ea_agent_secret@localhost:5432/ea_agent_db` |
| `REDIS_URL` | `redis://localhost:6379` |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → Credentials → OAuth 2.0 Client ID |
| `GOOGLE_CLIENT_SECRET` | Same place |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/google/callback` |
| `ENCRYPTION_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `GCP_PROJECT_ID` | Google Cloud Console → project selector |
| `PUBSUB_TOPIC` | `projects/<GCP_PROJECT_ID>/topics/gmail-push` |
| `PUBSUB_VERIFICATION_TOKEN` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `INGEST_API_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |

### 4. Run migrations

```bash
cd backend
alembic upgrade head
```

### 5. Google Cloud setup (one-time)

```bash
# Create Pub/Sub topic for Gmail push
gcloud pubsub topics create gmail-push --project=$GCP_PROJECT_ID

# Grant Gmail permission to publish
gcloud pubsub topics add-iam-policy-binding gmail-push \
  --project=$GCP_PROJECT_ID \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

# Start ngrok tunnel
ngrok http 8000

# Create push subscription (replace <ngrok-id>)
gcloud pubsub subscriptions create gmail-push-sub \
  --topic=gmail-push \
  --project=$GCP_PROJECT_ID \
  --push-endpoint="https://<ngrok-id>.ngrok.app/webhooks/gmail?token=$PUBSUB_VERIFICATION_TOKEN" \
  --ack-deadline=30
```

> **Note:** Free ngrok gives a new URL each restart. Update with:
> ```bash
> gcloud pubsub subscriptions modify-push-config gmail-push-sub \
>   --project=$GCP_PROJECT_ID \
>   --push-endpoint="https://<new-ngrok-id>.ngrok.app/webhooks/gmail?token=$PUBSUB_VERIFICATION_TOKEN"
> ```

### 6. Start everything

Open four terminals from `ea-agent/backend`:

```bash
# API server
source venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Celery worker
source venv/bin/activate && celery -A app.celery_app:celery_app worker -l info

# Celery beat (scheduled jobs)
source venv/bin/activate && celery -A app.celery_app:celery_app beat -l info

# ngrok tunnel
ngrok http 8000
```

Then start the mobile app:

```bash
cd mobile
npx expo start
```

> Always restart the Celery worker after backend code changes — it does not hot-reload.

---

## Running Tests

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -v
```

Tests use in-memory SQLite — no PostgreSQL or Redis needed.

---

## Current Capabilities

| Feature | Status |
|---|---|
| Gmail real-time sync | Done |
| Google Calendar sync | Done |
| AI task extraction (Claude Haiku) | Done |
| Source toggle (enable/disable per source) | Done |
| Smart priority + deadline detection | Done |
| Snooze with auto-resurface | Done |
| AI completion detection before reminders | Done |
| Mobile app with swipe actions | Done |
| Push notifications (APNs) | Infrastructure ready, credentials pending |
| Additional sources (WhatsApp, Slack, etc.) | Future |
