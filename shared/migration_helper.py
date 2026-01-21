"""
Migration helper for upgrading existing Forkalytics databases to the new schema.
This script handles:
1. Creating the default instance
2. Migrating existing data to use instance_id
3. Backfilling normalized hashtags and mentions
"""
import os
import sys
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# Add shared module to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_db_session, engine
from models import Base, Instance, Hashtag, PostHashtag, PostMention

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("migration_helper")


def check_if_migration_needed():
    """Check if migration is needed by looking for the instances table"""
    with engine.connect() as conn:
        result = conn.execute(text(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'instances'
            );
            """
        ))
        exists = result.scalar()
        return not exists


def create_default_instance():
    """Create or get the default instance"""
    mastodon_instance = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")
    stream_type = os.getenv("STREAM_TYPE", "public:local")
    
    with get_db_session() as db:
        # Check if instance already exists
        instance = db.query(Instance).filter(Instance.base_url == mastodon_instance).first()
        
        if not instance:
            instance = Instance(
                base_url=mastodon_instance,
                stream_type=stream_type,
                created_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow()
            )
            db.add(instance)
            db.flush()
            logger.info(f"Created default instance: {mastodon_instance} (ID: {instance.id})")
        else:
            logger.info(f"Default instance already exists: {mastodon_instance} (ID: {instance.id})")
        
        return instance.id


def migrate_accounts_to_instance(instance_id: int):
    """Migrate existing accounts to use instance_id"""
    with engine.connect() as conn:
        # Check if we need to migrate (old schema has id as primary key only)
        result = conn.execute(text(
            """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'mastodon_accounts' AND column_name = 'instance_id';
            """
        ))
        has_instance_id = result.fetchone() is not None
        
        if not has_instance_id:
            logger.info("Accounts table needs migration - this will be handled by schema recreation")
        else:
            # Update any accounts that don't have instance_id set
            result = conn.execute(text(
                """
                UPDATE mastodon_accounts 
                SET instance_id = :instance_id 
                WHERE instance_id IS NULL;
                """
            ), {"instance_id": instance_id})
            conn.commit()
            logger.info(f"Updated {result.rowcount} accounts with instance_id")


def migrate_posts_to_instance(instance_id: int):
    """Migrate existing posts to use instance_id"""
    with engine.connect() as conn:
        # Check if we need to migrate
        result = conn.execute(text(
            """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'mastodon_posts' AND column_name = 'instance_id';
            """
        ))
        has_instance_id = result.fetchone() is not None
        
        if not has_instance_id:
            logger.info("Posts table needs migration - this will be handled by schema recreation")
        else:
            # Update any posts that don't have instance_id set
            result = conn.execute(text(
                """
                UPDATE mastodon_posts 
                SET instance_id = :instance_id 
                WHERE instance_id IS NULL;
                """
            ), {"instance_id": instance_id})
            conn.commit()
            logger.info(f"Updated {result.rowcount} posts with instance_id")


def backfill_normalized_hashtags(instance_id: int, batch_size: int = 1000):
    """
    Backfill normalized hashtags from JSON fields in existing posts.
    This is safe to run multiple times (idempotent).
    """
    logger.info("Starting hashtag normalization backfill...")
    
    with engine.connect() as conn:
        # Get posts with hashtags that haven't been normalized yet
        result = conn.execute(text(
            """
            SELECT p.id, p.instance_id, p.hashtags
            FROM mastodon_posts p
            WHERE p.hashtags IS NOT NULL 
              AND p.hashtags != 'null'
              AND p.hashtags::text != '[]'
              AND p.instance_id = :instance_id
              AND NOT EXISTS (
                  SELECT 1 FROM post_hashtags ph 
                  WHERE ph.post_id = p.id AND ph.instance_id = p.instance_id
              )
            LIMIT :batch_size;
            """
        ), {"instance_id": instance_id, "batch_size": batch_size})
        
        posts = result.fetchall()
        
        if not posts:
            logger.info("No posts with hashtags to backfill")
            return 0
        
        logger.info(f"Backfilling hashtags for {len(posts)} posts")
        
        total_tags_created = 0
        total_associations_created = 0
        
        with get_db_session() as db:
            for post_row in posts:
                post_id, post_instance_id, hashtags_json = post_row
                
                # Parse hashtags (they're stored as a JSON array)
                import json
                try:
                    if isinstance(hashtags_json, str):
                        hashtags = json.loads(hashtags_json)
                    else:
                        hashtags = hashtags_json
                    
                    if not isinstance(hashtags, list):
                        continue
                        
                    for tag_name in hashtags:
                        if not tag_name:
                            continue
                            
                        # Normalize tag name (lowercase)
                        tag_name_lower = tag_name.lower()
                        
                        # Get or create hashtag
                        hashtag = db.query(Hashtag).filter(
                            Hashtag.instance_id == post_instance_id,
                            Hashtag.name == tag_name_lower
                        ).first()
                        
                        if not hashtag:
                            hashtag = Hashtag(
                                instance_id=post_instance_id,
                                name=tag_name_lower,
                                first_seen_at=datetime.utcnow(),
                                last_seen_at=datetime.utcnow()
                            )
                            db.add(hashtag)
                            db.flush()
                            total_tags_created += 1
                        else:
                            hashtag.last_seen_at = datetime.utcnow()
                        
                        # Create association if it doesn't exist
                        existing_assoc = db.query(PostHashtag).filter(
                            PostHashtag.instance_id == post_instance_id,
                            PostHashtag.post_id == post_id,
                            PostHashtag.hashtag_id == hashtag.id
                        ).first()
                        
                        if not existing_assoc:
                            assoc = PostHashtag(
                                instance_id=post_instance_id,
                                post_id=post_id,
                                hashtag_id=hashtag.id
                            )
                            db.add(assoc)
                            total_associations_created += 1
                    
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse hashtags for post {post_id}: {e}")
                    continue
        
        logger.info(f"Created {total_tags_created} new hashtags and {total_associations_created} associations")
        return len(posts)


def backfill_normalized_mentions(instance_id: int, batch_size: int = 1000):
    """
    Backfill normalized mentions from JSON fields in existing posts.
    This is safe to run multiple times (idempotent).
    """
    logger.info("Starting mention normalization backfill...")
    
    with engine.connect() as conn:
        # Get posts with mentions that haven't been normalized yet
        result = conn.execute(text(
            """
            SELECT p.id, p.instance_id, p.mentions
            FROM mastodon_posts p
            WHERE p.mentions IS NOT NULL 
              AND p.mentions != 'null'
              AND p.mentions::text != '[]'
              AND p.instance_id = :instance_id
              AND NOT EXISTS (
                  SELECT 1 FROM post_mentions pm 
                  WHERE pm.post_id = p.id AND pm.instance_id = p.instance_id
              )
            LIMIT :batch_size;
            """
        ), {"instance_id": instance_id, "batch_size": batch_size})
        
        posts = result.fetchall()
        
        if not posts:
            logger.info("No posts with mentions to backfill")
            return 0
        
        logger.info(f"Backfilling mentions for {len(posts)} posts")
        
        total_mentions_created = 0
        
        with get_db_session() as db:
            for post_row in posts:
                post_id, post_instance_id, mentions_json = post_row
                
                # Parse mentions (stored as array of acct strings in current schema)
                import json
                try:
                    if isinstance(mentions_json, str):
                        mentions = json.loads(mentions_json)
                    else:
                        mentions = mentions_json
                    
                    if not isinstance(mentions, list):
                        continue
                    
                    for mention_acct in mentions:
                        if not mention_acct:
                            continue
                        
                        # Extract username from acct
                        username = mention_acct.split('@')[0] if '@' in mention_acct else mention_acct
                        
                        # Try to find the mentioned account in our database
                        # Note: We may not have all mentioned accounts if they're from other instances
                        mentioned_account = db.execute(text(
                            """
                            SELECT id, instance_id 
                            FROM mastodon_accounts 
                            WHERE acct = :acct AND instance_id = :instance_id
                            LIMIT 1;
                            """
                        ), {"acct": mention_acct, "instance_id": post_instance_id}).fetchone()
                        
                        if mentioned_account:
                            mentioned_account_id, mentioned_instance_id = mentioned_account
                        else:
                            # Use a placeholder for unknown accounts
                            # In production, you might want to skip these or fetch them from the API
                            mentioned_account_id = "unknown"
                            mentioned_instance_id = post_instance_id
                        
                        # Create mention association if it doesn't exist
                        existing_mention = db.query(PostMention).filter(
                            PostMention.instance_id == post_instance_id,
                            PostMention.post_id == post_id,
                            PostMention.mentioned_acct == mention_acct
                        ).first()
                        
                        if not existing_mention:
                            mention = PostMention(
                                instance_id=post_instance_id,
                                post_id=post_id,
                                mentioned_account_id=mentioned_account_id,
                                mentioned_account_instance_id=mentioned_instance_id,
                                mentioned_acct=mention_acct,
                                mentioned_username=username
                            )
                            db.add(mention)
                            total_mentions_created += 1
                    
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse mentions for post {post_id}: {e}")
                    continue
        
        logger.info(f"Created {total_mentions_created} mention associations")
        return len(posts)


def run_migration():
    """
    Main migration function.
    This is designed to be safe to run multiple times.
    """
    logger.info("Starting Forkalytics schema migration...")
    
    # Create all new tables (existing tables will be skipped)
    logger.info("Creating new tables...")
    Base.metadata.create_all(bind=engine)
    
    # Create default instance
    logger.info("Setting up default instance...")
    instance_id = create_default_instance()
    
    # Note: The composite primary key migration requires a more complex approach
    # For now, we'll document that existing data needs to be handled carefully
    logger.info("""
    ========================================================================
    IMPORTANT: Primary Key Migration Notice
    ========================================================================
    The new schema uses composite primary keys (id, instance_id) for:
    - mastodon_accounts
    - mastodon_posts
    
    If you have existing data, you have two options:
    
    1. FRESH START (Recommended for development):
       - Drop all tables and let the application recreate them
       - This is the cleanest approach
    
    2. PRESERVE DATA (For production):
       - This requires a more sophisticated migration script
       - You'll need to:
         a) Export existing data
         b) Drop and recreate tables with new schema
         c) Re-import data with instance_id populated
       - Contact the maintainer for assistance
    
    For now, this script assumes a fresh start or that you'll handle
    existing data migration manually.
    ========================================================================
    """)
    
    # Backfill normalized data (safe to run on both fresh and existing installs)
    try:
        logger.info("Backfilling normalized hashtags...")
        total = 0
        while True:
            count = backfill_normalized_hashtags(instance_id, batch_size=1000)
            total += count
            if count == 0:
                break
            logger.info(f"Progress: {total} posts processed so far...")
        
        logger.info("Backfilling normalized mentions...")
        total = 0
        while True:
            count = backfill_normalized_mentions(instance_id, batch_size=1000)
            total += count
            if count == 0:
                break
            logger.info(f"Progress: {total} posts processed so far...")
        
    except Exception as e:
        logger.error(f"Error during backfill: {e}")
        logger.info("You can run this script again later to complete the backfill")
    
    logger.info("Migration complete!")
    logger.info(f"Default instance ID: {instance_id}")


if __name__ == "__main__":
    run_migration()
