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
from shared.database import get_db_session, init_db, get_default_instance_id
from shared.models import (
    MastodonAccount, MastodonPost, PostMetricSnapshot, PostVersion,
    Hashtag, PostHashtag, PostMention, StreamEvent
)

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

# Get instance ID (cached after first lookup)
_instance_id_cache = None

def get_instance_id() -> int:
    """Get the instance ID for this worker"""
    global _instance_id_cache
    if _instance_id_cache is None:
        _instance_id_cache = get_default_instance_id()
    return _instance_id_cache

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
        
        # Extract domain from acct for analytics
        acct = account_data.get("acct", "")
        domain = acct.split('@')[1] if '@' in acct else None
        is_local = '@' not in acct or not domain
        
        # Create account dict with new fields
        account_dict = {
            "id": account_data.get("id"),
            "username": account_data.get("username", ""),
            "acct": acct,
            "display_name": account_data.get("display_name", ""),
            "followers_count": account_data.get("followers_count", 0),
            "following_count": account_data.get("following_count", 0),
            "statuses_count": account_data.get("statuses_count", 0),
            "bot": account_data.get("bot", False),
            "avatar_url": account_data.get("avatar"),
            "is_local": is_local,
            "domain": domain,
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
            "account_instance_id": None,  # Will be set during save
            "has_media": has_media,
            "media_types": media_types if media_types else None,
            "hashtags": hashtags if hashtags else None,  # Keep for backward compat
            "mentions": mentions if mentions else None,  # Keep for backward compat
            "created_at": created_at,
            "edited_at": edited_at,
        }
        
        # Return structured data including hashtags and mentions for normalization
        return account_dict, post_dict, hashtags, mentions_data
        
    except Exception as e:
        logger.error(f"Error parsing status: {e}")
        return None, None, None, None


def save_status(account_dict: dict, post_dict: dict, hashtags: list, mentions_data: list):
    """Save or update account and post in database with normalized hashtags and mentions"""
    instance_id = get_instance_id()
    
    with get_db_session() as db:
        # Upsert account
        existing_account = db.query(MastodonAccount).filter(
            MastodonAccount.id == account_dict["id"],
            MastodonAccount.instance_id == instance_id
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
            existing_account.is_local = account_dict["is_local"]
            existing_account.domain = account_dict["domain"]
            existing_account.last_seen_at = datetime.now(timezone.utc)
        else:
            account_dict["instance_id"] = instance_id
            db.add(MastodonAccount(**account_dict))
        
        # Upsert post
        post_dict["instance_id"] = instance_id
        post_dict["account_instance_id"] = instance_id
        
        existing_post = db.query(MastodonPost).filter(
            MastodonPost.id == post_dict["id"],
            MastodonPost.instance_id == instance_id
        ).first()
        
        is_new_post = existing_post is None
        is_edit = False
        
        if existing_post:
            # Check if this is an edit (content changed)
            is_edit = (
                existing_post.content != post_dict["content"] or
                existing_post.edited_at != post_dict["edited_at"]
            )
            
            # If edited, create a version entry
            if is_edit and existing_post.edited_at != post_dict["edited_at"]:
                # Find the next version sequence number
                max_version = db.query(PostVersion).filter(
                    PostVersion.instance_id == instance_id,
                    PostVersion.post_id == post_dict["id"]
                ).count()
                
                version = PostVersion(
                    instance_id=instance_id,
                    post_id=post_dict["id"],
                    version_seq=max_version + 1,
                    valid_from=post_dict["edited_at"] or datetime.now(timezone.utc),
                    content_html=post_dict["content"],
                    content_text=post_dict["content_text"],
                    spoiler_text=post_dict["spoiler_text"],
                    sensitive=post_dict["sensitive"],
                    hashtags_json=hashtags,
                    mentions_json=[m.get("acct") for m in mentions_data] if mentions_data else [],
                    edited_at=post_dict["edited_at"]
                )
                db.add(version)
            
            # Update engagement metrics
            existing_post.reblogs_count = post_dict["reblogs_count"]
            existing_post.favourites_count = post_dict["favourites_count"]
            existing_post.replies_count = post_dict["replies_count"]
            existing_post.engagement_score = post_dict["engagement_score"]
            existing_post.edited_at = post_dict["edited_at"]
            existing_post.content = post_dict["content"]
            existing_post.content_text = post_dict["content_text"]
        else:
            db.add(MastodonPost(**post_dict))
            db.flush()  # Ensure post is created before adding relationships
        
        # Create metric snapshot for time-series tracking
        snapshot = PostMetricSnapshot(
            instance_id=instance_id,
            post_id=post_dict["id"],
            captured_at=datetime.now(timezone.utc),
            replies_count=post_dict["replies_count"],
            reblogs_count=post_dict["reblogs_count"],
            favourites_count=post_dict["favourites_count"],
            engagement_score=post_dict["engagement_score"]
        )
        db.add(snapshot)
        
        # Normalize hashtags (only for new posts or edits)
        if is_new_post or is_edit:
            # Clear existing associations if this is an edit
            if is_edit:
                db.query(PostHashtag).filter(
                    PostHashtag.instance_id == instance_id,
                    PostHashtag.post_id == post_dict["id"]
                ).delete()
            
            for tag_name in hashtags:
                # Get or create hashtag
                tag_name_lower = tag_name.lower()
                hashtag = db.query(Hashtag).filter(
                    Hashtag.instance_id == instance_id,
                    Hashtag.name == tag_name_lower
                ).first()
                
                if not hashtag:
                    hashtag = Hashtag(
                        instance_id=instance_id,
                        name=tag_name_lower,
                        first_seen_at=datetime.now(timezone.utc),
                        last_seen_at=datetime.now(timezone.utc)
                    )
                    db.add(hashtag)
                    db.flush()
                else:
                    hashtag.last_seen_at = datetime.now(timezone.utc)
                
                # Create association
                post_hashtag = PostHashtag(
                    instance_id=instance_id,
                    post_id=post_dict["id"],
                    hashtag_id=hashtag.id
                )
                db.add(post_hashtag)
            
            # Normalize mentions (only for new posts or edits)
            if is_edit:
                db.query(PostMention).filter(
                    PostMention.instance_id == instance_id,
                    PostMention.post_id == post_dict["id"]
                ).delete()
            
            for mention_obj in mentions_data:
                mention_acct = mention_obj.get("acct", "")
                if not mention_acct:
                    continue
                
                # Extract info
                username = mention_obj.get("username", mention_acct.split('@')[0])
                
                # Try to find the mentioned account
                mentioned_account_id = mention_obj.get("id", "unknown")
                
                post_mention = PostMention(
                    instance_id=instance_id,
                    post_id=post_dict["id"],
                    mentioned_account_id=mentioned_account_id,
                    mentioned_account_instance_id=instance_id,
                    mentioned_acct=mention_acct,
                    mentioned_username=username
                )
                db.add(post_mention)


async def process_event(event_type: str, payload: str):
    """Process a streaming event"""
    instance_id = get_instance_id()
    
    # Log the stream event for replay/debugging
    try:
        with get_db_session() as db:
            # Parse payload to extract IDs
            payload_data = None
            payload_status_id = None
            payload_account_id = None
            
            try:
                if event_type != "delete":
                    payload_data = json.loads(payload)
                    payload_status_id = payload_data.get("id")
                    payload_account_id = payload_data.get("account", {}).get("id")
                else:
                    payload_status_id = payload  # Delete events just send the ID
            except:
                pass
            
            stream_event = StreamEvent(
                instance_id=instance_id,
                received_at=datetime.now(timezone.utc),
                event_type=event_type,
                payload=payload_data if payload_data else {"id": payload},
                payload_status_id=payload_status_id,
                payload_account_id=payload_account_id
            )
            db.add(stream_event)
    except Exception as e:
        logger.warning(f"Failed to log stream event: {e}")
    
    if event_type == "update":
        try:
            status_data = json.loads(payload)
            account_dict, post_dict, hashtags, mentions_data = parse_status(status_data)
            
            if account_dict and post_dict:
                save_status(account_dict, post_dict, hashtags, mentions_data)
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
            account_dict, post_dict, hashtags, mentions_data = parse_status(status_data)
            
            if account_dict and post_dict:
                save_status(account_dict, post_dict, hashtags, mentions_data)
                logger.info(f"Updated edited post {post_dict['id']}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse edited status JSON: {e}")
            
    elif event_type == "delete":
        # Post was deleted - soft delete (tombstone)
        try:
            deleted_post_id = payload
            with get_db_session() as db:
                post = db.query(MastodonPost).filter(
                    MastodonPost.id == deleted_post_id,
                    MastodonPost.instance_id == instance_id
                ).first()
                
                if post:
                    post.deleted_at = datetime.now(timezone.utc)
                    # Optionally redact content for privacy
                    # post.content = "[deleted]"
                    # post.content_text = "[deleted]"
                    logger.info(f"Soft-deleted post {deleted_post_id}")
                else:
                    logger.debug(f"Delete event for unknown post {deleted_post_id}")
        except Exception as e:
            logger.error(f"Failed to handle delete event: {e}")


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
