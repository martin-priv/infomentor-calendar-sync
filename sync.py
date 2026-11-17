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

SUBJECT_MAP = {
    "BL": "Bild",
    "EN": "Engelska",
    "HKK": "Hem- och konsumentkunskap",
    "IDH": "Idrott och hälsa",
    "MA": "Matematik",
    "ME": "Mentorstid",
    "MU": "Musik",
    "NO": "Naturorientering (Biologi/Fysik/Kemi)",
    "SKV: Läs": "Läsning",
    "SL": "Slöjd",
    "SO": "Samhällsorientering (Historia/Geografi/Samhällskunskap)",
    "SPV SP": "Spanska",
    "SPV TY": "Tyska",
    "SV": "Svenska",
    "TK": "Teknik",
    "Schemabrytande: Friluftsdag": "Friluftsdag",
    "Schemabrytande: NP": "Nationella prov (NP)",
    "Studiestöd": "Studiestöd",
}
NON_LESSONS = {"RAST", "LUNCH", "Förflyttning", "Ombyte"}

def run_sync(child_filter: str = None, dry_run: bool = False, days_ahead: int = None, custom_calendar_id: str = None, custom_exclude: str = None, sync_school_hours: bool = True):
    # Load configuration
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    sync_days = days_ahead or int(os.environ.get("SYNC_DAYS_AHEAD", "7"))

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

    env_school_hours = os.environ.get("SYNC_SCHOOL_HOURS", "true").lower() in ("true", "1", "yes")
    do_school_hours = sync_school_hours and env_school_hours

    def is_excluded(text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        return any(p in t for p in exclude_patterns)

    # Child color mapping: "Adam:9,Isak:10,Leo:4" or default soft palette
    # Google Calendar colors: 9=Blueberry (#5484ed), 10=Basil (#51b749), 4=Flamingo (#ff887c), 7=Peacock (#46d6db), 3=Grape (#dbadff)
    child_colors_config = {}
    raw_colors = os.environ.get("CHILD_COLORS", "")
    if raw_colors:
        for pair in raw_colors.split(","):
            if ":" in pair:
                cname, cid = pair.split(":", 1)
                child_colors_config[cname.strip().lower()] = cid.strip()

    DEFAULT_PALETTE = ["9", "10", "4", "7", "3", "6", "2", "1"]

    print(f"🔄 Starting InfoMentor Sync (Window: {sync_days} days, DryRun: {dry_run})")
    print(f"🔑 Using InfoMentor account: {im_user}")
    if exclude_patterns:
        print(f"🚫 Active exclusion filter: {', '.join(exclude_patterns)}")
    if do_school_hours:
        print("🏫 Daily school hours overview enabled")

    # Step 1: Connect to InfoMentor
    client = InfomentorClient()
    print("🌐 Logging in to InfoMentor...")
    client.login(im_user, im_pass)
    print(f"✅ Logged in successfully. Found {len(client.pupils)} children:")
    for p in client.pupils:
        print(f"   • {p['name']} (ID: {p['id']})")

    # Step 2: Initialize AI Agent
    ai_agent = InfomentorAIAgent()
    print(f"🤖 AI Provider: {ai_agent.provider.upper()} (Model: {ai_agent.model})")

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
    end_date = (datetime.now() + timedelta(days=sync_days)).strftime("%Y-%m-%d")

    all_synthesized_events = []

    # Step 4: Loop through pupils
    for child_idx, pupil in enumerate(client.pupils):
        full_name = pupil["name"]
        short_name = full_name.split(",")[0].strip() if "," in full_name else full_name.split()[0]
        # In InfoMentor: "Efternamn, Förnamn" -> extract first name
        parts = [p.strip() for p in full_name.split(",")]
        first_name = parts[1] if len(parts) > 1 else parts[0]

        if child_filter and child_filter.lower() not in full_name.lower():
            print(f"⏭️ Skipping {full_name} (filtered)")
            continue

        child_color = child_colors_config.get(first_name.lower()) or DEFAULT_PALETTE[child_idx % len(DEFAULT_PALETTE)]
        print(f"\n👤 Processing: {first_name} ({full_name}) [Color: {child_color}]...")
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
        print(f"      Found {len(uols)} active planeringar. Enriching top {min(len(uols), 5)}...")
        for u in uols[:5]:
            detail = client.get_uol_details(u["id"])
            if detail:
                u.update(detail)

        # Fetch messages
        print("   📥 Fetching teacher messages...")
        raw_msgs = client.get_messages()[:5]
        for m in raw_msgs:
            detail = client.get_message_detail(m["id"])
            if detail:
                m.update(detail)
        print(f"      Fetched {len(raw_msgs)} recent messages with bodies.")

        # Fetch timetable schedule
        print("   📥 Fetching timetable schedule...")
        raw_schedule = client.get_schedule(start_date, end_date)
        clean_sched_by_day = {}
        lessons_by_day = {}
        for item in raw_schedule:
            title = item.get("title", "")
            if title in NON_LESSONS or is_excluded(title):
                continue
            day = item["start"][:10]
            s_time = item["startTime"]
            e_time = item["endTime"]
            room = item.get("notes", {}).get("roomInfo", "").strip()
            tutor = item.get("notes", {}).get("tutors", "").strip()
            subj = SUBJECT_MAP.get(title, title)
            desc = f"{s_time}-{e_time} {subj}"
            if room:
                desc += f" (Sal: {room})"
            if tutor:
                desc += f" (Lärare: {tutor})"
            clean_sched_by_day.setdefault(day, []).append(desc)
            lessons_by_day.setdefault(day, []).append({
                "title": title,
                "subject": subj,
                "startTime": s_time,
                "endTime": e_time,
                "room": room,
                "tutor": tutor
            })
        print(f"      Found schedule across {len(clean_sched_by_day)} school days.")

        # Pass full schedule across the sync window to AI (at most 10 school days, ensures full timetable context)
        sched_for_ai = clean_sched_by_day

        # AI Synthesis (with timetable slotting & message precedence)
        print(f"   🧠 Synthesizing with Gemini AI Agent ({len(sched_for_ai)} schedule days in prompt)...")
        synthesized = ai_agent.synthesize_events(
            child_name=f"{first_name} {parts[0]}" if len(parts) > 1 else first_name,
            calendar_entries=cal_entries,
            uols=uols[:5],
            messages=raw_msgs,
            schedule_by_day=sched_for_ai
        )

        # Post-filter synthesized events
        synthesized = [ev for ev in synthesized if not is_excluded(ev.get("summary", ""))]
        print(f"   ✨ Generated {len(synthesized)} synthesized events for {first_name}.")

        # School day overview events (Skolstart & Sluttid med dagens schema)
        school_day_events = []
        if do_school_hours:
            pupil_id = pupil.get("id", first_name.lower())
            for day, day_lessons in sorted(lessons_by_day.items()):
                # Exclude optional drop-in studiestöd from school day start/end bounds
                core_lessons = [l for l in day_lessons if l["title"] != "Studiestöd"] or day_lessons
                if not core_lessons:
                    continue
                first_start = min(l["startTime"] for l in core_lessons)
                last_end = max(l["endTime"] for l in core_lessons)

                desc_lines = ["📚 Dagens schema:"]
                for l in core_lessons:
                    extra = []
                    if l["room"]: extra.append(f"Sal: {l['room']}")
                    if l["tutor"]: extra.append(f"Lärare: {l['tutor']}")
                    extra_str = f" ({', '.join(extra)})" if extra else ""
                    desc_lines.append(f"• {l['startTime']}–{l['endTime']}: {l['subject']}{extra_str}")

                # Compact 15-minute slot at start time to avoid massive overlapping blocks
                s_dt = datetime.strptime(f"{day}T{first_start}:00", "%Y-%m-%dT%H:%M:%S")
                e_dt = s_dt + timedelta(minutes=15)
                end_iso = e_dt.strftime("%Y-%m-%dT%H:%M:%S+01:00")

                school_day_events.append({
                    "infomentor_id": f"schoolday_{pupil_id}_{day}",
                    "summary": f"{first_name}: Skola {first_start}–{last_end}",
                    "description": "\n".join(desc_lines),
                    "start": f"{day}T{first_start}:00+01:00",
                    "end": end_iso,
                    "is_all_day": False,
                    "location": "",
                    "event_type": "SCHOOL_DAY",
                    "child_name": first_name,
                    "child_first_name": first_name
                })
            print(f"   🏫 Generated {len(school_day_events)} daily school schedule events for {first_name}.")

        for ev in synthesized:
            ev["child_first_name"] = first_name
            ev["color_id"] = child_color
            all_synthesized_events.append(ev)
        for ev in school_day_events:
            ev["color_id"] = child_color
            all_synthesized_events.append(ev)

    # Step 5: Push to Google Calendar or Print (Dry-run)
    print(f"\n📊 Total synthesized events across children: {len(all_synthesized_events)}")

    # Check existing events and purge any previously synced events that match exclusion list
    time_min = f"{start_date}T00:00:00Z"
    time_max = f"{end_date}T23:59:59Z"

    if not dry_run and gcal:
        existing = gcal.get_existing_events(time_min, time_max)
        print(f"   Found {len(existing)} existing events currently in calendar window.")

        # Purge excluded events and obsolete multi-day all-day test events
        clean_existing = []
        purged_count = 0
        for ev in existing:
            if ev.get("status") == "cancelled":
                clean_existing.append(ev)
                continue

            summary = ev.get("summary", "")
            props = ev.get("extendedProperties", {}).get("private", {})
            im_id = props.get("infomentor_id", "")
            is_synced = props.get("source") == "infomentor_sync" or bool(im_id)

            # Check if this is an obsolete multi-day test banner (e.g. uol_... spanning multiple days)
            is_obsolete_all_day_test = False
            start = ev.get("start", {})
            end = ev.get("end", {})
            if is_synced and "date" in start:
                s_d = start.get("date", "")
                e_d = end.get("date", "")
                if im_id in ("uol_8592172_prov_v49", "uol_8592172_v49"):
                    is_obsolete_all_day_test = True
                elif ("prov" in summary.lower() or "läxa" in summary.lower() or "uol_" in im_id):
                    try:
                        s_dt = datetime.strptime(s_d, "%Y-%m-%d")
                        e_dt = datetime.strptime(e_d, "%Y-%m-%d")
                        if (e_dt - s_dt).days > 1:
                            is_obsolete_all_day_test = True
                    except Exception:
                        pass

            if is_synced and (is_excluded(summary) or is_obsolete_all_day_test):
                gcal.delete_event(ev["id"])
                print(f"   🗑️ Purged obsolete/excluded event: {summary} ({im_id})")
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
    preserved_count = 0
    skipped_deleted_count = 0

    for ev in all_synthesized_events:
        res = gcal.upsert_event(ev, existing_events=existing)
        act = res.get("action")
        if act == "created":
            created_count += 1
            print(f"   ➕ Created: {res.get('summary')}")
        elif act == "updated_preserved":
            preserved_count += 1
            print(f"   🛡️ Preserved user modifications: {res.get('summary')}")
        elif act == "skipped_deleted":
            skipped_deleted_count += 1
            print(f"   🚫 Skipped deleted event: {res.get('summary')}")
        else:
            updated_count += 1
            print(f"   🔄 Updated: {res.get('summary')}")

    print("\n✅ Sync completed successfully!")
    print(f"   🎉 Created: {created_count} events")
    print(f"   🔄 Updated: {updated_count} events")
    if preserved_count > 0:
        print(f"   🛡️ Preserved user changes: {preserved_count} events")
    if skipped_deleted_count > 0:
        print(f"   🚫 Skipped user-deleted: {skipped_deleted_count} events")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync InfoMentor events to Google Calendar")
    parser.add_argument("--child", type=str, help="Filter by child name (e.g. förnamn)")
    parser.add_argument("--dry-run", action="store_true", help="Print synthesized events without pushing to Google Calendar")
    parser.add_argument("--days", type=int, default=None, help="Number of days ahead to sync (default: from .env or 7)")
    parser.add_argument("--calendar-id", type=str, help="Override Google Calendar ID")
    parser.add_argument("--exclude", type=str, help="Comma-separated patterns to exclude (e.g. 'Studion öppen,Fritids')")
    parser.add_argument("--no-school-hours", action="store_true", help="Do not create daily school hours overview events")
    args = parser.parse_args()

    run_sync(
        child_filter=args.child,
        dry_run=args.dry_run,
        days_ahead=args.days,
        custom_calendar_id=args.calendar_id,
        custom_exclude=args.exclude,
        sync_school_hours=not args.no_school_hours
    )
