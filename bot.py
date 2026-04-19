#!/usr/bin/env python3
"""
IGAUTO — Instagram Automation Telegram Bot
Uses direct Instagram private API calls (no instagrapi).
Auth: sessionid cookie only.
"""

import os
import json
import logging
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — set these in Railway environment variables
# ══════════════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID      = int(os.environ.get("ALLOWED_USER_ID", "0"))
IG_SESSION_JSON      = os.environ.get("IG_SESSION_JSON", "")
IG_USERNAME          = os.environ.get("IG_USERNAME", "")
IG_USER_ID           = os.environ.get("IG_USER_ID", "")
DEFAULT_ARCHIVE_DAYS = int(os.environ.get("ARCHIVE_DAYS", "7"))
API_DELAY            = float(os.environ.get("API_DELAY", "2.0"))
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger("IGAUTO")


# ── State ─────────────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.ready        = False
        self.sessionid    = ""
        self.csrftoken    = ""
        self.ds_user_id   = ""
        self.username     = IG_USERNAME or "unknown"
        self.archive_days = DEFAULT_ARCHIVE_DAYS
        self.session      = None   # requests.Session

state = BotState()


# ══════════════════════════════════════════════════════════════════════════════
# INSTAGRAM HTTP CLIENT — direct API, no instagrapi
# ══════════════════════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": "Instagram 275.0.0.27.98 Android (28/9; 380dpi; 1080x2220; OnePlus; 6T; devitron; qcom; en_US; 314665256)",
    "Accept":          "*/*",
    "Accept-Language": "en-US",
    "Accept-Encoding": "gzip, deflate",
    "X-IG-App-ID":     "567067343352427",
    "X-IG-Capabilities": "3brTvw==",
    "Connection":      "keep-alive",
}

def make_session(sessionid: str, csrftoken: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.set("sessionid",  sessionid,  domain=".instagram.com")
    s.cookies.set("csrftoken",  csrftoken,  domain=".instagram.com")
    s.cookies.set("ds_user_id", state.ds_user_id, domain=".instagram.com")
    s.headers["X-CSRFToken"] = csrftoken
    return s

def ig_get(path: str, params: dict = None) -> dict:
    url = f"https://i.instagram.com/api/v1/{path}"
    r = state.session.get(url, params=params, timeout=15)
    logger.info(f"GET {path} → {r.status_code}")
    if r.status_code == 200:
        return r.json()
    raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")

def ig_post(path: str, data: dict = None) -> dict:
    url = f"https://i.instagram.com/api/v1/{path}"
    r = state.session.post(url, data=data or {}, timeout=15)
    logger.info(f"POST {path} → {r.status_code}")
    if r.status_code == 200:
        return r.json()
    raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")

def get_user_posts(user_id: str, max_id: str = None) -> dict:
    params = {"count": 33}
    if max_id:
        params["max_id"] = max_id
    return ig_get(f"feed/user/{user_id}/", params)

def get_archived_posts(max_id: str = None) -> dict:
    params = {"only_show_quality_gate_safe": "false"}
    if max_id:
        params["max_id"] = max_id
    return ig_get("feed/only_me_feed/", params)

def archive_media(media_id: str) -> dict:
    return ig_post(f"media/{media_id}/only_me/", {"media_id": media_id})

def unarchive_media(media_id: str) -> dict:
    return ig_post(f"media/{media_id}/undo_only_me/", {"media_id": media_id})

def delete_media(media_id: str) -> dict:
    return ig_post(f"media/{media_id}/delete/", {"media_id": media_id})

def check_account_status() -> dict:
    return ig_get("accounts/account_security_info/")

def get_media_info(media_id: str) -> dict:
    return ig_get(f"media/{media_id}/info/")

def days_ago(timestamp: int) -> int:
    try:
        delta = datetime.now(timezone.utc) - datetime.fromtimestamp(timestamp, timezone.utc)
        return delta.days
    except Exception:
        return 999

def is_reach_limited(item: dict) -> bool:
    if item.get("is_sensitive_media"): return True
    if item.get("feedback_required"):  return True
    if item.get("sensitivity_friction_info"): return True
    clips = item.get("clips_metadata") or {}
    if clips.get("is_flagged"): return True
    return False

def fmt_item(item: dict, index=None) -> str:
    pk   = item.get("pk", "?")
    code = item.get("code", str(pk)[:10])
    ts   = item.get("taken_at", 0)
    d    = days_ago(ts)
    icon = "🎥" if item.get("media_type") == 2 else "📷"
    limit= " ⚡LIMIT" if is_reach_limited(item) else ""
    prefix = f"{index}. " if index is not None else "• "
    return f"{prefix}{icon} `{code}` · {d}d ago{limit}"

def fetch_all_posts(max_items=50) -> list:
    """Fetch user's public feed posts."""
    uid = state.ds_user_id
    posts, max_id = [], None
    while len(posts) < max_items:
        data = get_user_posts(uid, max_id)
        items = data.get("items", [])
        posts.extend(items)
        if not data.get("more_available") or not items:
            break
        max_id = data.get("next_max_id")
    return posts[:max_items]

def fetch_all_archived(max_items=100) -> list:
    """Fetch all archived posts via only_me_feed."""
    posts, max_id = [], None
    while len(posts) < max_items:
        data = get_archived_posts(max_id)
        items = data.get("items", [])
        posts.extend(items)
        if not data.get("more_available") or not items:
            break
        max_id = data.get("next_max_id")
    return posts[:max_items]


# ══════════════════════════════════════════════════════════════════════════════
# SESSION LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_session() -> bool:
    raw = IG_SESSION_JSON.strip()
    if not raw:
        logger.warning("IG_SESSION_JSON not set.")
        return False

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"IG_SESSION_JSON not valid JSON: {e}")
        return False

    # Flatten any format into a simple dict
    cookies: dict = {}
    if isinstance(data, list):
        cookies = {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
        logger.info(f"Format: browser cookie list ({len(data)} cookies)")
    elif isinstance(data, dict):
        if "sessionid" in data:
            cookies = data
        elif "cookies" in data and isinstance(data["cookies"], dict):
            cookies = data["cookies"]
        else:
            # instagrapi settings — dig out cookie values
            for k in ("sessionid", "csrftoken", "ds_user_id", "mid", "rur"):
                if k in data:
                    cookies[k] = data[k]
        logger.info(f"Format: dict with keys {list(cookies.keys())[:6]}")

    sessionid  = cookies.get("sessionid", "").strip()
    csrftoken  = cookies.get("csrftoken", "").strip()
    ds_user_id = (cookies.get("ds_user_id") or IG_USER_ID or "").strip()

    if not sessionid:
        logger.error("No sessionid found in IG_SESSION_JSON.")
        return False

    # csrftoken fallback — extract from sessionid if missing
    if not csrftoken:
        csrftoken = sessionid[:32] if len(sessionid) >= 32 else "missing"

    logger.info(f"sessionid=***{sessionid[-8:]}, csrftoken=***{csrftoken[-6:]}, ds_user_id={ds_user_id}")

    state.sessionid  = sessionid
    state.csrftoken  = csrftoken
    state.ds_user_id = ds_user_id
    state.username   = IG_USERNAME or f"uid:{ds_user_id}"
    state.session    = make_session(sessionid, csrftoken)
    state.ready      = True

    logger.info(f"✅ Session loaded — user_id={ds_user_id}, username={state.username}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM GUARDS
# ══════════════════════════════════════════════════════════════════════════════

def is_allowed(update: Update) -> bool:
    return not (ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID)

def auth_required(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if not is_allowed(update):
            await update.message.reply_text("⛔ Unauthorized.")
            return
        if not state.ready:
            await update.message.reply_text(
                "⚠️ Session not loaded. Check IG_SESSION_JSON env var and run /reload."
            )
            return
        return await func(update, ctx, *a, **kw)
    return wrapper

def esc(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    ig_ok = "✅ Ready" if state.ready else "❌ Not loaded"
    await update.message.reply_text(
        f"🤖 *IGAUTO* — Instagram Automation\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Session: {ig_ok}  ·  Window: *{state.archive_days}d*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"`/status` — Account health & reach limit\n"
        f"`/posts` — List recent posts\n"
        f"`/archive` — Archive last N days posts\n"
        f"`/unarchive` — Restore all archived posts\n"
        f"`/preview_kill` — Preview reach\\-limit posts\n"
        f"`/kill` — Delete reach\\-limit posts ⚠️\n"
        f"`/setdays <n>` — Change archive window\n"
        f"`/debug` — Diagnose session\n"
        f"`/reload` — Reload session\n",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


@auth_required
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Checking account status...")
    try:
        posts = fetch_all_posts(50)
        limited = [p for p in posts if is_reach_limited(p)]

        try:
            sec = check_account_status()
            action_blocked = sec.get("is_action_blocked", False)
        except Exception:
            action_blocked = False

        icon = "🔴" if (limited or action_blocked) else "🟢"
        lines = [
            f"{icon} *Account Status*",
            "━━━━━━━━━━━━━━━━━━",
            f"👤 Username: `{esc(state.username)}`",
            f"🆔 User ID: `{esc(state.ds_user_id)}`",
            f"📊 Posts checked: `{len(posts)}`",
            f"⚡ Reach\\-limit posts: `{len(limited)}`",
            f"🚫 Action block: `{'YES ⛔' if action_blocked else 'NO ✓'}`",
        ]
        if limited:
            lines += ["", "⚠️ *Flagged posts:*"]
            for i, m in enumerate(limited[:10], 1):
                lines.append(fmt_item(m, i))
            lines.append("\nRun `/kill` to delete these\\.")
        else:
            lines.append("\n✅ No reach\\-limit posts detected\\.")

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{esc(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Loading posts...")
    try:
        posts = fetch_all_posts(30)
        if not posts:
            await msg.edit_text("📭 No posts found.")
            return
        limited = sum(1 for p in posts if is_reach_limited(p))
        lines = [f"📋 *Your Posts* \\({len(posts)} fetched\\)", "━━━━━━━━━━━━━━━━━━"]
        for i, p in enumerate(posts[:20], 1):
            lines.append(fmt_item(p, i))
        if len(posts) > 20:
            lines.append(f"_\\.\\.\\. showing 20 of {len(posts)}_")
        lines += ["━━━━━━━━━━━━━━━━━━", f"⚡ Reach\\-limit: `{limited}` · 📊 Total: `{len(posts)}`"]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{esc(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_archive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    days = state.archive_days
    msg = await update.message.reply_text(f"⏳ Fetching posts from last {days} days...")
    try:
        posts = fetch_all_posts(100)
        targets = [p for p in posts if days_ago(p.get("taken_at", 0)) <= days]

        if not targets:
            await msg.edit_text(f"ℹ️ No posts found in the last {days} days.")
            return

        await msg.edit_text(f"📦 Archiving {len(targets)} posts\\.\\.\\. \\(0/{len(targets)}\\)", parse_mode=ParseMode.MARKDOWN_V2)
        archived, failed = 0, 0

        for i, item in enumerate(targets, 1):
            try:
                archive_media(str(item["pk"]))
                archived += 1
            except Exception as e:
                logger.warning(f"Archive failed {item['pk']}: {e}")
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
        await msg.edit_text(f"❌ Error: `{esc(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_unarchive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Fetching archived posts...")
    try:
        archived_posts = fetch_all_archived()

        if not archived_posts:
            await msg.edit_text("ℹ️ No archived posts found.")
            return

        await msg.edit_text(f"↩️ Restoring {len(archived_posts)} posts\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        restored, failed = 0, 0

        for i, item in enumerate(archived_posts, 1):
            try:
                unarchive_media(str(item["pk"]))
                restored += 1
            except Exception as e:
                logger.warning(f"Unarchive failed {item['pk']}: {e}")
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
        await msg.edit_text(f"❌ Error: `{esc(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_preview_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning for reach-limit posts...")
    try:
        posts = fetch_all_posts(50)
        targets = [p for p in posts if is_reach_limited(p)]
        if not targets:
            await msg.edit_text("✅ No reach\\-limit posts found\\. Account is clean\\!", parse_mode=ParseMode.MARKDOWN_V2)
            return
        lines = [f"⚡ *Preview /kill — {len(targets)} flagged:*", "━━━━━━━━━━━━━━━━━━"]
        for i, m in enumerate(targets, 1):
            lines.append(fmt_item(m, i))
        lines += ["━━━━━━━━━━━━━━━━━━", f"Run `/kill` to permanently delete these {len(targets)} posts\\."]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{esc(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


@auth_required
async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning for reach-limit posts...")
    try:
        posts = fetch_all_posts(50)
        targets = [p for p in posts if is_reach_limited(p)]
        if not targets:
            await msg.edit_text("✅ No reach\\-limit posts found\\. Nothing to kill\\!", parse_mode=ParseMode.MARKDOWN_V2)
            return

        preview = "\n".join(fmt_item(m, i) for i, m in enumerate(targets[:8], 1))
        more = f"\n_\\.\\.\\. and {len(targets)-8} more_" if len(targets) > 8 else ""
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚡ CONFIRM DELETE", callback_data="kill_confirm"),
            InlineKeyboardButton("❌ Cancel",         callback_data="kill_cancel"),
        ]])
        await msg.edit_text(
            f"⚡ */kill* — `{len(targets)}` reach\\-limit posts:\n\n{preview}{more}\n\n"
            f"⚠️ *Permanently deletes these posts\\. Confirm?*",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
        )
        ctx.bot_data["kill_targets"] = targets
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{esc(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)


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

    await query.edit_message_text(f"⚡ Deleting {len(targets)} posts\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    deleted, failed = 0, 0

    for i, item in enumerate(targets, 1):
        try:
            delete_media(str(item["pk"]))
            deleted += 1
        except Exception as e:
            logger.warning(f"Delete failed {item['pk']}: {e}")
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


async def cmd_setdays(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(f"Usage: `/setdays <number>`\nCurrent: `{state.archive_days}` days", parse_mode=ParseMode.MARKDOWN_V2)
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
    has_json, json_valid, fmt, has_sid = bool(raw), False, "—", False
    if has_json:
        try:
            d = json.loads(raw)
            json_valid = True
            if isinstance(d, list):
                flat = {c["name"]: c["value"] for c in d if "name" in c}
                fmt = f"browser list ({len(d)} cookies)"
                has_sid = bool(flat.get("sessionid"))
            elif isinstance(d, dict):
                has_sid = bool(d.get("sessionid") or (d.get("cookies") or {}).get("sessionid"))
                fmt = "dict: " + ", ".join(list(d.keys())[:5])
        except Exception as e:
            fmt = f"parse error: {e}"

    lines = [
        "🔍 *Debug — Session Diagnostics*",
        "━━━━━━━━━━━━━━━━━━",
        f"IG\\_SESSION\\_JSON : `{'SET' if has_json else 'MISSING ❌'}`",
        f"JSON valid        : `{'YES' if json_valid else 'NO ❌'}`",
        f"Format            : {fmt}",
        f"Has sessionid     : `{'YES ✅' if has_sid else 'NO ❌'}`",
        f"IG\\_USER\\_ID      : `{esc(IG_USER_ID or 'not set')}`",
        f"IG\\_USERNAME      : `{esc(IG_USERNAME or 'not set')}`",
        f"Bot state ready   : `{'YES ✅' if state.ready else 'NO ❌'}`",
        f"ds\\_user\\_id      : `{esc(state.ds_user_id or 'not set')}`",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if state.ready:
        lines.append("✅ Session loaded\\. Try /status to test live API\\.")
    elif not has_sid:
        lines.append("❌ No sessionid found\\. Check your cookie export\\.")
    else:
        lines.append("⚠️ Send /reload to apply the session\\.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_reload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.message.reply_text("⏳ Reloading session...")
    ok = load_session()
    if ok:
        await msg.edit_text(
            f"✅ *Session reloaded\\!*\n\n"
            f"👤 Username: `{esc(state.username)}`\n"
            f"🆔 User ID: `{esc(state.ds_user_id)}`\n\n"
            f"Try /status to confirm API access\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await msg.edit_text("❌ Reload failed\\. Check IG\\_SESSION\\_JSON env var\\.", parse_mode=ParseMode.MARKDOWN_V2)


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception:", exc_info=ctx.error)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    logger.info("IGAUTO starting (direct HTTP mode)...")
    ok = load_session()
    logger.info(f"Session load: {'✅ OK' if ok else '❌ FAILED'}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("status",       cmd_status))
    app.add_handler(CommandHandler("posts",        cmd_posts))
    app.add_handler(CommandHandler("archive",      cmd_archive))
    app.add_handler(CommandHandler("unarchive",    cmd_unarchive))
    app.add_handler(CommandHandler("kill",         cmd_kill))
    app.add_handler(CommandHandler("preview_kill", cmd_preview_kill))
    app.add_handler(CommandHandler("setdays",      cmd_setdays))
    app.add_handler(CommandHandler("debug",        cmd_debug))
    app.add_handler(CommandHandler("reload",       cmd_reload))
    app.add_handler(CallbackQueryHandler(kill_callback, pattern="^kill_"))
    app.add_error_handler(error_handler)

    logger.info("Bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
