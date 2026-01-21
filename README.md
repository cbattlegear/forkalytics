# Forkalytics

A self-hosted Mastodon instance analytics service that tracks public posts, analyzes sentiment, and generates daily AI-powered trend summaries.

## Features

- **Real-time Streaming**: Connects to Mastodon's streaming API to capture all public posts
- **Engagement Tracking**: Tracks reblogs, favorites, and replies with weighted engagement scoring
- **Sentiment Analysis**: Uses OpenAI to analyze post sentiment (positive/negative/neutral)
- **Daily Summaries**: AI-generated daily summaries of trending topics and notable events
- **Analytics Dashboard**: React-based dashboard with charts and visualizations
- **Docker Compose**: Fully containerized for easy self-hosting

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Mastodon   │────▶│   Worker    │────▶│  PostgreSQL │
│  Instance   │     │ (Streamer)  │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
┌─────────────┐     ┌─────────────┐            │
│   OpenAI    │◀────│  Scheduler  │────────────┤
│     API     │     │ (Analytics) │            │
└─────────────┘     └─────────────┘            │
                                               │
                    ┌─────────────┐     ┌──────┴──────┐
                    │     Web     │◀────│     API     │
                    │ (Dashboard) │     │  (FastAPI)  │
                    └─────────────┘     └─────────────┘
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A Mastodon account on the instance you want to monitor
- OpenAI API key (for sentiment analysis and summaries)

### 1. Clone and Configure

```bash
cd forkalytics

# Copy the example environment file
cp .env.example .env
```

### 2. Get a Mastodon Access Token

1. Log into your Mastodon instance
2. Go to **Preferences** → **Development** → **New Application**
3. Name: `Forkalytics`
4. Scopes: Select `read:statuses`
5. Submit and copy the **Access Token**

### 3. Configure Environment

Edit `.env` with your settings:

```env
# Database (you can change the password)
POSTGRES_USER=forkalytics
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_DB=forkalytics

# Mastodon
MASTODON_INSTANCE=https://mastodon.social
MASTODON_ACCESS_TOKEN=your_token_here

# OpenAI
OPENAI_API_KEY=sk-your-openai-key
```

### 4. Start the Services

```bash
docker compose up -d
```

This starts:
- **PostgreSQL** (port 5432) - Database
- **Redis** (port 6379) - Caching/queues
- **API** (port 8000) - FastAPI backend
- **Worker** - Mastodon streaming client
- **Scheduler** - Sentiment analysis & summaries
- **Web** (port 3000) - Dashboard UI

### 5. Access the Dashboard

Open http://localhost:3000 in your browser.

## Services

### Worker (Streamer)

Connects to Mastodon's WebSocket streaming API and captures:
- New posts (`update` events)
- Edited posts (`status.update` events)
- Deleted posts (`delete` events)

Each post is parsed and stored with:
- Author information
- Content (HTML and plain text)
- Engagement metrics
- Hashtags and mentions
- Media attachments info

### Scheduler (Analytics)

Runs periodic jobs:
- **Every 5 minutes**: Sentiment analysis on new posts
- **Every hour**: Aggregates hourly statistics
- **Daily at 1 AM UTC**: Generates AI daily summary

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats` | Overview statistics |
| `GET /api/posts/popular` | Popular posts by engagement |
| `GET /api/posts/recent` | Most recent posts |
| `GET /api/stats/hourly` | Hourly activity statistics |
| `GET /api/hashtags/trending` | Trending hashtags |
| `GET /api/summaries` | Daily AI summaries |
| `GET /api/summaries/latest` | Most recent summary |
| `GET /api/sentiment/distribution` | Sentiment over time |

## Configuration Options

### Stream Types

Change `STREAM_TYPE` in docker-compose.yml worker environment:

- `public` - Federated timeline (all known posts)
- `public:local` - Local timeline only (this instance)
- `public:remote` - Remote posts only

### OpenAI Model

Edit `OPENAI_MODEL` in `worker/scheduler.py` (default: `gpt-4o-mini`)

### Batch Size

Edit `SENTIMENT_BATCH_SIZE` in `worker/scheduler.py` (default: 50)

## Development

### Local Development (without Docker)

```bash
# Start database and redis
docker compose up -d postgres redis

# API
cd api
pip install -r requirements.txt
uvicorn main:app --reload

# Worker
cd worker
pip install -r requirements.txt
python streamer.py

# Scheduler
cd worker
python scheduler.py

# Web
cd web
npm install
npm run dev
```

### Database Schema

See `shared/models.py` for SQLAlchemy models:
- `Instance` - Mastodon instance tracking (multi-instance support)
- `MastodonAccount` - User accounts (with instance_id)
- `MastodonPost` - Posts/statuses (with instance_id, soft deletes, edit tracking)
- `PostMetricSnapshot` - Time-series engagement metrics
- `PostVersion` - Edit history for posts
- `Hashtag` - Normalized hashtag dimension
- `PostHashtag` - Post-to-hashtag associations
- `PostMention` - Post-to-account mention associations
- `HashtagHourlyStat` - Aggregated hashtag statistics for trending analysis
- `PostSentiment` - Sentiment analysis results (with audit metadata)
- `DailySummary` - AI daily summaries (with audit metadata)
- `HourlyStat` - Aggregated hourly stats (recomputable)
- `HourlyTopic` - AI-extracted trending topics
- `StreamEvent` - Raw streaming events log (for replay/debugging)

**📚 For detailed information about the data model, see [DATA_MODELING.md](DATA_MODELING.md)**

## Monitoring Multiple Instances

To monitor multiple Mastodon instances, you can:

1. Run multiple worker containers with different `MASTODON_INSTANCE` and `MASTODON_ACCESS_TOKEN` values
2. Use `public:remote` stream type from a large instance to see federated content
3. Each instance is tracked separately with a unique `instance_id` in the database

## Upgrading from Previous Versions

The database schema has been significantly enhanced to support better analytics. See [DATA_MODELING.md](DATA_MODELING.md) for details.

### For New Installations
No special steps needed - just follow the Quick Start guide above.

### For Existing Installations

**Option 1: Fresh Start (Recommended for Development)**
```bash
docker compose down
docker volume rm forkalytics_postgres_data
docker compose up -d
```

**Option 2: Preserve Data (Production)**
```bash
# Backup first!
docker exec forkalytics-db pg_dump -U forkalytics forkalytics > backup.sql

# Run migration helper
docker exec forkalytics-worker python /app/shared/migration_helper.py
```

**Note**: The new schema uses composite primary keys. For production migrations with significant data, please review [DATA_MODELING.md](DATA_MODELING.md) for detailed migration steps.

## Troubleshooting

### Worker won't connect

- Verify your access token has `read:statuses` scope
- Check the instance URL is correct (include `https://`)
- Some instances require authorized-fetch; ensure you have a valid token

### No sentiment analysis

- Verify `OPENAI_API_KEY` is set correctly
- Check scheduler logs: `docker compose logs scheduler`

### Empty dashboard

- Data takes time to accumulate
- Check worker logs: `docker compose logs worker`
- Verify posts are being stored: check the API at http://localhost:8000/api/stats

## License

MIT
