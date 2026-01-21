"""
Analytics Scheduler
Runs periodic jobs for sentiment analysis and trend summarization
"""
import os
import sys
import logging
from datetime import datetime, timedelta
from typing import List
import json

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import openai
from sqlalchemy import func, desc
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Add shared module to path
sys.path.insert(0, "/app")
from shared.database import get_db_session, init_db, get_default_instance_id
from shared.models import (
    MastodonPost, PostSentiment, DailySummary, HourlyStat, HourlyTopic,
    Hashtag, PostHashtag, HashtagHourlyStat
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("analytics_scheduler")

# Initialize VADER sentiment analyzer (runs locally, no API needed)
vader_analyzer = SentimentIntensityAnalyzer()

# Configuration - treat empty strings as None
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None
SENTIMENT_BATCH_SIZE = 100  # Can process more since VADER is fast

# Get instance ID (cached after first lookup)
_instance_id_cache = None

def get_instance_id() -> int:
    """Get the instance ID for this worker"""
    global _instance_id_cache
    if _instance_id_cache is None:
        _instance_id_cache = get_default_instance_id()
    return _instance_id_cache

# Azure OpenAI Configuration - treat empty strings as None
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT") or None
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY") or None
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT") or None  # Deployment name for the model
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

# Model name (used for standard OpenAI, ignored for Azure where deployment is used)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Initialize OpenAI client (Azure or standard)
client = None
using_azure = False

if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT:
    # Use Azure OpenAI
    from openai import AzureOpenAI
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )
    using_azure = True
    # For Azure, use the deployment name as the model
    OPENAI_MODEL = AZURE_OPENAI_DEPLOYMENT
    logger.info(f"Using Azure OpenAI endpoint: {AZURE_OPENAI_ENDPOINT}, deployment: {AZURE_OPENAI_DEPLOYMENT}")
elif OPENAI_API_KEY:
    # Use standard OpenAI
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    logger.info(f"Using standard OpenAI with model: {OPENAI_MODEL}")
else:
    # Log which configuration is missing to help debugging
    logger.info("No OpenAI API configured. AI-powered summaries will be disabled (VADER sentiment still works).")
    if AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_API_KEY or AZURE_OPENAI_DEPLOYMENT:
        missing = []
        if not AZURE_OPENAI_ENDPOINT:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not AZURE_OPENAI_API_KEY:
            missing.append("AZURE_OPENAI_API_KEY")
        if not AZURE_OPENAI_DEPLOYMENT:
            missing.append("AZURE_OPENAI_DEPLOYMENT")
        logger.info(f"Partial Azure config detected. Missing: {', '.join(missing)}")


def analyze_sentiment_batch():
    """Analyze sentiment for unprocessed posts using VADER (local, no API needed)"""
    instance_id = get_instance_id()
    
    with get_db_session() as db:
        # Get posts that haven't been analyzed
        posts = db.query(MastodonPost).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.sentiment_analyzed == False,
            MastodonPost.content_text != None,
            MastodonPost.content_text != "",
            MastodonPost.deleted_at == None  # Skip deleted posts
        ).limit(SENTIMENT_BATCH_SIZE).all()
        
        if not posts:
            logger.info("No posts to analyze")
            return
        
        logger.info(f"Analyzing sentiment for {len(posts)} posts using VADER")
        
        analyzed_count = 0
        for post in posts:
            try:
                # Skip very short posts
                if len(post.content_text) < 10:
                    post.sentiment_analyzed = True
                    continue
                
                # Use VADER for sentiment analysis (fast, local, no API)
                scores = vader_analyzer.polarity_scores(post.content_text)
                compound = scores['compound']
                
                # Determine label based on compound score
                if compound >= 0.05:
                    label = "positive"
                elif compound <= -0.05:
                    label = "negative"
                else:
                    label = "neutral"
                
                # Calculate hash of input for deduplication
                import hashlib
                input_hash = hashlib.sha256(post.content_text.encode()).hexdigest()
                
                # Create sentiment record with audit metadata
                sentiment = PostSentiment(
                    instance_id=instance_id,
                    post_id=post.id,
                    sentiment_score=compound,
                    sentiment_label=label,
                    topics=[],  # VADER doesn't extract topics
                    model="vader",
                    model_version="vader-3.3.2",
                    prompt_version="n/a",  # VADER doesn't use prompts
                    input_hash=input_hash[:64],
                    analyzed_at=datetime.utcnow()
                )
                db.add(sentiment)
                
                # Mark post as analyzed
                post.sentiment_analyzed = True
                analyzed_count += 1
                
            except Exception as e:
                logger.error(f"Error analyzing post {post.id}: {e}")
                post.sentiment_analyzed = True  # Skip this post
        
        logger.info(f"Completed sentiment analysis: {analyzed_count} posts analyzed")


def generate_hourly_stats(target_hour: datetime = None, force: bool = False):
    """Aggregate hourly statistics for a specific hour or the last complete hour"""
    instance_id = get_instance_id()
    
    with get_db_session() as db:
        # Determine which hour to process
        if target_hour:
            hour_start = target_hour.replace(minute=0, second=0, microsecond=0)
        else:
            now = datetime.utcnow()
            current_hour = now.replace(minute=0, second=0, microsecond=0)
            hour_start = current_hour - timedelta(hours=1)
        
        hour_end = hour_start + timedelta(hours=1)
        
        # Check if we already have stats for this hour
        existing = db.query(HourlyStat).filter(
            HourlyStat.instance_id == instance_id,
            HourlyStat.hour == hour_start
        ).first()
        if existing:
            if force:
                db.delete(existing)
                db.commit()
                logger.info(f"Deleted existing stats for {hour_start} (force mode)")
            else:
                logger.debug(f"Stats already exist for {hour_start}")
                return
        
        # Aggregate posts from the hour (excluding deleted posts)
        stats = db.query(
            func.count(MastodonPost.id).label("post_count"),
            func.sum(MastodonPost.reblogs_count).label("reblog_count"),
            func.sum(MastodonPost.replies_count).label("reply_count"),
            func.sum(MastodonPost.engagement_score).label("total_engagement"),
            func.avg(MastodonPost.engagement_score).label("avg_engagement")
        ).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= hour_start,
            MastodonPost.created_at < hour_end,
            MastodonPost.deleted_at == None
        ).first()
        
        # Get average sentiment
        sentiment_avg = db.query(
            func.avg(PostSentiment.sentiment_score)
        ).join(
            MastodonPost,
            (PostSentiment.post_id == MastodonPost.id) & 
            (PostSentiment.instance_id == MastodonPost.instance_id)
        ).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= hour_start,
            MastodonPost.created_at < hour_end,
            MastodonPost.deleted_at == None
        ).scalar()
        
        # Get top hashtag
        # This is a simplified approach - for better performance, use post_hashtags table
        top_hashtag = None
        
        hourly_stat = HourlyStat(
            instance_id=instance_id,
            hour=hour_start,
            post_count=stats.post_count or 0,
            reblog_count=int(stats.reblog_count or 0),
            reply_count=int(stats.reply_count or 0),
            total_engagement=int(stats.total_engagement or 0),
            avg_engagement=float(stats.avg_engagement or 0),
            avg_sentiment=sentiment_avg,
            top_hashtag=top_hashtag,
            definition_version="v1.0",
            computed_at=datetime.utcnow()
        )
        if(stats.post_count > 0):
            db.add(hourly_stat)
            logger.info(f"Generated hourly stats for {hour_start}: {stats.post_count} posts")
        else:
            logger.info(f"No posts to generate hourly stats for {hour_start}")


def generate_hourly_stats_rolling(hours: int = 48):
    """Regenerate hourly statistics for the last N hours to capture updated engagement metrics"""
    logger.info(f"Regenerating hourly stats for the last {hours} hours")
    
    now = datetime.utcnow()
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    
    updated_count = 0
    for i in range(1, hours + 1):  # Start from 1 to skip current incomplete hour
        target_hour = current_hour - timedelta(hours=i)
        generate_hourly_stats(target_hour=target_hour, force=True)
        updated_count += 1
    
    logger.info(f"Completed regenerating {updated_count} hours of stats")


def extract_hourly_topics(target_hour: datetime = None, force: bool = False):
    """Extract trending topics from post content using AI for a specific hour"""
    if not client:
        logger.warning("OpenAI API key not configured, skipping topic extraction")
        return
    
    instance_id = get_instance_id()
    
    with get_db_session() as db:
        # Determine which hour to process
        if target_hour:
            hour_start = target_hour.replace(minute=0, second=0, microsecond=0)
        else:
            now = datetime.utcnow()
            current_hour = now.replace(minute=0, second=0, microsecond=0)
            hour_start = current_hour - timedelta(hours=1)
        
        hour_end = hour_start + timedelta(hours=1)
        
        # Check if we already have topics for this hour
        existing = db.query(HourlyTopic).filter(
            HourlyTopic.instance_id == instance_id,
            HourlyTopic.hour_start == hour_start
        ).first()
        if existing:
            if force:
                db.query(HourlyTopic).filter(
                    HourlyTopic.instance_id == instance_id,
                    HourlyTopic.hour_start == hour_start
                ).delete()
                # Committing delete so insert actually succeeds
                db.commit()
                logger.info(f"Deleted existing topics for {hour_start} (force mode)")
            else:
                logger.debug(f"Topics already exist for {hour_start}")
                return
        
        # Get posts from the hour, ordered by engagement (excluding deleted posts)
        posts = db.query(MastodonPost).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= hour_start,
            MastodonPost.created_at < hour_end,
            MastodonPost.content_text != None,
            MastodonPost.content_text != "",
            MastodonPost.deleted_at == None
        ).order_by(desc(MastodonPost.engagement_score)).limit(200).all()
        
        if len(posts) < 10:
            logger.info(f"Not enough posts for topic extraction ({len(posts)} posts) for hour {hour_start}")
            return
        
        logger.info(f"Extracting topics from {len(posts)} posts for hour {hour_start}")
        
        # Prepare content for OpenAI
        posts_text = "\n---\n".join([
            f"[ID:{p.id}] {p.content_text[:500]}" 
            for p in posts if p.content_text
        ])
        
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": """Analyze these social media posts and identify the top 5-8 trending topics/subjects being discussed.
                        For each topic, provide:
                        - topic: A short label (2-5 words)
                        - summary: One sentence describing what people are saying about it
                        - sentiment: average sentiment (-1 to 1) based on how people feel about this topic
                        - post_ids: list of post IDs from the input that discuss this topic
                        
                        Focus on substantive topics, not generic things like "greetings" or "daily life".
                        Return valid JSON only."""
                    },
                    {
                        "role": "user",
                        "content": f"Posts from the last hour:\n{posts_text}\n\nRespond with JSON: {{\"topics\": [{{\"topic\": \"...\", \"summary\": \"...\", \"sentiment\": 0.0, \"post_ids\": [...]}}]}}"
                    }
                ],
                temperature=0.5,
                max_tokens=1500
            )
            
            result_text = response.choices[0].message.content.strip()
            # Clean up potential markdown code blocks
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            
            data = json.loads(result_text)
            
            topics_added = 0
            for topic_data in data.get("topics", []):
                topic = HourlyTopic(
                    instance_id=instance_id,
                    hour_start=hour_start,
                    topic=topic_data.get("topic", "Unknown"),
                    summary=topic_data.get("summary"),
                    post_count=len(topic_data.get("post_ids", [])),
                    avg_sentiment=topic_data.get("sentiment"),
                    sample_post_ids=topic_data.get("post_ids", [])[:10]
                )
                db.add(topic)
                topics_added += 1
            
            logger.info(f"Extracted {topics_added} topics for hour {hour_start}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse topic extraction response: {e}")
        except Exception as e:
            logger.error(f"Error extracting topics: {e}")


def generate_daily_summary(target_date: datetime = None, force: bool = False):
    """Generate AI daily summary for a specific date or yesterday"""
    if not client:
        logger.warning("OpenAI API key not configured, skipping daily summary")
        return
    
    instance_id = get_instance_id()
    
    with get_db_session() as db:
        # Determine which day to process
        if target_date:
            day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            day_start = today - timedelta(days=1)
        
        day_end = day_start + timedelta(days=1)
        
        # Check if summary already exists
        existing = db.query(DailySummary).filter(
            DailySummary.instance_id == instance_id,
            DailySummary.date == day_start
        ).first()
        if existing:
            if force:
                db.delete(existing)
                # Committing delete so insert actually succeeds
                db.commit()
                logger.info(f"Deleted existing summary for {day_start.date()} (force mode)")
            else:
                logger.info(f"Daily summary already exists for {day_start.date()}")
                return
        
        # Gather statistics (excluding deleted posts)
        post_stats = db.query(
            func.count(MastodonPost.id).label("total_posts"),
            func.sum(MastodonPost.engagement_score).label("total_engagement"),
            func.count(func.distinct(MastodonPost.account_id)).label("unique_authors")
        ).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= day_start,
            MastodonPost.created_at < day_end,
            MastodonPost.deleted_at == None
        ).first()
        
        # Sentiment breakdown
        sentiment_stats = db.query(
            func.avg(PostSentiment.sentiment_score).label("avg_sentiment"),
            func.count().filter(PostSentiment.sentiment_label == "positive").label("positive_count"),
            func.count().filter(PostSentiment.sentiment_label == "negative").label("negative_count"),
            func.count().filter(PostSentiment.sentiment_label == "neutral").label("neutral_count")
        ).join(
            MastodonPost,
            (PostSentiment.post_id == MastodonPost.id) & 
            (PostSentiment.instance_id == MastodonPost.instance_id)
        ).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= day_start,
            MastodonPost.created_at < day_end,
            MastodonPost.deleted_at == None
        ).first()
        
        # Get top posts for context
        top_posts = db.query(MastodonPost).filter(
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= day_start,
            MastodonPost.created_at < day_end,
            MastodonPost.deleted_at == None
        ).order_by(desc(MastodonPost.engagement_score)).limit(20).all()
        
        # Prepare input stats for audit
        input_stats = {
            "total_posts": post_stats.total_posts or 0,
            "total_engagement": int(post_stats.total_engagement or 0),
            "unique_authors": post_stats.unique_authors or 0,
            "avg_sentiment": float(sentiment_stats.avg_sentiment) if sentiment_stats.avg_sentiment else None,
            "positive_count": sentiment_stats.positive_count or 0,
            "negative_count": sentiment_stats.negative_count or 0,
            "neutral_count": sentiment_stats.neutral_count or 0
        }
        
        # Calculate hash of input
        import hashlib
        input_hash = hashlib.sha256(json.dumps(input_stats, sort_keys=True).encode()).hexdigest()
        
        # Prepare content for AI summary
        posts_context = "\n".join([
            f"- {p.content_text[:200]}... (engagement: {p.engagement_score})"
            for p in top_posts if p.content_text
        ])
        
        # Generate AI summary
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": """You are an analytics assistant summarizing a day of social media activity on Mastodon.
                        Generate a concise summary including:
                        1. Overall mood/sentiment of the day
                        2. Key trending topics or themes
                        3. Notable events or discussions
                        4. Any interesting patterns
                        
                        Return a JSON object with:
                        - summary_text: 2-3 paragraph natural language summary
                        - trending_topics: array of 5-10 topic strings
                        - notable_events: array of 2-5 brief event descriptions"""
                    },
                    {
                        "role": "user",
                        "content": f"""Daily statistics:
                        - Total posts: {post_stats.total_posts}
                        - Total engagement: {post_stats.total_engagement}
                        - Unique authors: {post_stats.unique_authors}
                        - Average sentiment: {f'{sentiment_stats.avg_sentiment:.2f}' if sentiment_stats.avg_sentiment else 'N/A'}
                        - Positive posts: {sentiment_stats.positive_count}
                        - Negative posts: {sentiment_stats.negative_count}
                        - Neutral posts: {sentiment_stats.neutral_count}
                        
                        Top engaging posts:
                        {posts_context}"""
                    }
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            result_text = response.choices[0].message.content.strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            
            ai_result = json.loads(result_text)
            
            summary = DailySummary(
                instance_id=instance_id,
                date=day_start,
                window_start=day_start,
                window_end=day_end,
                total_posts=post_stats.total_posts or 0,
                total_engagement=int(post_stats.total_engagement or 0),
                unique_authors=post_stats.unique_authors or 0,
                avg_sentiment=sentiment_stats.avg_sentiment,
                positive_count=sentiment_stats.positive_count or 0,
                negative_count=sentiment_stats.negative_count or 0,
                neutral_count=sentiment_stats.neutral_count or 0,
                summary_text=ai_result.get("summary_text", ""),
                trending_topics=ai_result.get("trending_topics", []),
                notable_events=ai_result.get("notable_events", []),
                model=OPENAI_MODEL,
                prompt_version="v1.0",
                input_stats_json=input_stats,
                input_hash=input_hash[:64],
                generated_at=datetime.utcnow()
            )
            
            db.add(summary)
            logger.info(f"Generated daily summary for {day_start.date()}")
            
        except Exception as e:
            logger.error(f"Error generating daily summary: {e}")


def main():
    """Main scheduler entry point"""
    logger.info("Starting analytics scheduler")
    
    # Initialize database
    init_db()
    
    # Create scheduler
    scheduler = BlockingScheduler()
    
    # Sentiment analysis every 5 minutes
    scheduler.add_job(
        analyze_sentiment_batch,
        IntervalTrigger(minutes=5),
        id="sentiment_analysis",
        name="Analyze sentiment for new posts"
    )
    
    # Hourly stats at the top of every hour - reprocess last 48 hours to capture engagement updates
    scheduler.add_job(
        generate_hourly_stats_rolling,
        CronTrigger(minute=5),  # 5 minutes past every hour
        id="hourly_stats",
        name="Regenerate hourly statistics (last 48h)"
    )
    
    # Hourly topic extraction at 10 minutes past every hour
    scheduler.add_job(
        extract_hourly_topics,
        CronTrigger(minute=10),
        id="hourly_topics",
        name="Extract hourly trending topics"
    )
    
    # Daily summary at 1 AM UTC
    scheduler.add_job(
        generate_daily_summary,
        CronTrigger(hour=1, minute=0),
        id="daily_summary",
        name="Generate daily summary"
    )
    
    # Run initial jobs
    logger.info("Running initial sentiment analysis...")
    analyze_sentiment_batch()
    
    logger.info("Scheduler started. Jobs scheduled:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")
    
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
