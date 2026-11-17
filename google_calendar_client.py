from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import os

class GoogleCalendarClient:
    def __init__(self, service_account_file: str, calendar_id: str):
        self.calendar_id = calendar_id
        self.scopes = ["https://www.googleapis.com/auth/calendar"]
        self.creds = service_account.Credentials.from_service_account_file(
            service_account_file,
            scopes=self.scopes
        )
        self.service = build("calendar", "v3", credentials=self.creds)

    def test_connection(self) -> Dict[str, Any]:
        """Verifies calendar access and returns calendar metadata"""
        return self.service.calendars().get(calendarId=self.calendar_id).execute()

    def get_existing_events(self, time_min: str, time_max: str) -> List[Dict[str, Any]]:
        """
        Fetches events in date range that were synced from InfoMentor.
        Includes cancelled/deleted events so user deletions can be respected.
        """
        events = []
        page_token = None
        while True:
            resp = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                showDeleted=True,
                singleEvents=True,
                pageToken=page_token
            ).execute()
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return events

    def upsert_event(self, event_data: Dict[str, Any], existing_events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Creates or updates a Google Calendar event.
        event_data format:
        {
            "summary": "Elev: Matteprov Kapitel 2",
            "description": "...",
            "start": "2026-11-20T09:00:00+01:00" or "2026-11-20",
            "end": "2026-11-20T10:00:00+01:00" or "2026-11-20",
            "is_all_day": False,
            "location": "...",
            "infomentor_id": "cal_223855849",
            "child_name": "Elev"
        }
        """
        infomentor_id = str(event_data.get("infomentor_id", ""))
        child_name = event_data.get("child_name", "")

        body: Dict[str, Any] = {
            "summary": event_data["summary"],
            "description": event_data.get("description", ""),
            "extendedProperties": {
                "private": {
                    "source": "infomentor_sync",
                    "infomentor_id": infomentor_id,
                    "child_name": child_name
                }
            }
        }

        if event_data.get("location"):
            body["location"] = event_data["location"]

        color_id = event_data.get("color_id") or event_data.get("colorId")
        if color_id:
            body["colorId"] = str(color_id)

        if event_data.get("is_all_day"):
            s_date = event_data["start"][:10]
            e_date = event_data["end"][:10] if event_data.get("end") else s_date
            if s_date >= e_date:
                dt = datetime.strptime(s_date, "%Y-%m-%d") + timedelta(days=1)
                e_date = dt.strftime("%Y-%m-%d")
            body["start"] = {"date": s_date}
            body["end"] = {"date": e_date}
        else:
            s_time = event_data["start"]
            e_time = event_data.get("end", s_time)
            if len(s_time) == 10:
                s_time = f"{s_time}T09:00:00+01:00"
            if len(e_time) == 10:
                e_time = f"{e_time}T10:00:00+01:00"
            if s_time >= e_time:
                try:
                    dt = datetime.fromisoformat(s_time.replace("Z", "+00:00")) + timedelta(hours=1)
                    e_time = dt.isoformat()
                except Exception:
                    e_time = s_time
            body["start"] = {"dateTime": s_time, "timeZone": "Europe/Stockholm"}
            body["end"] = {"dateTime": e_time, "timeZone": "Europe/Stockholm"}

        # Check if already exists in existing_events or search by private extended property
        existing_id = None
        existing_event = None
        if existing_events:
            for ev in existing_events:
                props = ev.get("extendedProperties", {}).get("private", {})
                if props.get("infomentor_id") == infomentor_id and infomentor_id:
                    existing_id = ev["id"]
                    existing_event = ev
                    break

        if not existing_id and infomentor_id:
            # Search by private extended property
            res = self.service.events().list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"infomentor_id={infomentor_id}",
                showDeleted=True
            ).execute()
            items = res.get("items", [])
            if items:
                existing_id = items[0]["id"]
                existing_event = items[0]

        # Respect User Deletions (Alternative A)
        if existing_event and existing_event.get("status") == "cancelled":
            return {"action": "skipped_deleted", "id": existing_id, "summary": event_data["summary"]}

        # Respect User Modifications (Alternative A)
        user_modified_summary = False
        user_modified_time = False
        user_modified_desc = False

        if existing_event and existing_event.get("status") != "cancelled":
            ex_props = existing_event.get("extendedProperties", {}).get("private", {})
            last_summary = ex_props.get("last_synced_summary")
            last_start = ex_props.get("last_synced_start")
            last_end = ex_props.get("last_synced_end")
            last_desc = ex_props.get("last_synced_desc")

            # Explicit lock tag 🔒 or [manuell] in title or description
            ex_summary = existing_event.get("summary", "")
            ex_desc = existing_event.get("description", "")
            if "🔒" in ex_summary or "[manuell]" in ex_summary.lower() or "🔒" in ex_desc:
                user_modified_summary = True
                user_modified_time = True
                user_modified_desc = True

            # Check if user customized the title
            if last_summary and ex_summary != last_summary:
                user_modified_summary = True
            elif not last_summary and ex_summary and ex_summary != event_data["summary"]:
                user_modified_summary = True

            # Check if user customized the time
            ex_s = existing_event.get("start", {}).get("dateTime") or existing_event.get("start", {}).get("date")
            ex_e = existing_event.get("end", {}).get("dateTime") or existing_event.get("end", {}).get("date")
            if last_start and (ex_s != last_start or ex_e != last_end):
                user_modified_time = True

            # Check if user customized description
            if last_desc and ex_desc != last_desc:
                user_modified_desc = True

            # Preserve user modifications
            if user_modified_summary:
                body["summary"] = ex_summary
            if user_modified_time:
                body["start"] = existing_event["start"]
                body["end"] = existing_event["end"]
            if user_modified_desc:
                body["description"] = ex_desc

        # Track what we sync so future runs know if user modified it
        sync_start_val = body["start"].get("dateTime") or body["start"].get("date")
        sync_end_val = body["end"].get("dateTime") or body["end"].get("date")
        body["extendedProperties"]["private"]["last_synced_summary"] = body["summary"]
        body["extendedProperties"]["private"]["last_synced_start"] = sync_start_val
        body["extendedProperties"]["private"]["last_synced_end"] = sync_end_val
        body["extendedProperties"]["private"]["last_synced_desc"] = body.get("description", "")
        if user_modified_summary or user_modified_time or user_modified_desc:
            body["extendedProperties"]["private"]["user_modified"] = "true"

        if existing_id:
            updated = self.service.events().update(
                calendarId=self.calendar_id,
                eventId=existing_id,
                body=body
            ).execute()
            action = "updated_preserved" if (user_modified_summary or user_modified_time) else "updated"
            return {"action": action, "id": existing_id, "summary": updated.get("summary")}
        else:
            created = self.service.events().insert(
                calendarId=self.calendar_id,
                body=body
            ).execute()
            return {"action": "created", "id": created.get("id"), "summary": created.get("summary")}

    def delete_event(self, event_id: str):
        """Deletes an event by Google Calendar ID"""
        self.service.events().delete(
            calendarId=self.calendar_id,
            eventId=event_id
        ).execute()
