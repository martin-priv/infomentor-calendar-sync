#!/usr/bin/env python3
"""
InfoMentor to Google Calendar Sync
Synthesizes events, study plans (UOL), and teacher messages using Gemini AI.
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

from infomentor_client import InfomentorClient
from ai_agent import InfomentorAIAgent
from google_calendar_client import GoogleCalendarClient

def run_sync(child_filter: str = None, dry_run: bool = False, days_ahead: int = 45, custom_calendar_id: str = None, custom_exclude: str = None):
    # Load configuration
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    im_user = os.environ.get("INFOMENTOR_USERNAME")
    im_pass = os.environ.get("INFOMENTOR_PASSWORD")
    if not im_user or not im_pass:
        print("❌ Error: INFOMENTOR_USERNAME and INFOMENTOR_PASSWORD must be set in .env")
        sys.exit(1)

    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if not os.path.isabs(sa_file):
        sa_file = os.path.join(os.path.dirname(__file__), sa_file)

    calendar_id = custom_calendar_id or os.environ.get("GOOGLE_CALENDAR_ID")

    # Exclusion patterns (comma-separated, case-insensitive)
    exclude_raw = custom_exclude or os.environ.get("EXCLUDE_PATTERNS", "Studion öppen")
    exclude_patterns = [p.strip().lower() for p in exclude_raw.split(",") if p.strip()]

    def is_excluded(text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        return any(p in t for p in exclude_patterns)

    print(f"🔄 Starting InfoMentor Sync (Window: {days_ahead} days, DryRun: {dry_run})")
    print(f"🔑 Using InfoMentor account: {im_user}")
    if exclude_patterns:
        print(f"🚫 Active exclusion filter: {', '.join(exclude_patterns)}")

    # Step 1: Connect to InfoMentor
    client = InfomentorClient()
    print("🌐 Logging in to InfoMentor...")
    client.login(im_user, im_pass)
    print(f"✅ Logged in successfully. Found {len(client.pupils)} children:")
    for p in client.pupils:
        print(f"   • {p['name']} (ID: {p['id']})")

    # Step 2: Initialize AI Agent
    ai_agent = InfomentorAIAgent()

    # Step 3: Initialize Google Calendar Client (if not dry run)
    gcal = None
    if not dry_run:
        if not calendar_id:
            print("❌ Error: GOOGLE_CALENDAR_ID is not configured in .env and not passed via --calendar-id.")
            print("   Please provide your Google Calendar ID.")
            sys.exit(1)
        print(f"📅 Connecting to Google Calendar: {calendar_id}...")
        try:
            gcal = GoogleCalendarClient(sa_file, calendar_id)
            cal_meta = gcal.test_connection()
            print(f"✅ Connected to calendar: '{cal_meta.get('summary')}'")
        except Exception as ex:
            print(f"❌ Failed to connect to Google Calendar: {ex}")
            sys.exit(1)

    start_date = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    all_synthesized_events = []

    # Step 4: Loop through pupils
    for pupil in client.pupils:
        full_name = pupil["name"]
        short_name = full_name.split(",")[0].strip() if "," in full_name else full_name.split()[0]
        # In InfoMentor: "Efternamn, Förnamn" -> extract first name
        parts = [p.strip() for p in full_name.split(",")]
        first_name = parts[1] if len(parts) > 1 else parts[0]

        if child_filter and child_filter.lower() not in full_name.lower():
            print(f"⏭️ Skipping {full_name} (filtered)")
            continue

        print(f"\n👤 Processing: {first_name} ({full_name})...")
        client.switch_pupil(pupil["switch_url"])

        # Fetch raw calendar
        print(f"   📥 Fetching calendar ({start_date} to {end_date})...")
        cal_entries = client.get_calendar(start_date, end_date)
        raw_count = len(cal_entries)
        # Apply pre-filter to drop excluded events before AI
        cal_entries = [c for c in cal_entries if not is_excluded(c.get("title") or c.get("text", ""))]
        filtered_count = raw_count - len(cal_entries)
        if filtered_count > 0:
            print(f"      Found {raw_count} entries (filtered out {filtered_count} matching exclusion patterns).")
        else:
            print(f"      Found {raw_count} raw calendar entries.")

        # Fetch UOLs (planeringar)
        print("   📥 Fetching planeringar (UOL)...")
        uols = client.get_uols()
        print(f"      Found {len(uols)} active planeringar. Enriching details...")
        for u in uols:
            detail = client.get_uol_details(u["id"])
            if detail:
                u.update(detail)

        # Fetch messages
        print("   📥 Fetching teacher messages...")
        raw_msgs = client.get_messages()[:8]
        for m in raw_msgs:
            detail = client.get_message_detail(m["id"])
            if detail:
                m.update(detail)
        print(f"      Fetched {len(raw_msgs)} recent messages with bodies.")

        # AI Synthesis
        print("   🧠 Synthesizing with Gemini AI Agent...")
        synthesized = ai_agent.synthesize_events(
            child_name=f"{first_name} {parts[0]}" if len(parts) > 1 else first_name,
            calendar_entries=cal_entries,
            uols=uols,
            messages=raw_msgs
        )

        # Post-filter synthesized events
        synthesized = [ev for ev in synthesized if not is_excluded(ev.get("summary", ""))]

        print(f"   ✨ Generated {len(synthesized)} synthesized events for {first_name}.")
        for ev in synthesized:
            ev["child_first_name"] = first_name
            all_synthesized_events.append(ev)

    # Step 5: Push to Google Calendar or Print (Dry-run)
    print(f"\n📊 Total synthesized events across children: {len(all_synthesized_events)}")

    # Check existing events and purge any previously synced events that match exclusion list
    time_min = f"{start_date}T00:00:00Z"
    time_max = f"{end_date}T23:59:59Z"

    if not dry_run and gcal:
        existing = gcal.get_existing_events(time_min, time_max)
        print(f"   Found {len(existing)} existing events currently in calendar window.")

        # Purge excluded events
        clean_existing = []
        purged_count = 0
        for ev in existing:
            summary = ev.get("summary", "")
            props = ev.get("extendedProperties", {}).get("private", {})
            if (props.get("source") == "infomentor_sync" or props.get("infomentor_id")) and is_excluded(summary):
                gcal.delete_event(ev["id"])
                print(f"   🗑️ Purged excluded event: {summary}")
                purged_count += 1
            else:
                clean_existing.append(ev)

        if purged_count > 0:
            print(f"   🧹 Purged {purged_count} excluded events from Google Calendar.")
        existing = clean_existing

    if dry_run or not gcal:
        print("\n🔍 DRY RUN SUMMARY (Events that would be synced):")
        for ev in all_synthesized_events:
            print("----------------------------------------------------------------------")
            print(f"📌 {ev.get('summary')} [{ev.get('event_type')}]")
            print(f"   Tid: {ev.get('start')} -> {ev.get('end')} (Heldag: {ev.get('is_all_day')})")
            print(f"   ID: {ev.get('infomentor_id')}")
            desc_lines = ev.get('description', '').split("\n")
            print(f"   Beskrivning: {desc_lines[0]} ({len(desc_lines)} rader)")
        print("----------------------------------------------------------------------")
        print("💡 Run without --dry-run to push events to Google Calendar.")
        return

    # Live sync
    print(f"\n🚀 Pushing events to Google Calendar ({calendar_id})...")
    created_count = 0
    updated_count = 0

    for ev in all_synthesized_events:
        res = gcal.upsert_event(ev, existing_events=existing)
        if res.get("action") == "created":
            created_count += 1
            print(f"   ➕ Created: {res.get('summary')}")
        else:
            updated_count += 1
            print(f"   🔄 Updated: {res.get('summary')}")

    print("\n✅ Sync completed successfully!")
    print(f"   🎉 Created: {created_count} events")
    print(f"   🔄 Updated: {updated_count} events")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync InfoMentor events to Google Calendar")
    parser.add_argument("--child", type=str, help="Filter by child name (e.g. förnamn)")
    parser.add_argument("--dry-run", action="store_true", help="Print synthesized events without pushing to Google Calendar")
    parser.add_argument("--days", type=int, default=45, help="Number of days ahead to sync (default: 45)")
    parser.add_argument("--calendar-id", type=str, help="Override Google Calendar ID")
    parser.add_argument("--exclude", type=str, help="Comma-separated patterns to exclude (e.g. 'Studion öppen,Fritids')")
    args = parser.parse_args()

    run_sync(
        child_filter=args.child,
        dry_run=args.dry_run,
        days_ahead=args.days,
        custom_calendar_id=args.calendar_id,
        custom_exclude=args.exclude
    )
