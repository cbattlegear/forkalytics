"""
Mastodon Streaming Worker
Connects to Mastodon streaming API and processes public timeline events
"""
import asyncio
import json
import os
import sys
import logging
import signal
from datetime import datetime, timezone
from typing import Optional
import re

import websockets
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
logger = logging.getLogger("mastodon_streamer")

# Configuration
MASTODON_INSTANCE = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")
STREAM_TYPE = os.getenv("STREAM_TYPE", "public:local")  # public, public:local, public:remote

# Graceful shutdown
shutdown_event = asyncio.Event()


def extract_text_from_html(html_content: str) -> str:
    """Extract plain text from HTML content"""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    # Replace <br> and </p> with newlines
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.append("\n")
    return soup.get_text(strip=True)


def calculate_engagement_score(reblogs: int, favourites: int, replies: int) -> float:
    """Calculate weighted engagement score"""
    # Weights: reblogs are most valuable, then favorites, then replies
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


def save_status(account_dict: dict, post_dict: dict):
    """Save or update account and post in database"""
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
        else:
            db.add(MastodonPost(**post_dict))


async def process_event(event_type: str, payload: str):
    """Process a streaming event"""
    if event_type == "update":
        try:
            status_data = json.loads(payload)
            account_dict, post_dict = parse_status(status_data)
            
            if account_dict and post_dict:
                save_status(account_dict, post_dict)
                logger.info(
                    f"Saved post {post_dict['id']} by @{account_dict['acct']} "
                    f"(engagement: {post_dict['engagement_score']:.1f})"
                )
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse status JSON: {e}")
            
    elif event_type == "status.update":
        # Post was edited
        try:
            status_data = json.loads(payload)
            account_dict, post_dict = parse_status(status_data)
            
            if account_dict and post_dict:
                save_status(account_dict, post_dict)
                logger.info(f"Updated edited post {post_dict['id']}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse edited status JSON: {e}")
            
    elif event_type == "delete":
        # Post was deleted - just log it, keep in DB for analytics
        logger.info(f"Post deleted: {payload}")


async def stream_public_timeline():
    """Connect to Mastodon streaming API and process events"""
    # Build WebSocket URL
    instance_host = MASTODON_INSTANCE.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{instance_host}/api/v1/streaming"
    
    headers = {}
    if MASTODON_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {MASTODON_ACCESS_TOKEN}"
    
    logger.info(f"Connecting to {ws_url}...")
    
    while not shutdown_event.is_set():
        try:
            async with websockets.connect(
                ws_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            ) as websocket:
                logger.info("Connected to Mastodon streaming API")
                
                # Subscribe to public timeline
                subscribe_msg = json.dumps({
                    "type": "subscribe",
                    "stream": STREAM_TYPE
                })
                await websocket.send(subscribe_msg)
                logger.info(f"Subscribed to {STREAM_TYPE} stream")
                
                # Process incoming messages
                async for message in websocket:
                    if shutdown_event.is_set():
                        break
                        
                    try:
                        data = json.loads(message)
                        event_type = data.get("event")
                        payload = data.get("payload", "")
                        
                        if event_type:
                            await process_event(event_type, payload)
                            
                    except json.JSONDecodeError:
                        # Might be a heartbeat or other non-JSON message
                        pass
                        
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Streaming error: {e}. Reconnecting in 10s...")
            await asyncio.sleep(10)


def handle_shutdown(signum, frame):
    """Handle shutdown signals"""
    logger.info("Shutdown signal received")
    shutdown_event.set()


async def main():
    """Main entry point"""
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    
    # Validate configuration
    if not MASTODON_ACCESS_TOKEN:
        logger.error("MASTODON_ACCESS_TOKEN is required!")
        sys.exit(1)
    
    logger.info(f"Starting Mastodon streamer for {MASTODON_INSTANCE}")
    logger.info(f"Stream type: {STREAM_TYPE}")
    
    # Start streaming
    await stream_public_timeline()
    
    logger.info("Streamer stopped")


if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Run the async main
    asyncio.run(main())
