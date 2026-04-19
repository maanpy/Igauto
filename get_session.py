#!/usr/bin/env python3
"""
get_session.py — Run this LOCALLY to extract your Instagram session JSON.
Copy the output and paste it into Railway as the IG_SESSION_JSON env var.

Usage:
  pip install instagrapi
  python get_session.py
"""

from instagrapi import Client
import json

username = input("Instagram username: ").strip()
password = input("Instagram password: ").strip()

cl = Client()

try:
    cl.login(username, password)
    settings = cl.get_settings()
    session_json = json.dumps(settings)

    print("\n" + "="*60)
    print("✅ LOGIN SUCCESS")
    print("="*60)
    print(f"\nUsername : {username}")
    print(f"User ID  : {cl.user_id}")
    print("\n--- COPY THIS ENTIRE LINE AS IG_SESSION_JSON ---\n")
    print(session_json)
    print("\n--- END ---\n")
    print("Also set these Railway env vars:")
    print(f"  IG_USERNAME = {username}")
    print(f"  IG_USER_ID  = {cl.user_id}")

except Exception as e:
    print(f"\n❌ Login failed: {e}")
    print("Try logging in via the Instagram app first to clear any challenges.")
