# 🤖 IGAUTO — Instagram Automation Telegram Bot

Automate Instagram archiving, unarchiving, and reach-limit post deletion — all via Telegram commands. Runs on [Railway](https://railway.app) with **zero server setup**.

No username/password stored. Auth is done via a **session JSON** you generate once locally.

---

## ✨ Features

| Command | Description |
|---|---|
| `/status` | Check account health & reach limit status |
| `/archive` | Archive all posts from the last N days |
| `/unarchive` | Restore every archived post |
| `/preview_kill` | Preview which posts are reach-limited |
| `/kill` | Permanently delete all reach-limit posts (with confirm button) |
| `/posts` | List recent posts with status flags |
| `/setdays <n>` | Change archive window (default: 7 days) |
| `/session` | Instructions for refreshing your session |

---

## 🚀 Deploy to Railway in 5 steps

### Step 1 — Fork this repo

Click **Fork** on GitHub, then go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → select your fork.

### Step 2 — Create your Telegram bot

1. Open Telegram → message **[@BotFather](https://t.me/BotFather)**
2. Send `/newbot` and follow the prompts
3. Copy your **bot token** (looks like `123456789:ABCdef...`)

### Step 3 — Get your Telegram user ID

Message **[@userinfobot](https://t.me/userinfobot)** on Telegram. Copy the numeric ID it shows you.

### Step 4 — Generate your Instagram session JSON

Run this **locally** (one time only):

```bash
pip install instagrapi
python get_session.py
```

It will print a JSON string. Copy the entire output between the `---` markers.

### Step 5 — Set Railway environment variables

In Railway → your project → **Variables**, add:

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `ALLOWED_USER_ID` | Your Telegram numeric user ID |
| `IG_SESSION_JSON` | The full JSON string from `get_session.py` |
| `IG_USERNAME` | Your Instagram username (display only) |
| `IG_USER_ID` | Your Instagram numeric user ID (from get_session.py output) |

Optional:

| Variable | Default | Description |
|---|---|---|
| `ARCHIVE_DAYS` | `7` | Default archive window in days |
| `API_DELAY` | `1.5` | Seconds between Instagram API calls |

**Click Deploy** — the bot will be live in ~60 seconds.

---

## 🔑 Session Management

Your session is stored **only in Railway's env vars** — nothing is committed to git.

Sessions typically last **90 days**. When it expires:
1. Run `get_session.py` locally again
2. Update `IG_SESSION_JSON` in Railway Variables
3. Railway auto-redeploys

---

## 🔒 Security

- `ALLOWED_USER_ID` locks the bot to **only your Telegram account**
- No passwords are ever stored or transmitted
- Session JSON stays in Railway's encrypted env vars only
- `.gitignore` prevents accidental session file commits

---

## 📁 File Structure

```
igauto/
├── bot.py            # Main bot — all commands
├── get_session.py    # Local helper to extract session JSON
├── requirements.txt  # Python dependencies
├── Procfile          # Railway process definition
├── railway.toml      # Railway build config
└── .gitignore
```

---

## ⚠️ Notes on Reach Limit Detection

Instagram doesn't expose reach-limit flags via a clean public API field. The bot uses heuristics (`sensitivity_friction_info`, `feedback_required`, `clips_metadata.is_flagged`) to detect flagged posts. Cross-check in the Instagram app under **Settings → Account Status** for confirmation.
