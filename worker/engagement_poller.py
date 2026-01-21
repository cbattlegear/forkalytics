"""
Engagement Metrics Poller
Periodically fetches updated engagement metrics (reblogs, favorites, replies) 
for recent posts from the Mastodon API since the streaming API doesn't send these updates.
"""
import asyncio
import os
import sys
import logging
import signal
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from sqlalchemy import desc

# Add shared module to path
sys.path.insert(0, "/app")
from shared.database import get_db_session, init_db
from shared.models import MastodonPost

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("engagement_poller")

# Configuration
MASTODON_INSTANCE = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")

# Polling configuration
POLL_INTERVAL_SECONDS = int(os.getenv("ENGAGEMENT_POLL_INTERVAL", "300"))  # 5 minutes default
BATCH_SIZE = int(os.getenv("ENGAGEMENT_BATCH_SIZE", "50"))  # Posts per API batch
MAX_POST_AGE_HOURS = int(os.getenv("ENGAGEMENT_MAX_AGE_HOURS", "48"))  # Only refresh posts newer than this
REQUEST_DELAY_MS = int(os.getenv("ENGAGEMENT_REQUEST_DELAY_MS", "100"))  # Delay between API calls

# Graceful shutdown
shutdown_event = asyncio.Event()


def calculate_engagement_score(reblogs: int, favourites: int, replies: int) -> float:
    """Calculate weighted engagement score"""
    return (reblogs * 3.0) + (favourites * 2.0) + (replies * 1.0)


def get_posts_to_refresh() -> List[dict]:
    """Get posts that need engagement refresh from database"""
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=MAX_POST_AGE_HOURS)
    
    with get_db_session() as db:
        posts = db.query(MastodonPost).filter(
            MastodonPost.created_at >= cutoff_time,
            MastodonPost.visibility == "public",  # Only public posts can be fetched
            MastodonPost.reblog_of_id == None,  # Skip reblogs, fetch originals
        ).order_by(
            desc(MastodonPost.engagement_score),  # Prioritize already-popular posts
            desc(MastodonPost.created_at)
        ).limit(BATCH_SIZE).all()
        
        # Extract IDs and current metrics for comparison
        return [{
            "id": post.id,
            "url": post.url,
            "current_reblogs": post.reblogs_count,
            "current_favourites": post.favourites_count,
            "current_replies": post.replies_count,
        } for post in posts]


async def fetch_status_from_api(client: httpx.AsyncClient, status_id: str) -> Optional[dict]:
    """Fetch a single status from Mastodon API"""
    try:
        url = f"{MASTODON_INSTANCE}/api/v1/statuses/{status_id}"
        headers = {}
        if MASTODON_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {MASTODON_ACCESS_TOKEN}"
        
        response = await client.get(url, headers=headers)
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logger.debug(f"Status {status_id} not found (may be deleted)")
            return None
        elif response.status_code == 429:
            # Rate limited - extract retry-after if available
            retry_after = response.headers.get("X-RateLimit-Reset")
            logger.warning(f"Rate limited. Retry after: {retry_after}")
            return None
        else:
            logger.warning(f"Failed to fetch status {status_id}: HTTP {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching status {status_id}: {e}")
        return None


def update_post_metrics(post_id: str, new_metrics: dict) -> bool:
    """Update post engagement metrics in database"""
    try:
        with get_db_session() as db:
            post = db.query(MastodonPost).filter(MastodonPost.id == post_id).first()
            
            if not post:
                return False
            
            # Update metrics
            post.reblogs_count = new_metrics["reblogs_count"]
            post.favourites_count = new_metrics["favourites_count"]
            post.replies_count = new_metrics["replies_count"]
            post.engagement_score = calculate_engagement_score(
                new_metrics["reblogs_count"],
                new_metrics["favourites_count"],
                new_metrics["replies_count"]
            )
            
            # Update edited_at if the post was edited
            if new_metrics.get("edited_at"):
                post.edited_at = datetime.fromisoformat(
                    new_metrics["edited_at"].replace("Z", "+00:00")
                )
            
            return True
            
    except Exception as e:
        logger.error(f"Error updating post {post_id}: {e}")
        return False


async def poll_engagement_metrics():
    """Main polling loop - fetch and update engagement metrics"""
    logger.info(f"Starting engagement polling (interval: {POLL_INTERVAL_SECONDS}s, batch: {BATCH_SIZE})")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        while not shutdown_event.is_set():
            try:
                # Get posts to refresh
                posts_to_refresh = get_posts_to_refresh()
                
                if not posts_to_refresh:
                    logger.info("No posts to refresh")
                else:
                    logger.info(f"Refreshing engagement for {len(posts_to_refresh)} posts")
                    
                    updated_count = 0
                    changed_count = 0
                    
                    for post_info in posts_to_refresh:
                        if shutdown_event.is_set():
                            break
                        
                        # Fetch updated status from API
                        status_data = await fetch_status_from_api(client, post_info["id"])
                        
                        if status_data:
                            new_reblogs = status_data.get("reblogs_count", 0)
                            new_favourites = status_data.get("favourites_count", 0)
                            new_replies = status_data.get("replies_count", 0)
                            
                            # Check if metrics actually changed
                            metrics_changed = (
                                new_reblogs != post_info["current_reblogs"] or
                                new_favourites != post_info["current_favourites"] or
                                new_replies != post_info["current_replies"]
                            )
                            
                            if metrics_changed:
                                success = update_post_metrics(post_info["id"], {
                                    "reblogs_count": new_reblogs,
                                    "favourites_count": new_favourites,
                                    "replies_count": new_replies,
                                    "edited_at": status_data.get("edited_at"),
                                })
                                
                                if success:
                                    changed_count += 1
                                    old_score = calculate_engagement_score(
                                        post_info["current_reblogs"],
                                        post_info["current_favourites"],
                                        post_info["current_replies"]
                                    )
                                    new_score = calculate_engagement_score(
                                        new_reblogs, new_favourites, new_replies
                                    )
                                    logger.debug(
                                        f"Updated {post_info['id']}: "
                                        f"engagement {old_score:.1f} -> {new_score:.1f}"
                                    )
                            
                            updated_count += 1
                        
                        # Small delay to avoid hammering the API
                        await asyncio.sleep(REQUEST_DELAY_MS / 1000)
                    
                    logger.info(
                        f"Polling complete: {updated_count} fetched, "
                        f"{changed_count} had engagement changes"
                    )
                
                # Wait for next poll interval
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=POLL_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue polling
                    
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(30)  # Wait before retrying


async def refresh_single_batch():
    """Run a single refresh batch (for manual/scheduled runs)"""
    logger.info("Running single engagement refresh batch")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        posts_to_refresh = get_posts_to_refresh()
        
        if not posts_to_refresh:
            logger.info("No posts to refresh")
            return {"fetched": 0, "changed": 0}
        
        logger.info(f"Refreshing engagement for {len(posts_to_refresh)} posts")
        
        updated_count = 0
        changed_count = 0
        
        for post_info in posts_to_refresh:
            status_data = await fetch_status_from_api(client, post_info["id"])
            
            if status_data:
                new_reblogs = status_data.get("reblogs_count", 0)
                new_favourites = status_data.get("favourites_count", 0)
                new_replies = status_data.get("replies_count", 0)
                
                metrics_changed = (
                    new_reblogs != post_info["current_reblogs"] or
                    new_favourites != post_info["current_favourites"] or
                    new_replies != post_info["current_replies"]
                )
                
                if metrics_changed:
                    success = update_post_metrics(post_info["id"], {
                        "reblogs_count": new_reblogs,
                        "favourites_count": new_favourites,
                        "replies_count": new_replies,
                        "edited_at": status_data.get("edited_at"),
                    })
                    
                    if success:
                        changed_count += 1
                
                updated_count += 1
            
            await asyncio.sleep(REQUEST_DELAY_MS / 1000)
        
        logger.info(f"Batch complete: {updated_count} fetched, {changed_count} changed")
        return {"fetched": updated_count, "changed": changed_count}


def handle_shutdown(signum, frame):
    """Handle shutdown signals"""
    logger.info("Shutdown signal received")
    shutdown_event.set()


async def main():
    """Main entry point for continuous polling"""
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    
    logger.info(f"Starting engagement poller for {MASTODON_INSTANCE}")
    logger.info(f"Poll interval: {POLL_INTERVAL_SECONDS}s")
    logger.info(f"Batch size: {BATCH_SIZE}")
    logger.info(f"Max post age: {MAX_POST_AGE_HOURS}h")
    
    # Start polling
    await poll_engagement_metrics()
    
    logger.info("Engagement poller stopped")


if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Run the async main
    asyncio.run(main())
