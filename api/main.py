"""
Forkalytics API
FastAPI backend for analytics endpoints
"""
import os
import sys
import json
import httpx
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add shared module to path
sys.path.insert(0, "/app")
from shared.database import get_db, init_db, get_default_instance_id
from shared.models import (
    MastodonPost, MastodonAccount, PostSentiment, DailySummary, HourlyStat, HourlyTopic,
    Hashtag, PostHashtag, PostMetricSnapshot
)

# Configuration
MASTODON_INSTANCE = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")

# Get instance ID (cached after first lookup)
_instance_id_cache = None

def get_instance_id() -> int:
    """Get the instance ID for this API"""
    global _instance_id_cache
    if _instance_id_cache is None:
        _instance_id_cache = get_default_instance_id()
    return _instance_id_cache

# Initialize FastAPI
app = FastAPI(
    title="Forkalytics API",
    description="Analytics API for Mastodon instance monitoring",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for responses
class AccountResponse(BaseModel):
    id: str
    username: str
    acct: str
    display_name: Optional[str]
    followers_count: int
    avatar_url: Optional[str]
    
    class Config:
        from_attributes = True


class PostResponse(BaseModel):
    id: str
    url: Optional[str]
    content: Optional[str]
    content_text: Optional[str]
    language: Optional[str]
    reblogs_count: int
    favourites_count: int
    replies_count: int
    engagement_score: float
    has_media: bool
    hashtags: Optional[List[str]]
    created_at: datetime
    account: AccountResponse
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    
    class Config:
        from_attributes = True


class HourlyStatResponse(BaseModel):
    hour: datetime
    post_count: int
    reblog_count: int
    reply_count: int
    total_engagement: int
    avg_engagement: float
    avg_sentiment: Optional[float]
    
    class Config:
        from_attributes = True


class DailySummaryResponse(BaseModel):
    date: datetime
    total_posts: int
    total_engagement: int
    unique_authors: int
    avg_sentiment: Optional[float]
    positive_count: int
    negative_count: int
    neutral_count: int
    summary_text: Optional[str]
    trending_topics: Optional[List[str]]
    notable_events: Optional[List[str]]
    
    class Config:
        from_attributes = True


class SentimentOverview(BaseModel):
    avg_sentiment: Optional[float]
    positive_count: int
    negative_count: int
    neutral_count: int
    total_analyzed: int


class HashtagCount(BaseModel):
    hashtag: str
    count: int


class OverviewStats(BaseModel):
    total_users: int
    active_users: int
    total_posts: int
    recent_posts: int
    avg_engagement: float
    bot_count: int
    human_count: int
    activity_window_hours: int
    # Instance stats from Mastodon API
    instance_user_count: Optional[int] = None
    instance_status_count: Optional[int] = None
    instance_domain_count: Optional[int] = None
    instance_active_month: Optional[int] = None
    instance_name: Optional[str] = None


class StatsOverview(BaseModel):
    total_posts: int
    total_accounts: int
    posts_today: int
    posts_this_hour: int
    avg_engagement: float
    sentiment: SentimentOverview
    mastodon_instance: str = MASTODON_INSTANCE


# Startup event
@app.on_event("startup")
async def startup():
    init_db()


# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# Overview stats
@app.get("/api/stats", response_model=StatsOverview)
async def get_stats(db: Session = Depends(get_db)):
    """Get overview statistics"""
    instance_id = get_instance_id()
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    
    total_posts = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.deleted_at == None
    ).scalar()
    
    total_accounts = db.query(func.count(MastodonAccount.id)).filter(
        MastodonAccount.instance_id == instance_id
    ).scalar()
    
    posts_today = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= today_start,
        MastodonPost.deleted_at == None
    ).scalar()
    
    posts_this_hour = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= hour_start,
        MastodonPost.deleted_at == None
    ).scalar()
    
    avg_engagement = db.query(func.avg(MastodonPost.engagement_score)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.deleted_at == None
    ).scalar() or 0
    
    # Sentiment stats
    sentiment_query = db.query(
        func.avg(PostSentiment.sentiment_score).label("avg"),
        func.count().filter(PostSentiment.sentiment_label == "positive").label("positive"),
        func.count().filter(PostSentiment.sentiment_label == "negative").label("negative"),
        func.count().filter(PostSentiment.sentiment_label == "neutral").label("neutral"),
        func.count().label("total")
    ).filter(
        PostSentiment.instance_id == instance_id
    ).first()
    
    return StatsOverview(
        total_posts=total_posts or 0,
        total_accounts=total_accounts or 0,
        posts_today=posts_today or 0,
        posts_this_hour=posts_this_hour or 0,
        avg_engagement=float(avg_engagement),
        sentiment=SentimentOverview(
            avg_sentiment=sentiment_query.avg,
            positive_count=sentiment_query.positive or 0,
            negative_count=sentiment_query.negative or 0,
            neutral_count=sentiment_query.neutral or 0,
            total_analyzed=sentiment_query.total or 0
        )
    )


# User overview stats
@app.get("/api/stats/overview", response_model=OverviewStats)
async def get_overview_stats(db: Session = Depends(get_db)):
    """Get overview statistics including user counts"""
    instance_id = get_instance_id()
    now = datetime.now(timezone.utc)
    last_48_hours = now - timedelta(hours=48)
    
    # Total users (accounts we've seen)
    total_users = db.query(func.count(MastodonAccount.id)).filter(
        MastodonAccount.instance_id == instance_id
    ).scalar() or 0
    
    # Active users (posted in last 48 hours)
    active_users = db.query(func.count(func.distinct(MastodonPost.account_id))).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= last_48_hours,
        MastodonPost.deleted_at == None
    ).scalar() or 0
    
    # Total posts (not deleted)
    total_posts = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.deleted_at == None
    ).scalar() or 0
    
    # Posts in last 48 hours
    recent_posts = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= last_48_hours,
        MastodonPost.deleted_at == None
    ).scalar() or 0
    
    # Average engagement score (last 48 hours)
    avg_engagement = db.query(func.avg(MastodonPost.engagement_score)).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= last_48_hours,
        MastodonPost.deleted_at == None
    ).scalar() or 0
    
    # Bot vs human breakdown
    bot_count = db.query(func.count(MastodonAccount.id)).filter(
        MastodonAccount.instance_id == instance_id,
        MastodonAccount.bot == True
    ).scalar() or 0
    
    human_count = total_users - bot_count
    
    # Fetch instance stats from Mastodon API
    instance_user_count = None
    instance_status_count = None
    instance_domain_count = None
    instance_active_month = None
    instance_name = None
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{MASTODON_INSTANCE}/api/v2/instance")
            if response.status_code == 200:
                data = response.json()
                instance_name = data.get("title") or data.get("domain")
                
                # Stats are in different places depending on API version
                stats = data.get("stats", {})
                usage = data.get("usage", {})
                
                instance_user_count = stats.get("user_count")
                instance_status_count = stats.get("status_count")
                instance_domain_count = stats.get("domain_count")
                instance_active_month = usage.get("users", {}).get("active_month")
    except Exception as e:
        # Log but don't fail if we can't reach the instance API
        print(f"Warning: Could not fetch instance stats: {e}")
    
    return OverviewStats(
        total_users=total_users,
        active_users=active_users,
        total_posts=total_posts,
        recent_posts=recent_posts,
        avg_engagement=round(float(avg_engagement), 2),
        bot_count=bot_count,
        human_count=human_count,
        activity_window_hours=48,
        instance_user_count=instance_user_count,
        instance_status_count=instance_status_count,
        instance_domain_count=instance_domain_count,
        instance_active_month=instance_active_month,
        instance_name=instance_name
    )


# Popular posts
@app.get("/api/posts/popular", response_model=List[PostResponse])
async def get_popular_posts(
    hours: int = Query(24, ge=1, le=168, description="Time window in hours"),
    limit: int = Query(20, ge=1, le=100),
    language: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get most popular posts by engagement score"""
    instance_id = get_instance_id()
    since = datetime.utcnow() - timedelta(hours=hours)
    
    query = db.query(MastodonPost).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= since,
        MastodonPost.deleted_at == None
    )
    
    if language:
        query = query.filter(MastodonPost.language == language)
    
    posts = query.order_by(desc(MastodonPost.engagement_score)).limit(limit).all()
    
    # Enrich with sentiment data
    result = []
    for post in posts:
        sentiment = db.query(PostSentiment).filter(
            PostSentiment.instance_id == instance_id,
            PostSentiment.post_id == post.id
        ).first()
        
        post_data = PostResponse(
            id=post.id,
            url=post.url,
            content=post.content,
            content_text=post.content_text,
            language=post.language,
            reblogs_count=post.reblogs_count,
            favourites_count=post.favourites_count,
            replies_count=post.replies_count,
            engagement_score=post.engagement_score,
            has_media=post.has_media,
            hashtags=post.hashtags,
            created_at=post.created_at,
            account=AccountResponse(
                id=post.account.id,
                username=post.account.username,
                acct=post.account.acct,
                display_name=post.account.display_name,
                followers_count=post.account.followers_count,
                avatar_url=post.account.avatar_url
            ),
            sentiment_label=sentiment.sentiment_label if sentiment else None,
            sentiment_score=sentiment.sentiment_score if sentiment else None
        )
        result.append(post_data)
    
    return result


# Recent posts
@app.get("/api/posts/recent", response_model=List[PostResponse])
async def get_recent_posts(
    limit: int = Query(50, ge=1, le=100),
    language: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get most recent posts"""
    instance_id = get_instance_id()
    query = db.query(MastodonPost).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.deleted_at == None
    )
    
    if language:
        query = query.filter(MastodonPost.language == language)
    
    posts = query.order_by(desc(MastodonPost.created_at)).limit(limit).all()
    
    result = []
    for post in posts:
        sentiment = db.query(PostSentiment).filter(
            PostSentiment.instance_id == instance_id,
            PostSentiment.post_id == post.id
        ).first()
        
        post_data = PostResponse(
            id=post.id,
            url=post.url,
            content=post.content,
            content_text=post.content_text,
            language=post.language,
            reblogs_count=post.reblogs_count,
            favourites_count=post.favourites_count,
            replies_count=post.replies_count,
            engagement_score=post.engagement_score,
            has_media=post.has_media,
            hashtags=post.hashtags,
            created_at=post.created_at,
            account=AccountResponse(
                id=post.account.id,
                username=post.account.username,
                acct=post.account.acct,
                display_name=post.account.display_name,
                followers_count=post.account.followers_count,
                avatar_url=post.account.avatar_url
            ),
            sentiment_label=sentiment.sentiment_label if sentiment else None,
            sentiment_score=sentiment.sentiment_score if sentiment else None
        )
        result.append(post_data)
    
    return result


# Hourly stats
@app.get("/api/stats/hourly", response_model=List[HourlyStatResponse])
async def get_hourly_stats(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db)
):
    """Get hourly statistics"""
    instance_id = get_instance_id()
    since = datetime.utcnow() - timedelta(hours=hours)
    
    stats = db.query(HourlyStat).filter(
        HourlyStat.instance_id == instance_id,
        HourlyStat.hour >= since
    ).order_by(HourlyStat.hour).all()
    
    return stats


# Daily summaries
@app.get("/api/summaries", response_model=List[DailySummaryResponse])
async def get_daily_summaries(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db)
):
    """Get daily AI-generated summaries"""
    instance_id = get_instance_id()
    since = datetime.utcnow() - timedelta(days=days)
    
    summaries = db.query(DailySummary).filter(
        DailySummary.instance_id == instance_id,
        DailySummary.date >= since
    ).order_by(desc(DailySummary.date)).all()
    
    return summaries


# Latest summary
@app.get("/api/summaries/latest", response_model=Optional[DailySummaryResponse])
async def get_latest_summary(db: Session = Depends(get_db)):
    """Get the most recent daily summary"""
    instance_id = get_instance_id()
    summary = db.query(DailySummary).filter(
        DailySummary.instance_id == instance_id
    ).order_by(desc(DailySummary.date)).first()
    
    if not summary:
        raise HTTPException(status_code=404, detail="No summaries available yet")
    
    return summary


# Trending hashtags
@app.get("/api/hashtags/trending", response_model=List[HashtagCount])
async def get_trending_hashtags(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """Get trending hashtags using normalized hashtag tables"""
    instance_id = get_instance_id()
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Try to use normalized hashtag tables first
    try:
        # Query using PostHashtag join
        hashtag_counts = db.query(
            Hashtag.name,
            func.count(PostHashtag.post_id).label("count")
        ).join(
            PostHashtag, Hashtag.id == PostHashtag.hashtag_id
        ).join(
            MastodonPost,
            (PostHashtag.post_id == MastodonPost.id) & 
            (PostHashtag.instance_id == MastodonPost.instance_id)
        ).filter(
            Hashtag.instance_id == instance_id,
            MastodonPost.instance_id == instance_id,
            MastodonPost.created_at >= since,
            MastodonPost.deleted_at == None
        ).group_by(
            Hashtag.name
        ).order_by(
            desc("count")
        ).limit(limit).all()
        
        if hashtag_counts:
            return [HashtagCount(hashtag=tag, count=count) for tag, count in hashtag_counts]
    except Exception as e:
        logger.warning(f"Could not use normalized hashtags, falling back to JSON: {e}")
    
    # Fallback to JSON-based approach for backward compatibility
    posts = db.query(MastodonPost.hashtags).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= since,
        MastodonPost.hashtags != None,
        MastodonPost.deleted_at == None
    ).all()
    
    # Count hashtags
    hashtag_counts = {}
    for (hashtags,) in posts:
        if hashtags:
            for tag in hashtags:
                tag_lower = tag.lower()
                hashtag_counts[tag_lower] = hashtag_counts.get(tag_lower, 0) + 1
    
    # Sort and limit
    sorted_tags = sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    
    return [HashtagCount(hashtag=tag, count=count) for tag, count in sorted_tags]


# Sentiment distribution
@app.get("/api/sentiment/distribution")
async def get_sentiment_distribution(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db)
):
    """Get sentiment distribution over time"""
    instance_id = get_instance_id()
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Get hourly sentiment averages
    results = db.query(
        func.date_trunc('hour', MastodonPost.created_at).label('hour'),
        func.avg(PostSentiment.sentiment_score).label('avg_sentiment'),
        func.count().label('count')
    ).join(
        PostSentiment,
        (PostSentiment.post_id == MastodonPost.id) & 
        (PostSentiment.instance_id == MastodonPost.instance_id)
    ).filter(
        MastodonPost.instance_id == instance_id,
        MastodonPost.created_at >= since,
        MastodonPost.deleted_at == None
    ).group_by(
        func.date_trunc('hour', MastodonPost.created_at)
    ).order_by('hour').all()
    
    return [
        {
            "hour": r.hour.isoformat(),
            "avg_sentiment": r.avg_sentiment,
            "count": r.count
        }
        for r in results
    ]


# Hourly topics (AI-extracted from content)
@app.get("/api/topics/hourly")
async def get_hourly_topics(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db)
):
    """Get AI-extracted trending topics for the last N hours"""
    instance_id = get_instance_id()
    since = datetime.utcnow() - timedelta(hours=hours)
    
    topics = db.query(HourlyTopic).filter(
        HourlyTopic.instance_id == instance_id,
        HourlyTopic.hour_start >= since
    ).order_by(desc(HourlyTopic.hour_start), desc(HourlyTopic.post_count)).all()
    
    # Group by hour
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for topic in topics:
        hour_key = topic.hour_start.isoformat()
        if hour_key not in grouped:
            grouped[hour_key] = []
        grouped[hour_key].append({
            "topic": topic.topic,
            "summary": topic.summary,
            "post_count": topic.post_count,
            "avg_sentiment": topic.avg_sentiment,
            "sample_post_ids": topic.sample_post_ids if topic.sample_post_ids else []
        })
    
    return {"hourly_topics": grouped}


@app.get("/api/topics/current")
async def get_current_topics(db: Session = Depends(get_db)):
    """Get topics from the most recent hour"""
    instance_id = get_instance_id()
    
    # Get the most recent hour that has topics
    latest = db.query(HourlyTopic).filter(
        HourlyTopic.instance_id == instance_id
    ).order_by(desc(HourlyTopic.hour_start)).first()
    
    if not latest:
        return {"topics": [], "hour": None}
    
    current_hour = latest.hour_start
    
    # Get all topics for that hour
    topics = db.query(HourlyTopic).filter(
        HourlyTopic.instance_id == instance_id,
        HourlyTopic.hour_start == current_hour
    ).order_by(desc(HourlyTopic.post_count)).all()
    
    return {
        "hour": current_hour.isoformat(),
        "topics": [
            {
                "topic": t.topic,
                "summary": t.summary,
                "post_count": t.post_count,
                "avg_sentiment": t.avg_sentiment
            }
            for t in topics
        ]
    }
