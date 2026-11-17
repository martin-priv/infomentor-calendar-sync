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
        time_min and time_max should be RFC3339 strings (e.g. '2026-11-17T00:00:00Z')
        """
        events = []
        page_token = None
        while True:
            resp = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
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
            "summary": "Leo: Matteprov Kapitel 2",
            "description": "...",
            "start": "2026-11-20T09:00:00+01:00" or "2026-11-20",
            "end": "2026-11-20T10:00:00+01:00" or "2026-11-20",
            "is_all_day": False,
            "location": "...",
            "infomentor_id": "cal_223855849",
            "child_name": "Leo"
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
        if existing_events:
            for ev in existing_events:
                props = ev.get("extendedProperties", {}).get("private", {})
                if props.get("infomentor_id") == infomentor_id and infomentor_id:
                    existing_id = ev["id"]
                    break

        if not existing_id and infomentor_id:
            # Search by private extended property
            res = self.service.events().list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"infomentor_id={infomentor_id}"
            ).execute()
            items = res.get("items", [])
            if items:
                existing_id = items[0]["id"]

        if existing_id:
            updated = self.service.events().update(
                calendarId=self.calendar_id,
                eventId=existing_id,
                body=body
            ).execute()
            return {"action": "updated", "id": existing_id, "summary": updated.get("summary")}
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
