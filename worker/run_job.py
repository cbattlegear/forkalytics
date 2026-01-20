#!/usr/bin/env python
"""Manually run scheduled jobs."""
import sys
from scheduler import (
    analyze_sentiment_batch,
    extract_hourly_topics,
    generate_daily_summary,
    generate_hourly_stats
)

JOBS = {
    "sentiment": analyze_sentiment_batch,
    "topics": extract_hourly_topics,
    "summary": generate_daily_summary,
    "stats": generate_hourly_stats,
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

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in JOBS:
        print("Usage: python run_job.py <job>")
        print(f"Available jobs: {', '.join(JOBS.keys())}")
        print()
        print("  sentiment  - Analyze sentiment of unprocessed posts")
        print("  topics     - Extract trending topics from recent posts")
        print("  summary    - Generate daily summary (uses OpenAI)")
        print("  stats      - Generate hourly statistics")
        print("  all        - Run all jobs in sequence")
        sys.exit(1)
    
    job_name = sys.argv[1]
    
    if job_name == "all":
        run_all()
    else:
        print(f"Running {job_name}...")
        JOBS[job_name]()
        print("Done!")

if __name__ == "__main__":
    main()
