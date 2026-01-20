"""
Forkalytics API
FastAPI backend for analytics endpoints
"""
import os
import sys
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from pydantic import BaseModel

# Add shared module to path
sys.path.insert(0, "/app")
from shared.database import get_db, init_db
from shared.models import MastodonPost, MastodonAccount, PostSentiment, DailySummary, HourlyStat, HourlyTopic

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


class StatsOverview(BaseModel):
    total_posts: int
    total_accounts: int
    posts_today: int
    posts_this_hour: int
    avg_engagement: float
    sentiment: SentimentOverview


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
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    
    total_posts = db.query(func.count(MastodonPost.id)).scalar()
    total_accounts = db.query(func.count(MastodonAccount.id)).scalar()
    
    posts_today = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.created_at >= today_start
    ).scalar()
    
    posts_this_hour = db.query(func.count(MastodonPost.id)).filter(
        MastodonPost.created_at >= hour_start
    ).scalar()
    
    avg_engagement = db.query(func.avg(MastodonPost.engagement_score)).scalar() or 0
    
    # Sentiment stats
    sentiment_query = db.query(
        func.avg(PostSentiment.sentiment_score).label("avg"),
        func.count().filter(PostSentiment.sentiment_label == "positive").label("positive"),
        func.count().filter(PostSentiment.sentiment_label == "negative").label("negative"),
        func.count().filter(PostSentiment.sentiment_label == "neutral").label("neutral"),
        func.count().label("total")
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


# Popular posts
@app.get("/api/posts/popular", response_model=List[PostResponse])
async def get_popular_posts(
    hours: int = Query(24, ge=1, le=168, description="Time window in hours"),
    limit: int = Query(20, ge=1, le=100),
    language: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get most popular posts by engagement score"""
    since = datetime.utcnow() - timedelta(hours=hours)
    
    query = db.query(MastodonPost).filter(
        MastodonPost.created_at >= since
    )
    
    if language:
        query = query.filter(MastodonPost.language == language)
    
    posts = query.order_by(desc(MastodonPost.engagement_score)).limit(limit).all()
    
    # Enrich with sentiment data
    result = []
    for post in posts:
        sentiment = db.query(PostSentiment).filter(
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
    query = db.query(MastodonPost)
    
    if language:
        query = query.filter(MastodonPost.language == language)
    
    posts = query.order_by(desc(MastodonPost.created_at)).limit(limit).all()
    
    result = []
    for post in posts:
        sentiment = db.query(PostSentiment).filter(
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
    since = datetime.utcnow() - timedelta(hours=hours)
    
    stats = db.query(HourlyStat).filter(
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
    since = datetime.utcnow() - timedelta(days=days)
    
    summaries = db.query(DailySummary).filter(
        DailySummary.date >= since
    ).order_by(desc(DailySummary.date)).all()
    
    return summaries


# Latest summary
@app.get("/api/summaries/latest", response_model=Optional[DailySummaryResponse])
async def get_latest_summary(db: Session = Depends(get_db)):
    """Get the most recent daily summary"""
    summary = db.query(DailySummary).order_by(desc(DailySummary.date)).first()
    
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
    """Get trending hashtags"""
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Get posts with hashtags
    posts = db.query(MastodonPost.hashtags).filter(
        MastodonPost.created_at >= since,
        MastodonPost.hashtags != None
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
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Get hourly sentiment averages
    results = db.query(
        func.date_trunc('hour', MastodonPost.created_at).label('hour'),
        func.avg(PostSentiment.sentiment_score).label('avg_sentiment'),
        func.count().label('count')
    ).join(PostSentiment).filter(
        MastodonPost.created_at >= since
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
    since = datetime.utcnow() - timedelta(hours=hours)
    
    topics = db.query(HourlyTopic).filter(
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
    # Get the most recent hour that has topics
    latest = db.query(HourlyTopic).order_by(desc(HourlyTopic.hour_start)).first()
    
    if not latest:
        return {"topics": [], "hour": None}
    
    current_hour = latest.hour_start
    
    # Get all topics for that hour
    topics = db.query(HourlyTopic).filter(
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
