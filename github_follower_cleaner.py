#!/usr/bin/env python3
"""
GitHub Follower Cleaner
========================
Detects and blocks suspicious followers based on:
  1. Bio/description with spam phrases (e.g. "give me stars", "back to your repositories")
  2. Anime/cartoon avatar detected by Claude Vision

Usage:
    pip install requests pillow
    python github_follower_cleaner.py

Required environment variables:
    GITHUB_TOKEN      → Personal GitHub token (scope: read:user, user:follow, user:block)
    ANTHROPIC_API_KEY → Your Anthropic API key (to analyze avatars)
"""

import os
import sys
import base64
import requests
import json
from io import BytesIO

# ─── Configuration ────────────────────────────────────────────────────────────

GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

GITHUB_API   = "https://api.github.com"
CLAUDE_API   = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Spam phrases to look for in bio, name, or company
SPAM_PHRASES = [
    "give me stars",
    "back to your repositories",
    "star my repo",
    "star back",
    "follow back",
    "follow me back",
    "i follow back",
    "follow4follow",
    "f4f",
    "star4star",
    "🗝️",           # emoji characteristic of these bots
]

# ─── GitHub Helpers ───────────────────────────────────────────────────────────

def gh_headers():
    if not GITHUB_TOKEN:
        print("❌  Missing GITHUB_TOKEN. Please export the environment variable.")
        sys.exit(1)
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

def get_all_followers():
    """Fetches all followers by paginating the API."""
    followers = []
    page = 1
    print("🔍  Fetching followers...")
    while True:
        url = f"{GITHUB_API}/user/followers?per_page=100&page={page}"
        r = requests.get(url, headers=gh_headers())
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        followers.extend(batch)
        page += 1
    print(f"   → {len(followers)} followers found.\n")
    return followers

def get_user_details(username):
    """Fetches the full profile of a user."""
    url = f"{GITHUB_API}/users/{username}"
    r = requests.get(url, headers=gh_headers())
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def block_user(username):
    """Blocks a user on GitHub."""
    url = f"{GITHUB_API}/user/blocks/{username}"
    r = requests.put(url, headers=gh_headers())
    return r.status_code == 204

# ─── Spam Text Detection ──────────────────────────────────────────────────────

def has_spam_text(user_details):
    """Returns True if bio, name, or company contain spam phrases."""
    fields = [
        (user_details.get("bio") or "").lower(),
        (user_details.get("name") or "").lower(),
        (user_details.get("company") or "").lower(),
        (user_details.get("login") or "").lower(),
    ]
    combined = " ".join(fields)
    for phrase in SPAM_PHRASES:
        if phrase.lower() in combined:
            return True, phrase
    return False, None

# ─── Anime/Cartoon Avatar Detection with Claude Vision ───────────────────────

def download_avatar_base64(avatar_url):
    """Downloads the avatar and converts it to base64."""
    try:
        r = requests.get(avatar_url, timeout=10)
        r.raise_for_status()
        return base64.b64encode(r.content).decode("utf-8"), r.headers.get("content-type", "image/jpeg")
    except Exception as e:
        print(f"      ⚠️  Could not download avatar: {e}")
        return None, None

def is_anime_avatar(avatar_url):
    """Uses Claude Vision to detect if the avatar is anime/cartoon."""
    if not ANTHROPIC_API_KEY:
        # Skip image analysis if no API key is set
        return False, "no API key"

    img_b64, media_type = download_avatar_base64(avatar_url)
    if not img_b64:
        return False, "not downloadable"

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Is this profile picture an anime, cartoon, or illustrated character "
                            "(not a real human photo or abstract logo)? "
                            "Reply ONLY with YES or NO."
                        ),
                    },
                ],
            }
        ],
    }

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        r = requests.post(CLAUDE_API, headers=headers, json=payload, timeout=20)
        r.raise_for_status()
        answer = r.json()["content"][0]["text"].strip().upper()
        return answer.startswith("YES"), answer
    except Exception as e:
        return False, f"error: {e}"

# ─── Core Logic ───────────────────────────────────────────────────────────────

def analyze_follower(follower):
    """
    Analyzes a follower and returns a dict with the results.
    Possible reasons: spam_text, anime_avatar
    """
    username = follower["login"]
    reasons  = []

    # 1. Fetch profile details
    details = get_user_details(username)
    if not details:
        return {"username": username, "suspicious": False, "reasons": []}

    # 2. Text-based spam check
    spam, phrase = has_spam_text(details)
    if spam:
        reasons.append(f"bio/name contains: '{phrase}'")

    # 3. Avatar check (only if user has a photo and API key is set)
    avatar_url = details.get("avatar_url", "")
    if avatar_url and ANTHROPIC_API_KEY:
        is_anime, raw = is_anime_avatar(avatar_url)
        if is_anime:
            reasons.append("anime/cartoon avatar detected by AI")

    return {
        "username": username,
        "profile_url": details.get("html_url", f"https://github.com/{username}"),
        "bio": (details.get("bio") or "")[:120],
        "suspicious": len(reasons) > 0,
        "reasons": reasons,
    }

def run():
    print("=" * 55)
    print("   🧹  GitHub Follower Cleaner")
    print("=" * 55)

    if not GITHUB_TOKEN:
        print("❌  GITHUB_TOKEN is not set.")
        print("   Run: export GITHUB_TOKEN=your_token")
        sys.exit(1)

    followers = get_all_followers()
    suspicious_list = []

    for i, follower in enumerate(followers, 1):
        uname = follower["login"]
        print(f"[{i:>4}/{len(followers)}] Analyzing @{uname}...", end="", flush=True)
        result = analyze_follower(follower)
        if result["suspicious"]:
            suspicious_list.append(result)
            print(f"  🚩 SUSPICIOUS → {', '.join(result['reasons'])}")
        else:
            print("  ✅ OK")

    # ─── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print(f"   📋  Summary: {len(suspicious_list)} suspicious out of {len(followers)} followers")
    print("=" * 55)

    if not suspicious_list:
        print("🎉  No suspicious followers found!")
        return

    print("\nSuspicious users detected:\n")
    for idx, u in enumerate(suspicious_list, 1):
        print(f"  {idx}. @{u['username']}")
        print(f"     🔗 {u['profile_url']}")
        if u["bio"]:
            print(f"     📝 Bio: {u['bio']}")
        for r in u["reasons"]:
            print(f"     ⚠️  {r}")
        print()

    # ─── Confirmation before blocking ─────────────────────────────────────────
    print("-" * 55)
    answer = input(f"Block these {len(suspicious_list)} users? (y/N): ").strip().lower()

    if answer not in ("y", "yes"):
        print("🚫  Operation cancelled. No one was blocked.")
        return

    print("\n🔒  Blocking users...\n")
    blocked = 0
    for u in suspicious_list:
        success = block_user(u["username"])
        if success:
            print(f"   ✅  @{u['username']} blocked.")
            blocked += 1
        else:
            print(f"   ❌  Error blocking @{u['username']}.")

    print(f"\n🏁  Done. {blocked}/{len(suspicious_list)} users blocked.")

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()