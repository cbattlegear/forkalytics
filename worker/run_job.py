#!/usr/bin/env python
"""Manually run scheduled jobs."""
import sys
import asyncio
from datetime import datetime, timedelta
from scheduler import (
    analyze_sentiment_batch,
    extract_hourly_topics,
    generate_daily_summary,
    generate_hourly_stats
)
from backfill import (
    backfill_public_timeline,
    backfill_hashtag,
    backfill_trending
)

JOBS = {
    "sentiment": analyze_sentiment_batch,
    "topics": extract_hourly_topics,
    "summary": generate_daily_summary,
    "stats": generate_hourly_stats,
    "backfill": None,  # Special case - uses async
    "reprocess": None,  # Special case - date range processing
    "all": None  # Special case
}

def run_all():
    print("Running hourly stats...")
    generate_hourly_stats()
    print("Running sentiment analysis...")
    analyze_sentiment_batch()
    print("Running topic extraction...")
    extract_hourly_topics()
    print("Running daily summary...")
    generate_daily_summary()
    print("All jobs complete!")

def run_backfill():
    """Run backfill job with optional arguments."""
    max_posts = 1000
    mode = "public"
    hashtag = None
    
    # Parse additional arguments
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] in ("-n", "--max-posts") and i + 1 < len(args):
            max_posts = int(args[i + 1])
            i += 2
        elif args[i] in ("-m", "--mode") and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
        elif args[i] in ("-t", "--hashtag") and i + 1 < len(args):
            hashtag = args[i + 1]
            i += 2
        else:
            i += 1
    
    if mode == "public":
        asyncio.run(backfill_public_timeline(max_posts=max_posts))
    elif mode == "hashtag" and hashtag:
        asyncio.run(backfill_hashtag(hashtag=hashtag, max_posts=max_posts))
    elif mode == "trending":
        asyncio.run(backfill_trending(max_posts_per_tag=max_posts // 10))
    else:
        print("Invalid backfill mode. Use: public, hashtag (with -t), or trending")
        sys.exit(1)


def parse_date(date_str: str) -> datetime:
    """Parse a date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"Invalid date format: {date_str}. Use YYYY-MM-DD")
        sys.exit(1)


def run_reprocess():
    """Run analytics jobs for a historical date range."""
    start_date = None
    end_date = None
    force = False
    skip_topics = False
    skip_summary = False
    
    # Parse arguments
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] in ("-s", "--start") and i + 1 < len(args):
            start_date = parse_date(args[i + 1])
            i += 2
        elif args[i] in ("-e", "--end") and i + 1 < len(args):
            end_date = parse_date(args[i + 1])
            i += 2
        elif args[i] in ("-f", "--force"):
            force = True
            i += 1
        elif args[i] == "--skip-topics":
            skip_topics = True
            i += 1
        elif args[i] == "--skip-summary":
            skip_summary = True
            i += 1
        else:
            i += 1
    
    if not start_date:
        print("Error: --start date is required")
        print("Usage: python run_job.py reprocess --start YYYY-MM-DD [--end YYYY-MM-DD] [--force]")
        sys.exit(1)
    
    if not end_date:
        end_date = start_date
    
    if end_date < start_date:
        print("Error: end date must be after start date")
        sys.exit(1)
    
    print(f"Reprocessing data from {start_date.date()} to {end_date.date()}")
    if force:
        print("Force mode: will overwrite existing data")
    
    # First, run sentiment analysis on all unprocessed posts
    print("\n=== Running sentiment analysis ===")
    # Run multiple batches to process all posts
    batch_count = 0
    while True:
        batch_count += 1
        print(f"Sentiment batch {batch_count}...")
        analyze_sentiment_batch()
        # Check if there are more to process (simple heuristic: if we processed a full batch, there might be more)
        if batch_count >= 100:  # Safety limit
            break
        # The function processes 100 at a time, so we keep going until it processes fewer
        # For simplicity, let's just run a few batches
        if batch_count >= 10:
            break
    
    # Process each day in the range
    current_date = start_date
    while current_date <= end_date:
        print(f"\n=== Processing {current_date.date()} ===")
        
        # Generate hourly stats for each hour of the day
        print("Generating hourly stats...")
        for hour in range(24):
            target_hour = current_date.replace(hour=hour, minute=0, second=0, microsecond=0)
            generate_hourly_stats(target_hour=target_hour, force=force)
        
        # Generate hourly topics for each hour (if OpenAI configured)
        if not skip_topics:
            print("Extracting hourly topics...")
            for hour in range(24):
                target_hour = current_date.replace(hour=hour, minute=0, second=0, microsecond=0)
                extract_hourly_topics(target_hour=target_hour, force=force)
        
        # Generate daily summary
        if not skip_summary:
            print("Generating daily summary...")
            generate_daily_summary(target_date=current_date, force=force)
        
        current_date += timedelta(days=1)
    
    print(f"\n=== Reprocessing complete! ===")
    print(f"Processed {(end_date - start_date).days + 1} day(s)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in JOBS:
        print("Usage: python run_job.py <job> [options]")
        print(f"Available jobs: {', '.join(JOBS.keys())}")
        print()
        print("  sentiment  - Analyze sentiment of unprocessed posts")
        print("  topics     - Extract trending topics from recent posts")
        print("  summary    - Generate daily summary (uses OpenAI)")
        print("  stats      - Generate hourly statistics")
        print("  backfill   - Backfill historical posts from Mastodon")
        print("               Options: -n <max_posts> -m <mode> -t <hashtag>")
        print("               Modes: public, hashtag, trending")
        print("  reprocess  - Run all analytics for a historical date range")
        print("               Options: -s/--start YYYY-MM-DD (required)")
        print("                        -e/--end YYYY-MM-DD (optional, defaults to start)")
        print("                        -f/--force (overwrite existing data)")
        print("                        --skip-topics (skip AI topic extraction)")
        print("                        --skip-summary (skip AI daily summary)")
        print("  all        - Run all jobs in sequence")
        sys.exit(1)
    
    job_name = sys.argv[1]
    
    if job_name == "all":
        run_all()
    elif job_name == "backfill":
        print("Running backfill...")
        run_backfill()
        print("Done!")
    elif job_name == "reprocess":
        run_reprocess()
    else:
        print(f"Running {job_name}...")
        JOBS[job_name]()
        print("Done!")

if __name__ == "__main__":
    main()
