#!/usr/bin/env python3
"""
IGAUTO — Instagram Automation Telegram Bot
Session-only auth. All config via environment variables.
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)
from telegram.constants import ParseMode

from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, ChallengeRequired, ClientError,
)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — all from environment variables (set in Railway dashboard)
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID     = int(os.environ.get("ALLOWED_USER_ID", "0"))

# Instagram session — paste the full JSON export from instagrapi
# OR set individual cookie values (both methods supported)
IG_SESSION_JSON     = os.environ.get("IG_SESSION_JSON", "")       # full session JSON string
IG_USERNAME         = os.environ.get("IG_USERNAME", "")           # just for display
IG_USER_ID          = os.environ.get("IG_USER_ID", "")            # numeric user ID

DEFAULT_ARCHIVE_DAYS = int(os.environ.get("ARCHIVE_DAYS", "7"))
API_DELAY            = float(os.environ.get("API_DELAY", "1.5"))

# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("IGAUTO")


# ── State ─────────────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.ig: Optional[Client] = None
        self.ready = False
        self.username = IG_USERNAME or "unknown"
        self.user_id_num: Optional[int] = None
        self.archive_days = DEFAULT_ARCHIVE_DAYS
        self.cached_posts: list = []

state = BotState()


# ── Auth guard ────────────────────────────────────────────────────────────────
def is_allowed(update: Update) -> bool:
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return False
    return True

def auth_required(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if not is_allowed(update):
            await update.message.reply_text("⛔ Unauthorized.")
            return
        if not state.ready:
            await update.message.reply_text(
                "⚠️ Instagram session not loaded\\.\n"
                "Set `IG_SESSION_JSON` in Railway env vars and redeploy\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        return await func(update, ctx, *a, **kw)
    return wrapper


# ── Instagram session bootstrap ───────────────────────────────────────────────
def load_session() -> bool:
    """
    Load Instagram session from IG_SESSION_JSON env var.

    We do NOT call login_by_sessionid() or any verification endpoint —
    Railway's server IP is unknown to Instagram and triggers a challenge.
    Instead we inject the sessionid cookie directly into the requests session,
    which is exactly what instagrapi uses under the hood anyway.
    The session will be validated lazily on the first real API call.
    """
    import traceback
    global state

    raw = IG_SESSION_JSON.strip()
    if not raw:
        logger.warning("IG_SESSION_JSON not set — IG commands disabled.")
        return False

    # ── Parse JSON ────────────────────────────────────────────────────────
    try:
        session_data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"IG_SESSION_JSON is not valid JSON: {e}")
        return False

    # ── Flatten to a simple cookie dict ───────────────────────────────────
    cookies: dict = {}

    if isinstance(session_data, list):
        # Browser export: [{"name":"sessionid","value":"..."},...]
        cookies = {
            c["name"]: c["value"]
            for c in session_data
            if isinstance(c, dict) and "name" in c and "value" in c
        }
        logger.info(f"Format: browser cookie list ({len(session_data)} items) → {list(cookies.keys())}")

    elif isinstance(session_data, dict):
        # Full instagrapi settings JSON
        if "authorization_data" in session_data or "uuids" in session_data:
            nested = session_data.get("cookies", {})
            cookies = nested if isinstance(nested, dict) else {}
            # Merge top-level keys that look like cookies
            for k in ("sessionid", "csrftoken", "ds_user_id", "mid", "rur", "ig_did"):
                if k in session_data:
                    cookies[k] = session_data[k]
            logger.info("Format: full instagrapi settings")
        # Flat cookie dict
        elif "sessionid" in session_data:
            cookies = session_data
            logger.info("Format: flat cookie dict")
        else:
            logger.error(f"Unrecognized dict. Keys: {list(session_data.keys())[:10]}")
            return False
    else:
        logger.error(f"Unexpected JSON type: {type(session_data).__name__}")
        return False

    sessionid = cookies.get("sessionid", "").strip()
    if not sessionid:
        logger.error("No 'sessionid' value found after parsing. Cannot authenticate.")
        return False

    logger.info(f"sessionid extracted: ***{sessionid[-8:]} (last 8 chars)")

    # ── Build client and inject cookies without any API call ──────────────
    try:
        cl = Client()
        cl.delay_range = [API_DELAY, API_DELAY + 0.5]

        # Inject all cookies directly into the underlying requests.Session
        # This is safe — instagrapi reads from this same cookie jar for every request
        for name, value in cookies.items():
            cl.private.cookies.set(name, str(value), domain=".instagram.com")
            cl.private.cookies.set(name, str(value), domain="i.instagram.com")

        logger.info(f"Cookies injected: {list(cookies.keys())}")

        # Set user_id via the internal attribute (avoids read-only property)
        uid_str = (
            IG_USER_ID
            or cookies.get("ds_user_id", "")
            or str(session_data.get("user_id", "") if isinstance(session_data, dict) else "")
        ).strip()

        if uid_str:
            try:
                cl._user_id = int(uid_str)          # internal attr — works in instagrapi v1 & v2
                cl.user_id  = int(uid_str)          # try the property too, may work in older versions
            except (AttributeError, ValueError):
                try:
                    cl._user_id = int(uid_str)
                except Exception:
                    pass

        logger.info(f"user_id set to: {uid_str}")

    except Exception as e:
        logger.error(f"Cookie injection failed: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        return False

    # ── Mark ready — real validation happens on first command ─────────────
    state.ig           = cl
    state.ready        = True
    state.user_id_num  = int(uid_str) if uid_str else None
    state.username     = IG_USERNAME or f"uid:{uid_str}"
    logger.info(f"✅ Session loaded (no challenge) — user_id={state.user_id_num}, username={state.username}")
    return True


def get_ig() -> Client:
    return state.ig


# ── Helpers ───────────────────────────────────────────────────────────────────
def days_ago(media) -> int:
    try:
        taken = media.taken_at
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - taken).days
    except Exception:
        return 999

def is_reach_limited(media) -> bool:
    try:
        info = media.dict() if hasattr(media, "dict") else {}
        if info.get("sensitivity_friction_info"):
            return True
        if info.get("feedback_required"):
            return True
        if info.get("is_unified_video") and info.get("clips_metadata", {}).get("is_flagged"):
            return True
    except Exception:
        pass
    return False

def fmt_post(media, index=None) -> str:
    prefix = f"{index}\\. " if index is not None else "• "
    d = days_ago(media)
    code = getattr(media, "code", str(media.pk)[:10])
    icon = "🎥" if getattr(media, "is_unified_video", False) else "📷"
    limit = " ⚡`LIMIT`" if is_reach_limited(media) else ""
    return f"{prefix}{icon} `{code}` · {d}d ago{limit}"

async def fetch_posts(amount=50) -> list:
    cl = get_ig()
    posts = cl.user_medias(state.user_id_num, amount=amount)
    state.cached_posts = posts
    return posts

async def fetch_archived() -> list:
    cl = get_ig()
    # Method name changed across instagrapi versions — try all known names
    for method_name in ("archived_medias", "media_archived_medias"):
        if hasattr(cl, method_name):
            logger.info(f"fetch_archived: using cl.{method_name}()")
            return getattr(cl, method_name)()
    raise AttributeError(
        "No archived-media method found. Tried: archived_medias, media_archived_medias"
    )

def escape(text: str) -> str:
    """Escape special chars for MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    ig_status = "✅ Session loaded" if state.ready else "❌ No session — set `IG_SESSION_JSON`"
    text = (
        "🤖 *IGAUTO* — Instagram Automation\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Instagram: {ig_status}\n"
        f"Archive window: *{state.archive_days} days*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📋 *Commands:*\n"
        "`/status` — Check reach limit & account health\n"
        "`/archive` — Archive posts from last N days\n"
        "`/unarchive` — Restore all archived posts\n"
        "`/preview_kill` — Preview reach\\-limit posts\n"
        "`/kill` — Delete all reach\\-limit posts ⚠️\n"
        "`/posts` — List recent posts\n"
        "`/setdays <n>` — Change archive window\n"
        "`/session` — How to get your session JSON\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

async def cmd_session_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    text = (
        "📋 *How to get your Instagram Session JSON*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "*Method 1 — Python script \\(recommended\\):*\n"
        "```python\n"
        "from instagrapi import Client\n"
        "cl = Client()\n"
        "cl.login('your_username', 'your_password')\n"
        "print(cl.get_settings())\n"
        "```\n"
        "Copy the printed JSON → paste into Railway as `IG_SESSION_JSON`\n\n"
        "*Method 2 — Browser cookies:*\n"
        "1\\. Log in to instagram\\.com\n"
        "2\\. DevTools → Application → Cookies\n"
        "3\\. Copy `sessionid`, `csrftoken`, `ds_user_id`\n"
        "4\\. Build JSON: `{\"sessionid\":\"...\",\"csrftoken\":\"...\",\"ds_user_id\":\"...\"}`\n\n"
        "*Railway env vars to set:*\n"
        "`IG_SESSION_JSON` — the full JSON string\n"
        "`IG_USERNAME` — your username \\(display only\\)\n"
        "`IG_USER_ID` — your numeric user ID\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Checking account status...")
    try:
        cl = get_ig()
        posts = await fetch_posts(50)
        limited = [p for p in posts if is_reach_limited(p)]

        action_blocked = False
        try:
            cl.user_info(state.user_id_num)
        except Exception as e:
            if any(x in str(e).lower() for x in ["feedback_required", "action_blocked"]):
                action_blocked = True

        ok = not limited and not action_blocked
        icon = "🟢" if ok else "🔴"

        lines = [
            f"{icon} *Account Status*",
            "━━━━━━━━━━━━━━━━━━",
            f"👤 Username: `{escape(state.username)}`",
            f"🆔 User ID: `{state.user_id_num}`",
            f"📊 Posts fetched: `{len(posts)}`",
            f"⚡ Reach\\-limit posts: `{len(limited)}`",
            f"🚫 Action block: `{'YES ⛔' if action_blocked else 'NO ✓'}`",
        ]

        if limited:
            lines += ["", "⚠️ *Flagged posts:*"]
            for i, m in enumerate(limited[:10], 1):
                lines.append(fmt_post(m, i))
            lines.append("")
            lines.append("Run `/kill` to delete these\\.")
        else:
            lines.append("\n✅ No reach\\-limit posts detected\\.")

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{escape(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_archive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    days = state.archive_days
    msg = await update.message.reply_text(f"⏳ Fetching posts from last {days} days...")
    try:
        cl = get_ig()
        posts = await fetch_posts(100)
        targets = [p for p in posts if days_ago(p) <= days]

        if not targets:
            await msg.edit_text(f"ℹ️ No posts found in the last {days} days to archive.")
            return

        await msg.edit_text(f"📦 Archiving {len(targets)} posts\\.\\.\\. \\(0/{len(targets)}\\)", parse_mode=ParseMode.MARKDOWN_V2)
        archived = 0
        failed = 0

        for i, media in enumerate(targets, 1):
            try:
                # Try both known method names for archiving
                _archive_fn = getattr(cl, "media_archive", None) or getattr(cl, "media_only_me", None)
                if not _archive_fn:
                    raise AttributeError("No archive method found on Client (tried: media_archive)")
                _archive_fn(media.pk)
                archived += 1
            except Exception as e:
                logger.warning(f"Archive failed {media.pk}: {e}")
                failed += 1
            await asyncio.sleep(API_DELAY)
            if i % 5 == 0 or i == len(targets):
                await msg.edit_text(
                    f"📦 Archiving\\.\\.\\. \\({i}/{len(targets)}\\)\n✅ Done: {archived} ❌ Failed: {failed}",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

        await msg.edit_text(
            f"✅ *Archive complete\\!*\n\n📦 Archived: `{archived}`\n❌ Failed: `{failed}`\n📅 Window: `{days}` days",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{escape(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_unarchive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Fetching archived posts...")
    try:
        cl = get_ig()
        archived_posts = await fetch_archived()

        if not archived_posts:
            await msg.edit_text("ℹ️ No archived posts found.")
            return

        await msg.edit_text(f"↩️ Restoring {len(archived_posts)} posts\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        restored = 0
        failed = 0

        for i, media in enumerate(archived_posts, 1):
            try:
                # Try both known method names for unarchiving
                _unarchive_fn = getattr(cl, "media_unarchive", None) or getattr(cl, "media_undo_only_me", None)
                if not _unarchive_fn:
                    raise AttributeError("No unarchive method found on Client (tried: media_unarchive)")
                _unarchive_fn(media.pk)
                restored += 1
            except Exception as e:
                logger.warning(f"Unarchive failed {media.pk}: {e}")
                failed += 1
            await asyncio.sleep(API_DELAY)
            if i % 5 == 0 or i == len(archived_posts):
                await msg.edit_text(
                    f"↩️ Restoring\\.\\.\\. \\({i}/{len(archived_posts)}\\)\n✅ Restored: {restored} ❌ Failed: {failed}",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

        await msg.edit_text(
            f"✅ *Unarchive complete\\!*\n\n↩️ Restored: `{restored}`\n❌ Failed: `{failed}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{escape(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_preview_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning for reach-limit posts...")
    try:
        posts = await fetch_posts(50)
        targets = [p for p in posts if is_reach_limited(p)]

        if not targets:
            await msg.edit_text("✅ No reach\\-limit posts found\\. Account is clean\\!", parse_mode=ParseMode.MARKDOWN_V2)
            return

        lines = [f"⚡ *Preview /kill — {len(targets)} flagged posts:*", "━━━━━━━━━━━━━━━━━━"]
        for i, m in enumerate(targets, 1):
            lines.append(fmt_post(m, i))
        lines += ["━━━━━━━━━━━━━━━━━━", f"Run `/kill` to permanently delete these {len(targets)} posts\\."]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{escape(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning for reach-limit posts...")
    try:
        posts = await fetch_posts(50)
        targets = [p for p in posts if is_reach_limited(p)]

        if not targets:
            await msg.edit_text("✅ No reach\\-limit posts found\\. Nothing to kill\\!", parse_mode=ParseMode.MARKDOWN_V2)
            return

        preview = "\n".join(fmt_post(m, i) for i, m in enumerate(targets[:8], 1))
        more = f"\n_\\.\\.\\. and {len(targets)-8} more_" if len(targets) > 8 else ""

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚡ CONFIRM DELETE", callback_data="kill_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="kill_cancel"),
        ]])

        await msg.edit_text(
            f"⚡ */kill* — `{len(targets)}` reach\\-limit posts found:\n\n{preview}{more}\n\n"
            f"⚠️ *Permanently deletes these posts\\. Confirm?*",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
        )
        ctx.bot_data["kill_targets"] = targets
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{escape(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


async def kill_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "kill_cancel":
        await query.edit_message_text("❌ /kill cancelled. No posts deleted.")
        return

    targets = ctx.bot_data.get("kill_targets", [])
    if not targets:
        await query.edit_message_text("⚠️ Session expired. Run /kill again.")
        return

    cl = get_ig()
    await query.edit_message_text(f"⚡ Deleting {len(targets)} posts\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    deleted = 0
    failed = 0

    for i, media in enumerate(targets, 1):
        try:
            cl.media_delete(media.pk)
            deleted += 1
        except Exception as e:
            logger.warning(f"Delete failed {media.pk}: {e}")
            failed += 1
        await asyncio.sleep(API_DELAY)
        if i % 3 == 0 or i == len(targets):
            await query.edit_message_text(
                f"⚡ Deleting\\.\\.\\. \\({i}/{len(targets)}\\)\n🗑 Deleted: {deleted} ❌ Failed: {failed}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

    ctx.bot_data.pop("kill_targets", None)
    await query.edit_message_text(
        f"⚡ */kill complete\\!*\n\n🗑 Deleted: `{deleted}`\n❌ Failed: `{failed}`\n\nRun /status to verify\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


@auth_required
async def cmd_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Loading posts...")
    try:
        posts = await fetch_posts(30)
        if not posts:
            await msg.edit_text("📭 No posts found.")
            return

        lines = [f"📋 *Your Posts* \\({len(posts)} fetched\\)", "━━━━━━━━━━━━━━━━━━"]
        for i, m in enumerate(posts[:20], 1):
            lines.append(fmt_post(m, i))
        if len(posts) > 20:
            lines.append(f"_\\.\\.\\. showing 20 of {len(posts)}_")

        limited = sum(1 for p in posts if is_reach_limited(p))
        lines += ["━━━━━━━━━━━━━━━━━━", f"⚡ Reach\\-limit: `{limited}` · 📊 Total: `{len(posts)}`"]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{escape(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_setdays(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            f"Usage: `/setdays <number>`\nCurrent: `{state.archive_days}` days",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    days = int(args[0])
    if not 1 <= days <= 365:
        await update.message.reply_text("⚠️ Must be between 1 and 365.")
        return
    state.archive_days = days
    await update.message.reply_text(f"✅ Archive window set to *{days} days*\\.", parse_mode=ParseMode.MARKDOWN_V2)



async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    raw = IG_SESSION_JSON.strip()
    has_json = bool(raw)
    json_valid = False
    json_format = "not checked"
    has_sessionid = False

    if has_json:
        try:
            d = json.loads(raw)
            json_valid = True

            # Handle list format (browser cookie export)
            if isinstance(d, list):
                cookie_dict = {c["name"]: c["value"] for c in d if "name" in c and "value" in c}
                json_format = f"browser cookie list ({len(d)} cookies) - auto-converted"
                has_sessionid = bool(cookie_dict.get("sessionid"))

            elif isinstance(d, dict):
                if "authorization_data" in d or (
                    "cookies" in d and isinstance(d["cookies"], dict)
                ):
                    json_format = "full instagrapi settings"
                    has_sessionid = bool(
                        d.get("authorization_data") or
                        (d.get("cookies") or {}).get("sessionid")
                    )
                elif "sessionid" in d:
                    json_format = "flat cookie dict"
                    has_sessionid = True
                else:
                    json_format = "unknown dict — keys: " + ", ".join(list(d.keys())[:6])
                    has_sessionid = False
            else:
                json_format = f"unexpected type: {type(d).__name__}"

        except Exception as e:
            json_format = f"parse error: {e}"

    lines = [
        "🔍 DEBUG — Session Diagnostics",
        "━━━━━━━━━━━━━━━━━━",
        f"IG_SESSION_JSON set : {'YES' if has_json else 'NO ❌'}",
        f"JSON valid          : {'YES' if json_valid else 'NO ❌'}",
        f"Format detected     : {json_format}",
        f"Has sessionid token : {'YES ✅' if has_sessionid else 'NO ❌'}",
        f"IG_USER_ID set      : {IG_USER_ID or 'not set'}",
        f"IG_USERNAME set     : {IG_USERNAME or 'not set'}",
        f"Bot state ready     : {'YES ✅' if state.ready else 'NO ❌'}",
        f"Client user_id      : {state.user_id_num or 'not set'}",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if not has_json:
        lines.append("FIX: Set IG_SESSION_JSON in Railway Variables and redeploy.")
    elif not json_valid:
        lines.append("FIX: JSON is malformed. Re-run get_session.py and copy the output.")
    elif not has_sessionid:
        lines.append(
            "FIX: Your JSON does not contain a sessionid.\n"
            "Run get_session.py locally and paste the FULL output as IG_SESSION_JSON.\n"
            "It must start with { and contain 'cookies' or 'sessionid'."
        )
    elif not state.ready:
        lines.append("Session looks valid. Send /reload to apply it now.")
    else:
        lines.append("All good! Try /status to test a live API call.")

    await update.message.reply_text("\n".join(lines))


async def cmd_reload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.message.reply_text("⏳ Reloading Instagram session...")

    # Capture any exception from load_session for display
    last_error = []
    _orig_error = logger.error
    def capturing_error(msg_str, *a, **kw):
        last_error.append(str(msg_str))
        _orig_error(msg_str, *a, **kw)
    logger.error = capturing_error

    ok = load_session()
    logger.error = _orig_error  # restore

    if ok:
        await msg.edit_text(
            f"✅ Session reloaded!\n\n"
            f"User ID : {state.user_id_num}\n"
            f"Username: {state.username}\n\n"
            f"Try /status to confirm API access."
        )
    else:
        err_summary = "\n".join(last_error[-3:]) if last_error else "unknown error"
        await msg.edit_text(
            f"❌ Session reload failed.\n\n"
            f"Error:\n{err_summary}\n\n"
            f"Check Railway logs for the full traceback.\n"
            f"Run /debug to inspect your session JSON."
        )


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception:", exc_info=ctx.error)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    logger.info("IGAUTO starting...")
    session_ok = load_session()
    if session_ok:
        logger.info("✅ Instagram session ready.")
    else:
        logger.warning("⚠️  Instagram session NOT loaded. Set IG_SESSION_JSON env var.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("session",      cmd_session_help))
    app.add_handler(CommandHandler("status",       cmd_status))
    app.add_handler(CommandHandler("archive",      cmd_archive))
    app.add_handler(CommandHandler("unarchive",    cmd_unarchive))
    app.add_handler(CommandHandler("kill",         cmd_kill))
    app.add_handler(CommandHandler("preview_kill", cmd_preview_kill))
    app.add_handler(CommandHandler("posts",        cmd_posts))
    app.add_handler(CommandHandler("setdays",      cmd_setdays))
    app.add_handler(CommandHandler("debug",        cmd_debug))
    app.add_handler(CommandHandler("reload",       cmd_reload))
    app.add_handler(CallbackQueryHandler(kill_callback, pattern="^kill_"))
    app.add_error_handler(error_handler)

    logger.info("Bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
