# Data Modeling Improvements

This document describes the improved data model implemented in Forkalytics based on analytics best practices.

## Overview

The enhanced data model provides:
- **Multi-instance support** for monitoring multiple Mastodon instances
- **Time-series engagement tracking** for velocity and trending analysis
- **Edit history tracking** with full content versioning
- **Normalized hashtags and mentions** for efficient querying
- **Soft deletes** to preserve analytics integrity
- **Audit trails** for AI-generated content reproducibility
- **Event logging** for replay and debugging

## Key Improvements

### 1. Instance Dimension

**Problem**: Original schema couldn't distinguish between posts from different instances.

**Solution**: New `instances` table with instance_id as part of composite primary keys.

```sql
CREATE TABLE instances (
    id INTEGER PRIMARY KEY,
    base_url VARCHAR(255) UNIQUE NOT NULL,
    stream_type VARCHAR(50),
    created_at TIMESTAMP,
    last_seen_at TIMESTAMP
);
```

All main tables now use `(id, instance_id)` as composite primary keys.

### 2. Metric Snapshots

**Problem**: Only current engagement metrics were stored, making velocity analysis impossible.

**Solution**: `post_metric_snapshots` table captures engagement at multiple points in time.

```sql
CREATE TABLE post_metric_snapshots (
    id BIGSERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    post_id VARCHAR(64) NOT NULL,
    captured_at TIMESTAMP NOT NULL,
    replies_count INTEGER,
    reblogs_count INTEGER,
    favourites_count INTEGER,
    engagement_score FLOAT
);
```

**Usage**:
- Calculate engagement velocity: "This post gained 50 favorites in the first hour"
- Identify trending content: "Engagement increased 300% in the last 2 hours"
- Time-series charts of post performance

### 3. Edit History Tracking

**Problem**: Content changes from edits were lost.

**Solution**: `post_versions` table maintains full history using SCD Type 2 pattern.

```sql
CREATE TABLE post_versions (
    id BIGSERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    post_id VARCHAR(64) NOT NULL,
    version_seq INTEGER NOT NULL,
    valid_from TIMESTAMP NOT NULL,
    content_html TEXT,
    content_text TEXT,
    edited_at TIMESTAMP
);
```

**Usage**:
- Track how content changed over time
- Analyze sentiment changes after edits
- Debug issues with specific post versions

### 4. Normalized Hashtags

**Problem**: JSON arrays made hashtag queries expensive and limited trending analysis.

**Solution**: `hashtags` dimension table with `post_hashtags` bridge table.

```sql
CREATE TABLE hashtags (
    id BIGSERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    UNIQUE(instance_id, name)
);

CREATE TABLE post_hashtags (
    id BIGSERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    post_id VARCHAR(64) NOT NULL,
    hashtag_id BIGINT NOT NULL,
    UNIQUE(instance_id, post_id, hashtag_id)
);
```

**Benefits**:
- Fast hashtag queries with proper indexes
- Efficient "trending" calculations
- Co-occurrence analysis
- Unique user counts per tag

### 5. Hashtag Hourly Stats

**Problem**: Couldn't efficiently calculate true "trending" (velocity vs volume).

**Solution**: `hashtag_hourly_stats` pre-aggregated table for trending analysis.

```sql
CREATE TABLE hashtag_hourly_stats (
    id BIGSERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    hashtag_id BIGINT NOT NULL,
    hour_ts TIMESTAMP NOT NULL,
    posts_using_tag INTEGER,
    unique_accounts_using_tag INTEGER,
    total_engagement BIGINT,
    UNIQUE(instance_id, hashtag_id, hour_ts)
);
```

**Trending Algorithm**:
```python
# Spike detection: current hour vs baseline
current_hour_posts = get_hashtag_usage(tag, hour=now)
baseline_posts = get_hashtag_usage(tag, hour=now-24h)
trending_score = (current_hour_posts - baseline_posts) / baseline_posts

# Time decay: favor recent activity
trending_score *= exp(-hours_since / decay_factor)
```

### 6. Soft Deletes

**Problem**: Hard deletes broke FK relationships and lost historical data.

**Solution**: `deleted_at` timestamp field for tombstoning.

```sql
ALTER TABLE mastodon_posts ADD COLUMN deleted_at TIMESTAMP;
```

**Behavior**:
- Post deletion sets `deleted_at` timestamp
- Optionally redact `content` fields for privacy
- Keep row to maintain relationships and aggregates
- All queries filter `WHERE deleted_at IS NULL`

### 7. Audit Metadata

**Problem**: Couldn't reproduce AI-generated results or debug issues.

**Solution**: Comprehensive audit fields on AI tables.

**PostSentiment**:
```sql
ALTER TABLE post_sentiments ADD COLUMN model VARCHAR(100);
ALTER TABLE post_sentiments ADD COLUMN prompt_version VARCHAR(50);
ALTER TABLE post_sentiments ADD COLUMN input_hash VARCHAR(64);
ALTER TABLE post_sentiments ADD COLUMN analyzed_at TIMESTAMP;
ALTER TABLE post_sentiments ADD COLUMN status_version_seq INTEGER;
```

**DailySummary**:
```sql
ALTER TABLE daily_summaries ADD COLUMN window_start TIMESTAMP;
ALTER TABLE daily_summaries ADD COLUMN window_end TIMESTAMP;
ALTER TABLE daily_summaries ADD COLUMN model VARCHAR(100);
ALTER TABLE daily_summaries ADD COLUMN prompt_version VARCHAR(50);
ALTER TABLE daily_summaries ADD COLUMN input_stats_json JSON;
ALTER TABLE daily_summaries ADD COLUMN input_hash VARCHAR(64);
```

**Benefits**:
- Reproduce results: "What data generated this summary?"
- A/B test prompts: "Does prompt v2 improve sentiment accuracy?"
- Avoid re-analysis: "Already analyzed this content with same model"
- Debug issues: "Which model version produced this result?"

### 8. Stream Event Log

**Problem**: Couldn't replay events or debug missing data.

**Solution**: `stream_events` table logs all incoming events.

```sql
CREATE TABLE stream_events (
    id BIGSERIAL PRIMARY KEY,
    instance_id INTEGER NOT NULL,
    received_at TIMESTAMP NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload JSON,
    payload_status_id VARCHAR(64),
    processed_at TIMESTAMP,
    process_error TEXT
);
```

**Usage**:
- Replay events to reprocess data
- Debug: "Why is post X missing?"
- Backfill derived tables from raw events
- Monitor processing errors

**Retention**: Consider 7-30 day retention policy to manage size.

## Migration Guide

### For New Installations

No migration needed! Just deploy and the schema will be created automatically.

### For Existing Installations

#### Option 1: Fresh Start (Recommended for Development)

1. Stop all services: `docker compose down`
2. Drop database: `docker volume rm forkalytics_postgres_data`
3. Start services: `docker compose up -d`

#### Option 2: Preserve Data (Production)

**Important**: The new schema uses composite primary keys which requires careful migration.

1. **Backup your data**:
   ```bash
   docker exec forkalytics-db pg_dump -U forkalytics forkalytics > backup.sql
   ```

2. **Run migration helper**:
   ```bash
   docker exec forkalytics-worker python /app/shared/migration_helper.py
   ```

3. **Manual steps** (if migration helper can't handle composite keys):
   
   a. Export existing data:
   ```sql
   COPY (SELECT * FROM mastodon_posts) TO '/tmp/posts.csv' CSV HEADER;
   COPY (SELECT * FROM mastodon_accounts) TO '/tmp/accounts.csv' CSV HEADER;
   ```
   
   b. Let Alembic/SQLAlchemy recreate tables with new schema
   
   c. Re-import with instance_id populated:
   ```sql
   INSERT INTO mastodon_posts (id, instance_id, ...)
   SELECT id, 1 as instance_id, ... FROM old_posts_backup;
   ```

4. **Backfill normalized tables**:
   ```bash
   docker exec forkalytics-worker python -c "
   from shared.migration_helper import backfill_normalized_hashtags
   backfill_normalized_hashtags(instance_id=1)
   "
   ```

## Querying the New Schema

### Example: Engagement Velocity

```python
# Get posts that gained >50 favorites in the last hour
from datetime import datetime, timedelta

one_hour_ago = datetime.utcnow() - timedelta(hours=1)

posts_with_velocity = db.query(
    MastodonPost.id,
    MastodonPost.content_text,
    (
        func.coalesce(
            select([PostMetricSnapshot.favourites_count])
            .where(PostMetricSnapshot.post_id == MastodonPost.id)
            .where(PostMetricSnapshot.captured_at >= one_hour_ago)
            .order_by(PostMetricSnapshot.captured_at.desc())
            .limit(1)
            .scalar_subquery(),
            0
        ) - 
        func.coalesce(
            select([PostMetricSnapshot.favourites_count])
            .where(PostMetricSnapshot.post_id == MastodonPost.id)
            .where(PostMetricSnapshot.captured_at < one_hour_ago)
            .order_by(PostMetricSnapshot.captured_at.desc())
            .limit(1)
            .scalar_subquery(),
            0
        )
    ).label('favorites_gained')
).having(
    text('favorites_gained > 50')
).all()
```

### Example: Trending Hashtags

```python
# Hashtags with highest growth in last 4 hours vs previous 20 hours
current_window = datetime.utcnow() - timedelta(hours=4)
baseline_start = datetime.utcnow() - timedelta(hours=24)
baseline_end = datetime.utcnow() - timedelta(hours=4)

trending = db.query(
    Hashtag.name,
    func.count(PostHashtag.id).filter(
        MastodonPost.created_at >= current_window
    ).label('recent_count'),
    func.count(PostHashtag.id).filter(
        MastodonPost.created_at.between(baseline_start, baseline_end)
    ).label('baseline_count')
).join(
    PostHashtag, Hashtag.id == PostHashtag.hashtag_id
).join(
    MastodonPost, PostHashtag.post_id == MastodonPost.id
).group_by(
    Hashtag.name
).having(
    text('recent_count > baseline_count * 1.5')  # 50% growth
).order_by(
    desc('recent_count')
).all()
```

### Example: Edit Analysis

```python
# Find posts that changed sentiment after editing
posts_with_sentiment_change = db.query(
    MastodonPost.id,
    PostVersion.version_seq,
    PostSentiment.sentiment_label
).join(
    PostVersion, MastodonPost.id == PostVersion.post_id
).join(
    PostSentiment, (
        (PostSentiment.post_id == MastodonPost.id) &
        (PostSentiment.status_version_seq == PostVersion.version_seq)
    )
).filter(
    PostVersion.version_seq > 1
).all()
```

## Performance Considerations

### Indexes

All critical query paths are indexed:
- Composite primary keys: `(id, instance_id)`
- Time-based queries: `(instance_id, created_at)`
- Hashtag lookups: `(instance_id, hashtag_id, post_id)`
- Engagement queries: `engagement_score DESC`

### Partition Tables (Future Enhancement)

For very large datasets, consider partitioning:
```sql
-- Partition posts by month
CREATE TABLE mastodon_posts_2024_01 
PARTITION OF mastodon_posts 
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

### Metric Snapshot Retention

Balance detail vs storage:
- Keep all snapshots for last 7 days
- Hourly samples for 30 days
- Daily samples forever

```python
# Cleanup old high-frequency snapshots
db.execute("""
DELETE FROM post_metric_snapshots
WHERE captured_at < NOW() - INTERVAL '30 days'
AND post_id NOT IN (
    SELECT DISTINCT post_id 
    FROM post_metric_snapshots 
    WHERE captured_at >= NOW() - INTERVAL '30 days'
    AND EXTRACT(minute FROM captured_at) = 0  -- Keep hourly
)
""")
```

## Backward Compatibility

The implementation maintains backward compatibility:

1. **JSON fields preserved**: `hashtags` and `mentions` columns still populated
2. **API unchanged**: All existing endpoints work without modification
3. **Gradual migration**: Normalized tables populated alongside JSON
4. **Fallback queries**: API tries normalized tables first, falls back to JSON

## Future Enhancements

### 1. Account Relationships
```sql
CREATE TABLE account_follows (
    follower_account_id VARCHAR(64),
    followee_account_id VARCHAR(64),
    followed_at TIMESTAMP,
    PRIMARY KEY (follower_account_id, followee_account_id)
);
```

### 2. Media Attachments
```sql
CREATE TABLE media_attachments (
    id VARCHAR(64) PRIMARY KEY,
    post_id VARCHAR(64),
    type VARCHAR(50),
    url TEXT,
    preview_url TEXT,
    meta_json JSON
);
```

### 3. Conversation Threads
```sql
CREATE TABLE conversation_threads (
    id BIGSERIAL PRIMARY KEY,
    root_post_id VARCHAR(64),
    depth INTEGER,
    reply_count INTEGER
);
```

### 4. Scheduled Aggregates
Pre-compute expensive queries:
```sql
CREATE MATERIALIZED VIEW hourly_domain_stats AS
SELECT 
    instance_id,
    DATE_TRUNC('hour', created_at) as hour,
    domain,
    COUNT(*) as post_count,
    SUM(engagement_score) as total_engagement
FROM mastodon_posts
JOIN mastodon_accounts ON ...
GROUP BY instance_id, hour, domain;

-- Refresh hourly
REFRESH MATERIALIZED VIEW CONCURRENTLY hourly_domain_stats;
```

## Questions?

Refer to:
- Code: `shared/models.py` - Complete schema definitions
- Migration: `shared/migration_helper.py` - Migration utilities
- Examples: Worker and API code for query patterns
