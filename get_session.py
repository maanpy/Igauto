#!/usr/bin/env python3
"""
get_session.py — Run this LOCALLY to extract your Instagram session JSON.
Copy the output and paste it into Railway as the IG_SESSION_JSON env var.

Usage:
  pip install instagrapi
  python get_session.py
"""

import json
from instagrapi import Client

print("=" * 60)
print("  IGAUTO — Instagram Session Extractor")
print("=" * 60)
print()

username = input("Instagram username: ").strip()
password = input("Instagram password: ").strip()

print("\n⏳ Logging in...\n")

cl = Client()

try:
    cl.login(username, password)
    settings = cl.get_settings()
    session_json = json.dumps(settings)

    print("=" * 60)
    print("✅  LOGIN SUCCESS")
    print("=" * 60)
    print()
    print(f"  Username : {username}")
    print(f"  User ID  : {cl.user_id}")
    print()
    print("─" * 60)
    print("  RAILWAY ENV VARS — copy each value exactly:")
    print("─" * 60)
    print()
    print(f"  IG_USERNAME  =  {username}")
    print(f"  IG_USER_ID   =  {cl.user_id}")
    print()
    print("  IG_SESSION_JSON  =  (the long line below)")
    print()
    print(session_json)
    print()
    print("─" * 60)
    print("  ⚠️  Copy the JSON line above in full, starting with { and ending with }")
    print("  ⚠️  Do NOT wrap it in quotes in Railway — paste the raw value")
    print("─" * 60)

    # Also save to file as backup
    with open("ig_session.json", "w") as f:
        json.dump(settings, f)
    print("\n  Also saved to: ig_session.json (local backup — do not commit this file)")

except Exception as e:
    print(f"\n❌ Login failed: {type(e).__name__}: {e}")
    print()
    print("Tips:")
    print("  • Open the Instagram app and complete any security prompts first")
    print("  • If you have 2FA, approve the login request in the app")
    print("  • Try again after a minute if rate-limited")

