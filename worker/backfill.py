"""
Mastodon Backfill Worker
Fetches historical posts from Mastodon public timeline using the REST API
"""
import asyncio
import os
import sys
import logging
import time
from datetime import datetime, timezone
from typing import Optional
import argparse

import httpx
from bs4 import BeautifulSoup

# Add shared module to path
sys.path.insert(0, "/app")
from shared.database import get_db_session, init_db
from shared.models import MastodonAccount, MastodonPost

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mastodon_backfill")

# Configuration
MASTODON_INSTANCE = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")

# Rate limiting - Mastodon allows 300 requests per 5 minutes
RATE_LIMIT_DELAY = 1.0  # 1 second between requests to be safe
MAX_POSTS_PER_PAGE = 40  # Mastodon API limit


def extract_text_from_html(html_content: str) -> str:
    """Extract plain text from HTML content"""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.append("\n")
    return soup.get_text(strip=True)


def calculate_engagement_score(reblogs: int, favourites: int, replies: int) -> float:
    """Calculate weighted engagement score"""
    return (reblogs * 3.0) + (favourites * 2.0) + (replies * 1.0)


def parse_status(status_data: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Parse Mastodon status JSON into dictionaries for database insertion"""
    try:
        account_data = status_data.get("account", {})
        
        # Create account dict
        account_dict = {
            "id": account_data.get("id"),
            "username": account_data.get("username", ""),
            "acct": account_data.get("acct", ""),
            "display_name": account_data.get("display_name", ""),
            "followers_count": account_data.get("followers_count", 0),
            "following_count": account_data.get("following_count", 0),
            "statuses_count": account_data.get("statuses_count", 0),
            "bot": account_data.get("bot", False),
            "avatar_url": account_data.get("avatar"),
        }
        
        # Extract hashtags
        tags = status_data.get("tags", [])
        hashtags = [tag.get("name", "").lower() for tag in tags if tag.get("name")]
        
        # Extract mentions
        mentions_data = status_data.get("mentions", [])
        mentions = [m.get("acct", "") for m in mentions_data if m.get("acct")]
        
        # Extract media info
        media = status_data.get("media_attachments", [])
        has_media = len(media) > 0
        media_types = [m.get("type", "unknown") for m in media]
        
        # Parse timestamps
        created_at = datetime.fromisoformat(status_data["created_at"].replace("Z", "+00:00"))
        edited_at = None
        if status_data.get("edited_at"):
            edited_at = datetime.fromisoformat(status_data["edited_at"].replace("Z", "+00:00"))
        
        # Check if this is a reblog
        reblog_of_id = None
        if status_data.get("reblog"):
            reblog_of_id = status_data["reblog"].get("id")
        
        # Create post dict
        content = status_data.get("content", "")
        post_dict = {
            "id": status_data.get("id"),
            "uri": status_data.get("uri"),
            "url": status_data.get("url"),
            "content": content,
            "content_text": extract_text_from_html(content),
            "spoiler_text": status_data.get("spoiler_text", ""),
            "language": status_data.get("language"),
            "visibility": status_data.get("visibility", "public"),
            "sensitive": status_data.get("sensitive", False),
            "reblogs_count": status_data.get("reblogs_count", 0),
            "favourites_count": status_data.get("favourites_count", 0),
            "replies_count": status_data.get("replies_count", 0),
            "engagement_score": calculate_engagement_score(
                status_data.get("reblogs_count", 0),
                status_data.get("favourites_count", 0),
                status_data.get("replies_count", 0)
            ),
            "in_reply_to_id": status_data.get("in_reply_to_id"),
            "in_reply_to_account_id": status_data.get("in_reply_to_account_id"),
            "reblog_of_id": reblog_of_id,
            "account_id": account_data.get("id"),
            "has_media": has_media,
            "media_types": media_types if media_types else None,
            "hashtags": hashtags if hashtags else None,
            "mentions": mentions if mentions else None,
            "created_at": created_at,
            "edited_at": edited_at,
        }
        
        return account_dict, post_dict
        
    except Exception as e:
        logger.error(f"Error parsing status: {e}")
        return None, None


def save_status(account_dict: dict, post_dict: dict) -> bool:
    """Save or update account and post in database. Returns True if new post was added."""
    with get_db_session() as db:
        # Upsert account
        existing_account = db.query(MastodonAccount).filter(
            MastodonAccount.id == account_dict["id"]
        ).first()
        
        if existing_account:
            existing_account.username = account_dict["username"]
            existing_account.acct = account_dict["acct"]
            existing_account.display_name = account_dict["display_name"]
            existing_account.followers_count = account_dict["followers_count"]
            existing_account.following_count = account_dict["following_count"]
            existing_account.statuses_count = account_dict["statuses_count"]
            existing_account.bot = account_dict["bot"]
            existing_account.avatar_url = account_dict["avatar_url"]
            existing_account.last_seen_at = datetime.now(timezone.utc)
        else:
            db.add(MastodonAccount(**account_dict))
        
        # Upsert post
        existing_post = db.query(MastodonPost).filter(
            MastodonPost.id == post_dict["id"]
        ).first()
        
        if existing_post:
            # Update engagement metrics
            existing_post.reblogs_count = post_dict["reblogs_count"]
            existing_post.favourites_count = post_dict["favourites_count"]
            existing_post.replies_count = post_dict["replies_count"]
            existing_post.engagement_score = post_dict["engagement_score"]
            existing_post.edited_at = post_dict["edited_at"]
            return False
        else:
            db.add(MastodonPost(**post_dict))
            return True


async def fetch_public_timeline(
    client: httpx.AsyncClient,
    local: bool = True,
    max_id: Optional[str] = None,
    min_id: Optional[str] = None,
    limit: int = MAX_POSTS_PER_PAGE
) -> tuple[list, Optional[str], Optional[str]]:
    """
    Fetch posts from the public timeline.
    Returns: (posts, next_max_id, prev_min_id) for pagination
    """
    params = {"limit": limit, "local": str(local).lower()}
    if max_id:
        params["max_id"] = max_id
    if min_id:
        params["min_id"] = min_id
    
    headers = {}
    if MASTODON_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {MASTODON_ACCESS_TOKEN}"
    
    url = f"{MASTODON_INSTANCE}/api/v1/timelines/public"
    
    try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        posts = response.json()
        
        # Parse Link header for pagination
        next_max_id = None
        prev_min_id = None
        
        link_header = response.headers.get("Link", "")
        if link_header:
            # Parse pagination links
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    # Extract max_id from the URL
                    import re
                    match = re.search(r'max_id=(\d+)', link)
                    if match:
                        next_max_id = match.group(1)
                elif 'rel="prev"' in link:
                    match = re.search(r'min_id=(\d+)', link)
                    if match:
                        prev_min_id = match.group(1)
        
        # Fallback: use last post's ID if we got posts but no Link header
        if posts and not next_max_id:
            next_max_id = posts[-1]["id"]
        
        return posts, next_max_id, prev_min_id
        
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching timeline: {e.response.status_code} - {e.response.text}")
        if e.response.status_code == 429:
            # Rate limited - wait and retry
            retry_after = int(e.response.headers.get("Retry-After", 60))
            logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
            await asyncio.sleep(retry_after)
        raise
    except Exception as e:
        logger.error(f"Error fetching timeline: {e}")
        raise


async def fetch_hashtag_timeline(
    client: httpx.AsyncClient,
    hashtag: str,
    local: bool = True,
    max_id: Optional[str] = None,
    limit: int = MAX_POSTS_PER_PAGE
) -> tuple[list, Optional[str]]:
    """
    Fetch posts for a specific hashtag.
    Returns: (posts, next_max_id) for pagination
    """
    params = {"limit": limit, "local": str(local).lower()}
    if max_id:
        params["max_id"] = max_id
    
    headers = {}
    if MASTODON_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {MASTODON_ACCESS_TOKEN}"
    
    url = f"{MASTODON_INSTANCE}/api/v1/timelines/tag/{hashtag}"
    
    try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        posts = response.json()
        next_max_id = posts[-1]["id"] if posts else None
        
        return posts, next_max_id
        
    except Exception as e:
        logger.error(f"Error fetching hashtag timeline: {e}")
        raise


async def backfill_public_timeline(
    max_posts: int = 1000,
    local: bool = True,
    delay: float = RATE_LIMIT_DELAY
):
    """
    Backfill posts from the public timeline.
    Fetches posts going backwards in time.
    """
    logger.info(f"Starting backfill from {MASTODON_INSTANCE} (local={local})")
    logger.info(f"Target: {max_posts} posts, delay: {delay}s between requests")
    
    init_db()
    
    total_fetched = 0
    total_saved = 0
    max_id = None
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        while total_fetched < max_posts:
            try:
                posts, next_max_id, _ = await fetch_public_timeline(
                    client, local=local, max_id=max_id
                )
                
                if not posts:
                    logger.info("No more posts to fetch")
                    break
                
                for status in posts:
                    account_dict, post_dict = parse_status(status)
                    if account_dict and post_dict:
                        is_new = save_status(account_dict, post_dict)
                        if is_new:
                            total_saved += 1
                        total_fetched += 1
                
                logger.info(
                    f"Progress: {total_fetched} fetched, {total_saved} new "
                    f"(oldest: {posts[-1]['created_at'] if posts else 'N/A'})"
                )
                
                max_id = next_max_id
                if not max_id:
                    logger.info("No pagination link, stopping")
                    break
                
                # Rate limit delay
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"Error during backfill: {e}")
                # Wait and retry on error
                await asyncio.sleep(5)
                continue
    
    logger.info(f"Backfill complete! Fetched {total_fetched} posts, saved {total_saved} new posts")
    return total_fetched, total_saved


async def backfill_hashtag(
    hashtag: str,
    max_posts: int = 500,
    local: bool = True,
    delay: float = RATE_LIMIT_DELAY
):
    """
    Backfill posts for a specific hashtag.
    """
    logger.info(f"Starting hashtag backfill for #{hashtag} from {MASTODON_INSTANCE}")
    
    init_db()
    
    total_fetched = 0
    total_saved = 0
    max_id = None
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        while total_fetched < max_posts:
            try:
                posts, next_max_id = await fetch_hashtag_timeline(
                    client, hashtag=hashtag, local=local, max_id=max_id
                )
                
                if not posts:
                    logger.info("No more posts to fetch")
                    break
                
                for status in posts:
                    account_dict, post_dict = parse_status(status)
                    if account_dict and post_dict:
                        is_new = save_status(account_dict, post_dict)
                        if is_new:
                            total_saved += 1
                        total_fetched += 1
                
                logger.info(
                    f"Progress: {total_fetched} fetched, {total_saved} new "
                    f"(oldest: {posts[-1]['created_at'] if posts else 'N/A'})"
                )
                
                max_id = next_max_id
                if not max_id:
                    break
                
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"Error during hashtag backfill: {e}")
                await asyncio.sleep(5)
                continue
    
    logger.info(f"Hashtag backfill complete! Fetched {total_fetched} posts, saved {total_saved} new")
    return total_fetched, total_saved


async def backfill_trending(
    max_posts_per_tag: int = 200,
    local: bool = True,
    delay: float = RATE_LIMIT_DELAY
):
    """
    Backfill posts for currently trending hashtags.
    """
    logger.info(f"Fetching trending hashtags from {MASTODON_INSTANCE}")
    
    init_db()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch trending tags
        headers = {}
        if MASTODON_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {MASTODON_ACCESS_TOKEN}"
        
        try:
            response = await client.get(
                f"{MASTODON_INSTANCE}/api/v1/trends/tags",
                headers=headers
            )
            response.raise_for_status()
            trending_tags = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch trending tags: {e}")
            return
        
        logger.info(f"Found {len(trending_tags)} trending tags")
        
        total_saved = 0
        for tag_info in trending_tags[:10]:  # Limit to top 10 trending
            tag_name = tag_info.get("name", "")
            if not tag_name:
                continue
            
            logger.info(f"Backfilling trending tag: #{tag_name}")
            _, saved = await backfill_hashtag(
                hashtag=tag_name,
                max_posts=max_posts_per_tag,
                local=local,
                delay=delay
            )
            total_saved += saved
            
            # Small delay between tags
            await asyncio.sleep(2)
        
        logger.info(f"Trending backfill complete! Total new posts: {total_saved}")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical Mastodon posts"
    )
    parser.add_argument(
        "mode",
        choices=["public", "hashtag", "trending"],
        help="Backfill mode: 'public' for public timeline, 'hashtag' for specific hashtag, 'trending' for trending hashtags"
    )
    parser.add_argument(
        "--max-posts", "-n",
        type=int,
        default=1000,
        help="Maximum number of posts to fetch (default: 1000)"
    )
    parser.add_argument(
        "--hashtag", "-t",
        type=str,
        help="Hashtag to backfill (for hashtag mode, without #)"
    )
    parser.add_argument(
        "--federated", "-f",
        action="store_true",
        help="Include federated posts (not just local instance)"
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=RATE_LIMIT_DELAY,
        help=f"Delay between API requests in seconds (default: {RATE_LIMIT_DELAY})"
    )
    
    args = parser.parse_args()
    
    local = not args.federated
    
    if args.mode == "public":
        asyncio.run(backfill_public_timeline(
            max_posts=args.max_posts,
            local=local,
            delay=args.delay
        ))
    elif args.mode == "hashtag":
        if not args.hashtag:
            parser.error("--hashtag is required for hashtag mode")
        asyncio.run(backfill_hashtag(
            hashtag=args.hashtag,
            max_posts=args.max_posts,
            local=local,
            delay=args.delay
        ))
    elif args.mode == "trending":
        asyncio.run(backfill_trending(
            max_posts_per_tag=args.max_posts // 10,  # Spread across trending tags
            local=local,
            delay=args.delay
        ))


if __name__ == "__main__":
    main()
