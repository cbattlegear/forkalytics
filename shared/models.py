"""
Shared database models for Forkalytics
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, BigInteger, Text, DateTime, 
    Boolean, Float, ForeignKey, Index, JSON, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Instance(Base):
    """Mastodon instance tracking for multi-instance support"""
    __tablename__ = "instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    base_url = Column(String(255), unique=True, nullable=False)  # e.g., "https://mastodon.social"
    stream_type = Column(String(50))  # public, public:local, public:remote
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    accounts = relationship("MastodonAccount", back_populates="instance")
    posts = relationship("MastodonPost", back_populates="instance")

    __table_args__ = (
        Index("ix_instances_base_url", "base_url"),
    )


class MastodonAccount(Base):
    """Mastodon user account information"""
    __tablename__ = "mastodon_accounts"

    id = Column(String(64), primary_key=True)  # Composite key with instance_id
    instance_id = Column(Integer, ForeignKey("instances.id"), primary_key=True, nullable=False)
    username = Column(String(255), nullable=False)
    acct = Column(String(255), nullable=False)  # username@domain for remote
    display_name = Column(String(255))
    followers_count = Column(Integer, default=0)
    following_count = Column(Integer, default=0)
    statuses_count = Column(Integer, default=0)
    bot = Column(Boolean, default=False)
    avatar_url = Column(Text)
    
    # Additional fields for better analytics
    is_local = Column(Boolean)  # Is this a local account on the instance
    domain = Column(String(255))  # Domain extracted from acct
    
    # Tracking
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    instance = relationship("Instance", back_populates="accounts")
    posts = relationship("MastodonPost", back_populates="account", foreign_keys="MastodonPost.account_id")

    __table_args__ = (
        Index("ix_accounts_instance_acct", "instance_id", "acct"),
        Index("ix_accounts_followers", "followers_count"),
        Index("ix_accounts_domain", "domain"),
    )


class MastodonPost(Base):
    """Mastodon status/post with engagement metrics"""
    __tablename__ = "mastodon_posts"

    id = Column(String(64), primary_key=True)  # Composite key with instance_id
    instance_id = Column(Integer, ForeignKey("instances.id"), primary_key=True, nullable=False)
    uri = Column(Text, nullable=False)  # Canonical URI
    url = Column(Text)  # Web URL
    
    # Content
    content = Column(Text)  # HTML content
    content_text = Column(Text)  # Plain text extracted
    spoiler_text = Column(String(500))
    language = Column(String(10))
    visibility = Column(String(20), default="public")
    sensitive = Column(Boolean, default=False)
    
    # Engagement metrics (updated over time - latest known values)
    reblogs_count = Column(Integer, default=0)
    favourites_count = Column(Integer, default=0)
    replies_count = Column(Integer, default=0)
    
    # Computed engagement score
    engagement_score = Column(Float, default=0.0)
    
    # Threading
    in_reply_to_id = Column(String(64))
    in_reply_to_account_id = Column(String(64))
    
    # Reblog info (if this is a boost)
    reblog_of_id = Column(String(64))
    
    # Author - composite foreign key
    account_id = Column(String(64), nullable=False)
    account_instance_id = Column(Integer, nullable=False)
    
    # Media & metadata (kept for backward compatibility, but should migrate to normalized tables)
    has_media = Column(Boolean, default=False)
    media_types = Column(JSON)  # List of media types
    hashtags = Column(JSON)  # List of hashtags (deprecated - use post_hashtags table)
    mentions = Column(JSON)  # List of mentioned accounts (deprecated - use post_mentions table)
    
    # Timestamps
    created_at = Column(DateTime, nullable=False)
    edited_at = Column(DateTime)  # Last edit timestamp from Mastodon
    indexed_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime)  # Soft delete timestamp
    
    # Analysis flags
    sentiment_analyzed = Column(Boolean, default=False)
    
    # Relationships
    instance = relationship("Instance", back_populates="posts")
    account = relationship("MastodonAccount", back_populates="posts", 
                         foreign_keys=[account_id, account_instance_id])
    metric_snapshots = relationship("PostMetricSnapshot", back_populates="post")
    versions = relationship("PostVersion", back_populates="post", order_by="PostVersion.version_seq")
    hashtag_associations = relationship("PostHashtag", back_populates="post")
    mention_associations = relationship("PostMention", back_populates="post")

    __table_args__ = (
        ForeignKey(['account_id', 'account_instance_id'], 
                  ['mastodon_accounts.id', 'mastodon_accounts.instance_id']),
        Index("ix_posts_instance_created", "instance_id", "created_at"),
        Index("ix_posts_engagement", "engagement_score"),
        Index("ix_posts_language", "language"),
        Index("ix_posts_account", "account_id", "account_instance_id"),
        Index("ix_posts_sentiment_analyzed", "sentiment_analyzed"),
        Index("ix_posts_deleted_at", "deleted_at"),
        UniqueConstraint("instance_id", "uri", name="uq_posts_instance_uri"),
    )


class PostMetricSnapshot(Base):
    """Time-series snapshots of post engagement metrics"""
    __tablename__ = "post_metric_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    post_id = Column(String(64), nullable=False)
    
    # Snapshot timestamp
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Engagement metrics at this point in time
    replies_count = Column(Integer, default=0)
    reblogs_count = Column(Integer, default=0)
    favourites_count = Column(Integer, default=0)
    engagement_score = Column(Float, default=0.0)
    
    # Relationships
    post = relationship("MastodonPost", back_populates="metric_snapshots",
                       foreign_keys=[post_id, instance_id])

    __table_args__ = (
        ForeignKey(['post_id', 'instance_id'], 
                  ['mastodon_posts.id', 'mastodon_posts.instance_id']),
        Index("ix_snapshots_instance_captured", "instance_id", "captured_at"),
        Index("ix_snapshots_post_captured", "instance_id", "post_id", "captured_at"),
    )


class PostVersion(Base):
    """Version history for edited posts (SCD Type 2)"""
    __tablename__ = "post_versions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    post_id = Column(String(64), nullable=False)
    
    # Version tracking
    version_seq = Column(Integer, nullable=False)  # 1, 2, 3...
    valid_from = Column(DateTime, nullable=False)  # When this version became active
    
    # Content snapshot
    content_html = Column(Text)
    content_text = Column(Text)
    spoiler_text = Column(String(500))
    sensitive = Column(Boolean, default=False)
    
    # Metadata
    hashtags_json = Column(JSON)  # Hashtags at this version
    mentions_json = Column(JSON)  # Mentions at this version
    edited_at = Column(DateTime)  # Mastodon's edited_at timestamp
    
    # Relationships
    post = relationship("MastodonPost", back_populates="versions",
                       foreign_keys=[post_id, instance_id])

    __table_args__ = (
        ForeignKey(['post_id', 'instance_id'], 
                  ['mastodon_posts.id', 'mastodon_posts.instance_id']),
        Index("ix_versions_post", "instance_id", "post_id", "version_seq"),
        UniqueConstraint("instance_id", "post_id", "version_seq", 
                        name="uq_versions_post_seq"),
    )


class Hashtag(Base):
    """Normalized hashtag dimension"""
    __tablename__ = "hashtags"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    name = Column(String(255), nullable=False)  # Lowercased normalized name
    
    # Tracking
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    post_associations = relationship("PostHashtag", back_populates="hashtag")
    hourly_stats = relationship("HashtagHourlyStat", back_populates="hashtag")

    __table_args__ = (
        UniqueConstraint("instance_id", "name", name="uq_hashtags_instance_name"),
        Index("ix_hashtags_name", "name"),
    )


class PostHashtag(Base):
    """Many-to-many bridge between posts and hashtags"""
    __tablename__ = "post_hashtags"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    post_id = Column(String(64), nullable=False)
    hashtag_id = Column(BigInteger, ForeignKey("hashtags.id"), nullable=False)
    
    # Relationships
    post = relationship("MastodonPost", back_populates="hashtag_associations",
                       foreign_keys=[post_id, instance_id])
    hashtag = relationship("Hashtag", back_populates="post_associations")

    __table_args__ = (
        ForeignKey(['post_id', 'instance_id'], 
                  ['mastodon_posts.id', 'mastodon_posts.instance_id']),
        UniqueConstraint("instance_id", "post_id", "hashtag_id", 
                        name="uq_post_hashtags_post_tag"),
        Index("ix_post_hashtags_hashtag", "instance_id", "hashtag_id", "post_id"),
        Index("ix_post_hashtags_post", "instance_id", "post_id"),
    )


class PostMention(Base):
    """Many-to-many bridge for post mentions"""
    __tablename__ = "post_mentions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    post_id = Column(String(64), nullable=False)
    mentioned_account_id = Column(String(64), nullable=False)
    mentioned_account_instance_id = Column(Integer, nullable=False)
    
    # From Mastodon mention object
    mentioned_acct = Column(String(255))  # Full acct (username@domain)
    mentioned_username = Column(String(255))
    
    # Relationships
    post = relationship("MastodonPost", back_populates="mention_associations",
                       foreign_keys=[post_id, instance_id])

    __table_args__ = (
        ForeignKey(['post_id', 'instance_id'], 
                  ['mastodon_posts.id', 'mastodon_posts.instance_id']),
        ForeignKey(['mentioned_account_id', 'mentioned_account_instance_id'],
                  ['mastodon_accounts.id', 'mastodon_accounts.instance_id']),
        UniqueConstraint("instance_id", "post_id", "mentioned_account_id", 
                        name="uq_post_mentions_post_account"),
        Index("ix_post_mentions_mentioned", "mentioned_account_id", "mentioned_account_instance_id"),
    )


class HashtagHourlyStat(Base):
    """Hourly aggregated statistics per hashtag for trending analysis"""
    __tablename__ = "hashtag_hourly_stats"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    hashtag_id = Column(BigInteger, ForeignKey("hashtags.id"), nullable=False)
    hour_ts = Column(DateTime, nullable=False)  # Truncated to hour
    
    # Aggregates
    posts_using_tag = Column(Integer, default=0)
    unique_accounts_using_tag = Column(Integer, default=0)
    total_engagement = Column(BigInteger, default=0)  # Sum of engagement scores
    
    # Relationships
    hashtag = relationship("Hashtag", back_populates="hourly_stats")

    __table_args__ = (
        UniqueConstraint("instance_id", "hashtag_id", "hour_ts", 
                        name="uq_hashtag_hourly_instance_tag_hour"),
        Index("ix_hashtag_hourly_hour", "instance_id", "hour_ts"),
        Index("ix_hashtag_hourly_tag", "instance_id", "hashtag_id", "hour_ts"),
    )


class StreamEvent(Base):
    """Raw streaming events log (Bronze layer) for replay and debugging"""
    __tablename__ = "stream_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    
    # Event metadata
    received_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    event_type = Column(String(50), nullable=False)  # update, status.update, delete
    
    # Payload
    payload = Column(JSON)  # Full raw JSON payload
    payload_status_id = Column(String(64))  # Extracted for indexing
    payload_account_id = Column(String(64))  # Extracted for indexing
    
    # Processing status
    processed_at = Column(DateTime)
    process_error = Column(Text)
    
    __table_args__ = (
        Index("ix_stream_events_instance_received", "instance_id", "received_at"),
        Index("ix_stream_events_status_id", "payload_status_id"),
        Index("ix_stream_events_processed", "processed_at"),
    )


class PostSentiment(Base):
    """Sentiment analysis results for posts"""
    __tablename__ = "post_sentiments"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    post_id = Column(String(64), nullable=False)
    
    # Sentiment scores (-1 to 1)
    sentiment_score = Column(Float, nullable=False)  # Overall sentiment
    sentiment_label = Column(String(20))  # positive, negative, neutral
    
    # Emotion breakdown (optional detailed analysis)
    emotions = Column(JSON)  # {"joy": 0.8, "anger": 0.1, ...}
    
    # Topics/themes detected
    topics = Column(JSON)  # ["technology", "politics", ...]
    
    # Analysis metadata (audit fields)
    analyzed_at = Column(DateTime, default=datetime.utcnow)
    model = Column(String(100))  # e.g., "vader-3.3.2", "gpt-4o-mini"
    model_version = Column(String(50))  # Deprecated: use model field
    prompt_version = Column(String(50))  # Version of prompt template used
    input_hash = Column(String(64))  # Hash of analyzed text for deduplication
    status_version_seq = Column(Integer)  # Link to post version if versioning enabled
    
    # Error tracking
    error = Column(Text)
    attempt_count = Column(Integer, default=1)
    next_retry_at = Column(DateTime)

    __table_args__ = (
        ForeignKey(['post_id', 'instance_id'], 
                  ['mastodon_posts.id', 'mastodon_posts.instance_id']),
        UniqueConstraint("instance_id", "post_id", name="uq_sentiment_instance_post"),
        Index("ix_sentiment_score", "sentiment_score"),
        Index("ix_sentiment_label", "sentiment_label"),
        Index("ix_sentiment_input_hash", "input_hash"),
    )


class DailySummary(Base):
    """AI-generated daily trend summaries"""
    __tablename__ = "daily_summaries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
    date = Column(DateTime, nullable=False)  # Date of summary
    
    # Time window (audit fields)
    window_start = Column(DateTime)  # UTC start of analysis window
    window_end = Column(DateTime)  # UTC end of analysis window
    
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
    
    # AI model metadata (audit fields)
    model = Column(String(100))  # e.g., "gpt-4o-mini"
    prompt_version = Column(String(50))  # Version of prompt template
    input_stats_json = Column(JSON)  # Stats used to generate summary
    input_hash = Column(String(64))  # Hash of input data
    
    # Metadata
    generated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("instance_id", "date", name="uq_summary_instance_date"),
        Index("ix_summary_date", "date"),
        Index("ix_summary_instance_date", "instance_id", "date"),
    )


class HourlyStat(Base):
    """Hourly aggregated statistics for charts (recomputable from base tables)"""
    __tablename__ = "hourly_stats"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
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
    
    # Rollup metadata (audit fields)
    definition_version = Column(String(50))  # Version of aggregation logic
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("instance_id", "hour", name="uq_hourly_instance_hour"),
        Index("ix_hourly_hour", "hour"),
        Index("ix_hourly_instance_hour", "instance_id", "hour"),
    )


class HourlyTopic(Base):
    """AI-extracted trending topics per hour based on post content"""
    __tablename__ = "hourly_topics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False)
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
        Index("ix_hourly_topics_instance_hour", "instance_id", "hour_start"),
        Index("ix_hourly_topics_topic", "topic"),
    )
