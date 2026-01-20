"""
Shared database models for Forkalytics
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, BigInteger, Text, DateTime, 
    Boolean, Float, ForeignKey, Index, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class MastodonAccount(Base):
    """Mastodon user account information"""
    __tablename__ = "mastodon_accounts"

    id = Column(String(64), primary_key=True)  # Mastodon account ID
    username = Column(String(255), nullable=False)
    acct = Column(String(255), nullable=False)  # username@domain for remote
    display_name = Column(String(255))
    followers_count = Column(Integer, default=0)
    following_count = Column(Integer, default=0)
    statuses_count = Column(Integer, default=0)
    bot = Column(Boolean, default=False)
    avatar_url = Column(Text)
    
    # Tracking
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    posts = relationship("MastodonPost", back_populates="account")

    __table_args__ = (
        Index("ix_accounts_acct", "acct"),
        Index("ix_accounts_followers", "followers_count"),
    )


class MastodonPost(Base):
    """Mastodon status/post with engagement metrics"""
    __tablename__ = "mastodon_posts"

    id = Column(String(64), primary_key=True)  # Mastodon status ID
    uri = Column(Text, unique=True, nullable=False)  # Canonical URI
    url = Column(Text)  # Web URL
    
    # Content
    content = Column(Text)  # HTML content
    content_text = Column(Text)  # Plain text extracted
    spoiler_text = Column(String(500))
    language = Column(String(10))
    visibility = Column(String(20), default="public")
    sensitive = Column(Boolean, default=False)
    
    # Engagement metrics (updated over time)
    reblogs_count = Column(Integer, default=0)
    favourites_count = Column(Integer, default=0)
    replies_count = Column(Integer, default=0)
    
    # Computed engagement score
    engagement_score = Column(Float, default=0.0)
    
    # Threading
    in_reply_to_id = Column(String(64))
    in_reply_to_account_id = Column(String(64))
    
    # Reblog info (if this is a boost)
    reblog_of_id = Column(String(64), ForeignKey("mastodon_posts.id"), nullable=True)
    
    # Author
    account_id = Column(String(64), ForeignKey("mastodon_accounts.id"), nullable=False)
    account = relationship("MastodonAccount", back_populates="posts")
    
    # Media & metadata
    has_media = Column(Boolean, default=False)
    media_types = Column(JSON)  # List of media types
    hashtags = Column(JSON)  # List of hashtags
    mentions = Column(JSON)  # List of mentioned accounts
    
    # Timestamps
    created_at = Column(DateTime, nullable=False)
    edited_at = Column(DateTime)
    indexed_at = Column(DateTime, default=datetime.utcnow)
    
    # Analysis flags
    sentiment_analyzed = Column(Boolean, default=False)
    
    __table_args__ = (
        Index("ix_posts_created_at", "created_at"),
        Index("ix_posts_engagement", "engagement_score"),
        Index("ix_posts_language", "language"),
        Index("ix_posts_account", "account_id"),
        Index("ix_posts_sentiment_analyzed", "sentiment_analyzed"),
    )


class PostSentiment(Base):
    """Sentiment analysis results for posts"""
    __tablename__ = "post_sentiments"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    post_id = Column(String(64), ForeignKey("mastodon_posts.id"), nullable=False, unique=True)
    
    # Sentiment scores (-1 to 1)
    sentiment_score = Column(Float, nullable=False)  # Overall sentiment
    sentiment_label = Column(String(20))  # positive, negative, neutral
    
    # Emotion breakdown (optional detailed analysis)
    emotions = Column(JSON)  # {"joy": 0.8, "anger": 0.1, ...}
    
    # Topics/themes detected
    topics = Column(JSON)  # ["technology", "politics", ...]
    
    # Analysis metadata
    analyzed_at = Column(DateTime, default=datetime.utcnow)
    model_version = Column(String(50))  # OpenAI model used

    __table_args__ = (
        Index("ix_sentiment_score", "sentiment_score"),
        Index("ix_sentiment_label", "sentiment_label"),
    )


class DailySummary(Base):
    """AI-generated daily trend summaries"""
    __tablename__ = "daily_summaries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    date = Column(DateTime, nullable=False, unique=True)  # Date of summary
    
    # Statistics
    total_posts = Column(Integer, default=0)
    total_engagement = Column(BigInteger, default=0)
    unique_authors = Column(Integer, default=0)
    
    # Sentiment aggregates
    avg_sentiment = Column(Float)
    positive_count = Column(Integer, default=0)
    negative_count = Column(Integer, default=0)
    neutral_count = Column(Integer, default=0)
    
    # Top content
    top_hashtags = Column(JSON)  # [{"tag": "python", "count": 100}, ...]
    top_languages = Column(JSON)  # [{"lang": "en", "count": 500}, ...]
    
    # AI-generated content
    summary_text = Column(Text)  # Natural language summary
    trending_topics = Column(JSON)  # AI-identified trends
    notable_events = Column(JSON)  # Significant happenings
    
    # Metadata
    generated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_summary_date", "date"),
    )


class HourlyStat(Base):
    """Hourly aggregated statistics for charts"""
    __tablename__ = "hourly_stats"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    hour = Column(DateTime, nullable=False)  # Truncated to hour
    
    # Volume metrics
    post_count = Column(Integer, default=0)
    reblog_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    
    # Engagement
    total_engagement = Column(BigInteger, default=0)
    avg_engagement = Column(Float, default=0.0)
    
    # Sentiment
    avg_sentiment = Column(Float)
    
    # Top hashtag this hour
    top_hashtag = Column(String(255))
    
    __table_args__ = (
        Index("ix_hourly_hour", "hour", unique=True),
    )


class HourlyTopic(Base):
    """AI-extracted trending topics per hour based on post content"""
    __tablename__ = "hourly_topics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    hour_start = Column(DateTime, nullable=False)  # Hour this topic was extracted from
    
    # Topic info
    topic = Column(String(255), nullable=False)  # Short topic label
    summary = Column(Text)  # Brief description of what people are saying
    post_count = Column(Integer, default=0)  # Number of posts about this topic
    avg_sentiment = Column(Float)  # Average sentiment of posts about this topic
    sample_post_ids = Column(JSON)  # Sample post IDs for reference
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_hourly_topics_hour", "hour_start"),
        Index("ix_hourly_topics_topic", "topic"),
    )
