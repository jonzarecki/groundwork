# Provider modules for data collection.
# Each provider must expose the same async interface:
#   collect_gmail(email, days) -> (text, page_count)
#   collect_calendar(email, days) -> text
#   collect_slack(days, email) -> text
#   seed_slack_cache(db_path) -> int
#   resolve_slack_cache_misses(slack_text, db_path) -> (csv, count)
#   enrich_names_from_directory(gmail_text, calendar_text, db_path) -> {email: name}
#   backfill_names(db_path, name_map) -> int
