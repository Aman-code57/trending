# main.py
import json
import os
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────

APP_NAME      = os.getenv("APP_NAME", "ViralFindr API")
API_V1_PREFIX = os.getenv("API_V1_PREFIX", "/api/v1")

try:
    ALLOWED_ORIGINS = json.loads(os.getenv("ALLOWED_ORIGINS", '["http://localhost:3000"]'))
    if not isinstance(ALLOWED_ORIGINS, list):
        ALLOWED_ORIGINS = ["http://localhost:3000"]
except Exception:
    ALLOWED_ORIGINS = ["http://localhost:3000"]

APIFY_TOKEN   = os.getenv("APIFY_API_TOKEN")
APIFY_BASE    = "https://api.apify.com/v2"
ACTOR_ID      = "apify~instagram-scraper"  # official Apify Instagram actor

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────────────────────

def check_token():
    if not APIFY_TOKEN:
        raise HTTPException(status_code=500, detail="Missing APIFY_API_TOKEN in .env")


async def run_actor(payload: dict, timeout: int = 120) -> list[dict]:
    """
    Run the Apify Instagram scraper actor synchronously and return results.
    Uses the run-sync-get-dataset-items endpoint — one call, gets results directly.
    """
    check_token()
    url = f"{APIFY_BASE}/acts/{ACTOR_ID}/run-sync-get-dataset-items"
    params = {
        "token": APIFY_TOKEN,
        "timeout": timeout,
        "memory": 512,
    }

    t = httpx.Timeout(timeout + 30.0, connect=10.0, read=timeout + 30.0)
    async with httpx.AsyncClient(timeout=t) as client:
        try:
            r = await client.post(url, params=params, json=payload)
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Apify actor timed out") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Apify request failed: {exc}") from exc

    if r.status_code not in (200, 201):
        raise HTTPException(
            status_code=r.status_code,
            detail=f"Apify error {r.status_code}: {r.text[:300]}",
        )

    data = r.json()
    return data if isinstance(data, list) else []


def safe_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def format_post(post: dict) -> dict:
    """Normalize a raw Apify Instagram post."""
    likes    = safe_int(post.get("likesCount"))
    comments = safe_int(post.get("commentsCount"))
    views    = safe_int(post.get("videoViewCount") or post.get("videoPlayCount"))

    caption  = (post.get("caption") or post.get("alt") or "").strip()

    media_url = (
        post.get("displayUrl")
        or post.get("thumbnailUrl")
        or (post.get("images") or [None])[0]
    )

    is_video  = post.get("isVideo") or post.get("type") == "Video"
    shortcode = post.get("shortCode") or post.get("id")

    return {
        "id":            post.get("id"),
        "shortcode":     shortcode,
        "kind":          "reel" if is_video else "image",
        "media_url":     media_url,
        "video_url":     post.get("videoUrl"),
        "permalink":     post.get("url") or (
            f"https://www.instagram.com/p/{shortcode}/" if shortcode else None
        ),
        "caption":       caption,
        "hashtags":      post.get("hashtags") or [],
        "mentions":      post.get("mentions") or [],
        "timestamp":     post.get("timestamp"),
        "like_count":    likes,
        "comment_count": comments,
        "view_count":    views,
        "engagement":    likes + comments,
        "owner_username": post.get("ownerUsername"),
        "owner_fullname": post.get("ownerFullName"),
    }


def format_profile(profile: dict) -> dict:
    """Normalize a raw Apify Instagram profile."""
    return {
        "username":     profile.get("username"),
        "full_name":    profile.get("fullName"),
        "biography":    profile.get("biography"),
        "followers":    safe_int(profile.get("followersCount")),
        "following":    safe_int(profile.get("followsCount")),
        "media_count":  safe_int(profile.get("postsCount")),
        "profile_pic":  profile.get("profilePicUrl"),
        "is_verified":  profile.get("verified") or False,
        "is_business":  profile.get("businessCategoryName") is not None,
        "external_url": profile.get("externalUrl"),
    }


# ── routes ────────────────────────────────────────────────────────────────────

# 1. HASHTAG POSTS
@app.get(f"{API_V1_PREFIX}/instagram/hashtag")
async def hashtag_posts(
    tag:     str = Query(..., description="Hashtag without #"),
    limit:   int = Query(12, ge=1, le=50),
    sort_by: str = Query("top", enum=["top", "recent"]),
):
    """
    Fetch public posts for any hashtag with full like/comment/view counts.
    """
    payload = {
        "directUrls": [f"https://www.instagram.com/explore/tags/{tag.lstrip('#')}/"],
        "resultsType": "posts",
        "resultsLimit": limit,
    }

    items = await run_actor(payload)
    posts = [format_post(p) for p in items]

    if sort_by == "top":
        posts.sort(key=lambda p: p["engagement"], reverse=True)
    else:
        posts.sort(key=lambda p: str(p.get("timestamp") or ""), reverse=True)

    return {
        "hashtag":      tag.lstrip("#"),
        "count":        len(posts),
        "sort_by":      sort_by,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "posts":        posts,
    }


# 2. USER PROFILE + POSTS
@app.get(f"{API_V1_PREFIX}/instagram/user/{{username}}")
async def user_profile(
    username: str,
    limit:    int = Query(12, ge=1, le=50),
):
    """
    Fetch public profile info + top posts for any Instagram username.
    """
    payload = {
        "usernames":   [username.lstrip("@")],
        "resultsType": "posts",
        "resultsLimit": limit,
    }

    items   = await run_actor(payload)
    profile = {}
    posts   = []

    for item in items:
        if item.get("username") and not item.get("id"):
            # profile object
            profile = format_profile(item)
        else:
            posts.append(format_post(item))

    # fallback: extract owner info from first post
    if not profile and posts:
        first = posts[0]
        profile = {
            "username":  first.get("owner_username") or username,
            "full_name": first.get("owner_fullname"),
        }

    posts.sort(key=lambda p: p["engagement"], reverse=True)

    return {
        "profile":      profile,
        "count":        len(posts),
        "posts":        posts,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


# 3. TRENDING — aggregate multiple hashtags into one viral feed
@app.get(f"{API_V1_PREFIX}/instagram/trending")
async def trending(
    tags: str = Query(
        "viral,trending,reels,explore,fyp",
        description="Comma-separated hashtags",
    ),
    limit_per_tag: int = Query(6, ge=1, le=20),
):
    """
    Aggregates posts from multiple hashtags into a unified
    trending feed ranked by engagement — core ViralFindr feed.
    """
    tag_list = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()][:6]

    # run one actor call with all hashtags — more efficient
    payload = {
        "directUrls": [f"https://www.instagram.com/explore/tags/{t}/" for t in tag_list],
        "resultsType": "posts",
        "resultsLimit": limit_per_tag,
    }

    items     = await run_actor(payload)
    seen_ids: set[str] = set()
    all_posts: list[dict] = []

    for item in items:
        post = format_post(item)
        pid  = str(post.get("id") or post.get("shortcode") or "")
        if pid and pid not in seen_ids:
            all_posts.append(post)
            seen_ids.add(pid)

    all_posts.sort(key=lambda p: p["engagement"], reverse=True)

    return {
        "tags":         tag_list,
        "total":        len(all_posts),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "posts":        all_posts,
    }


# 4. DASHBOARD — structured home page payload
@app.get(f"{API_V1_PREFIX}/instagram/dashboard")
async def dashboard(
    tags: str = Query(
        "viral,trending,reels",
        description="Comma-separated hashtags",
    ),
):
    """
    Returns full dashboard payload:
    - top_posts: highest engagement overall
    - top_reels: video/reel posts only
    - top_images: image posts only
    - hashtag_summary: post count per tag
    - top_hashtags: most engaging hashtags across all posts
    """
    tag_list = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()][:5]

    payload = {
        "directUrls": [f"https://www.instagram.com/explore/tags/{t}/" for t in tag_list],
        "resultsType": "posts",
        "resultsLimit": 10,
    }

    items     = await run_actor(payload)
    seen_ids: set[str] = set()
    all_posts: list[dict] = []

    for item in items:
        post = format_post(item)
        pid  = str(post.get("id") or post.get("shortcode") or "")
        if pid and pid not in seen_ids:
            all_posts.append(post)
            seen_ids.add(pid)

    all_posts.sort(key=lambda p: p["engagement"], reverse=True)

    # hashtag performance — which tags drive most engagement
    from collections import defaultdict
    tag_stats: dict[str, list[int]] = defaultdict(list)
    for post in all_posts:
        for tag in (post.get("hashtags") or []):
            tag_stats[tag.lower()].append(post["engagement"])

    top_hashtags = sorted(
        [
            {
                "tag":          tag,
                "uses":         len(engs),
                "avg_engagement": round(sum(engs) / len(engs), 1),
            }
            for tag, engs in tag_stats.items()
        ],
        key=lambda x: x["avg_engagement"],
        reverse=True,
    )[:15]

    return {
        "generated_at":    datetime.utcnow().isoformat() + "Z",
        "total_posts":     len(all_posts),
        "top_posts":       all_posts[:12],
        "top_reels":       [p for p in all_posts if p["kind"] == "reel"][:6],
        "top_images":      [p for p in all_posts if p["kind"] == "image"][:6],
        "top_hashtags":    top_hashtags,
        "hashtag_summary": [
            {"tag": t, "fetched": sum(1 for p in all_posts)}
            for t in tag_list
        ],
    }


# 5. SINGLE POST DETAIL
@app.get(f"{API_V1_PREFIX}/instagram/post/{{shortcode}}")
async def post_detail(shortcode: str):
    """
    Fetch details for a single post by shortcode.
    e.g. shortcode from instagram.com/p/CxYz123/
    """
    payload = {
        "directUrls":  [f"https://www.instagram.com/p/{shortcode}/"],
        "resultsType": "posts",
        "resultsLimit": 1,
    }

    items = await run_actor(payload)
    if not items:
        raise HTTPException(status_code=404, detail="Post not found")

    return format_post(items[0])


# 6. HEALTH CHECK
@app.get("/health")
async def health():
    return {
        "status":   "ok",
        "provider": "apify",
        "actor":    ACTOR_ID,
    }