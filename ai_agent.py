import os
import json
import re
import time
import requests
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

def clean_html(raw_html: Optional[str]) -> str:
    """Removes HTML tags and cleans up whitespace."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()

class InfomentorAIAgent:
    def __init__(self, api_key: Optional[str] = None, model: str = "models/gemini-flash-latest"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required for InfomentorAIAgent")
        self.model = model
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/{self.model}:generateContent?key={self.api_key}"

    def synthesize_events(
        self,
        child_name: str,
        calendar_entries: List[Dict[str, Any]],
        uols: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        existing_calendar_events: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Synthesizes raw calendar events, UOL study plans, and teacher messages
        into a coherent, high-value list of Google Calendar events.
        """
        # Prepare concise representation of input data
        clean_cal = []
        for c in calendar_entries:
            clean_cal.append({
                "id": str(c.get("id")),
                "title": c.get("title") or c.get("text", ""),
                "description": clean_html(c.get("text", "")),
                "start": c.get("startDateFull") or c.get("startDate"),
                "end": c.get("endDateFull") or c.get("endDate") or c.get("startDateFull") or c.get("startDate"),
                "is_all_day": c.get("isAllDayEvent", False),
                "start_time": c.get("startTime"),
                "end_time": c.get("endTime"),
                "subjects": c.get("subjects", [])
            })

        clean_uol = []
        for u in uols:
            overview_text = ""
            if "sections" in u:
                for s in u.get("sections", []):
                    for ov in s.get("overview", []):
                        overview_text += f"{ov.get('label')}: {ov.get('value')} | "
            details = (overview_text or clean_html(u.get("description", "")))[:400]
            clean_uol.append({
                "id": u.get("id"),
                "title": u.get("title"),
                "details": details
            })

        clean_msgs = []
        # Limit to the 5 most recent messages and truncate bodies
        for m in messages[:5]:
            clean_msgs.append({
                "id": m.get("id"),
                "subject": m.get("messageSubject") or m.get("subject"),
                "date": m.get("timeSent") or m.get("date"),
                "sender": m.get("sentUser", {}).get("displayName") if isinstance(m.get("sentUser"), dict) else "",
                "body": clean_html(m.get("body") or m.get("messageBody", ""))[:350]
            })

        prompt = f"""
Du är en intelligent assistent som hjälper en familj att hålla ordning på skolaktiviteter i Google Calendar för eleven: {child_name}.

INDATA FRÅN INFOMENTOR:
1. KALENDERHÄNDELSER (prov, läxor, deadlines, aktiviteter):
{json.dumps(clean_cal, ensure_ascii=False, indent=2)}

2. PLANERINGAR / UOL (studieplaner, provområden, kapitel och vad eleven ska plugga på):
{json.dumps(clean_uol, ensure_ascii=False, indent=2)}

3. MEDDELANDEN FRÅN LÄRARE (senaste meddelanden, information om inställda lektioner, sena starter, packlistor):
{json.dumps(clean_msgs, ensure_ascii=False, indent=2)}

UPPGIFT:
Skapa en sammanhållen, berikad lista av kalenderhändelser att synka till Google Calendar.

REGLER OCH RIKTLINJER:
1. TITELFORMAT:
   Varje händelse ska inledas med barnets förnamn: "{child_name.split()[0]}: [Kort beskrivning]"
   Exempel:
   - "{child_name.split()[0]}: Prov i Matematik (Kapitel 2 Tal)"
   - "{child_name.split()[0]}: Engelska - Glosor & textläxa"
   - "{child_name.split()[0]}: Friluftsdag vid Hellasgården"
   - "{child_name.split()[0]}: ⚠️ Inställd lektion: Franska (börjar 09:15)"

2. SAMMANSLAGNING OCH BERIKNING:
   - Om en kalenderhändelse är ett prov eller en inlämning, leta upp relevant Planering (UOL) eller meddelande som beskriver vad provet handlar om (t.ex. kapitel, sidor, begrepp, länk till classroom).
   - Skriv en strukturerad och tydlig beskrivning:
     📌 Vad: [Beskrivning av provet/händelsen]
     📖 Vad ska pluggas på: [Specifika kapitel, sidnummer, glosor, instruktioner]
     ℹ️ Info från lärare: [Eventuella tips eller detaljer]
     🔗 Källa: InfoMentor [Planering eller Kalender]

3. SCHEMAÄNDRINGAR OCH INSTÄLLDA LEKTIONER:
   - Granska lärarens meddelanden. Om ett meddelande nämner att en lektion är inställd, att skoldagen börjar senare (t.ex. kl 09:00 i stället för 08:15) eller slutar tidigare:
     Skapa en kalenderhändelse för den berörda dagen/tiden med varningsemblemet ⚠️ så föräldern ser det direkt!

4. EXKLUDERA RUTIN- OCH DROP-IN-AKTIVITETER:
   - Ta INTE med vanliga schemalagda drop-in-studiestugor eller läxhjälp (t.ex. "Studion öppen") eller allmän fritidspassning om det inte rör sig om en specifik prov/inlämningsdeadline eller avvikelse.

5. ID OCH SPÅRBARHET:
   - Ange ett unikt `infomentor_id` för varje händelse så att vi kan uppdatera den utan dubbletter:
     T.ex. "cal_<id>" om baserad på kalenderpost, "msg_<id>" om baserad på meddelande, eller "uol_<id>".

5. DATUM OCH TIDER:
   - Om det är en heldagshändelse (t.ex. ett prov där bara datum anges, eller studiedag):
     Sätt `is_all_day: true`, och `start` samt `end` till formatet "YYYY-MM-DD".
   - Om klockslag finns (t.ex. start_time "09:00", end_time "10:00"):
     Sätt `is_all_day: false`, och `start` samt `end` till ISO 8601 sträng "YYYY-MM-DDTHH:MM:SS+01:00".

6. FORMAT:
   Svara ENDAST med ett giltigt JSON-objekt med strukturen:
   {{
     "events": [
       {{
         "infomentor_id": "...",
         "summary": "...",
         "description": "...",
         "start": "...",
         "end": "...",
         "is_all_day": false,
         "location": "",
         "event_type": "EXAM" // Välj: EXAM, HOMEWORK, NOTICE, SCHEDULE_CHANGE, SPECIAL_DAY
       }}
     ]
   }}
"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2
            }
        }

        resp = None
        for attempt in range(2):
            try:
                resp = requests.post(self.endpoint, json=payload, timeout=90)
                if resp.status_code == 200:
                    break
            except Exception as ex:
                if attempt == 1:
                    raise
                print(f"   ⚠️ Gemini request attempt {attempt + 1} failed ({ex}), retrying...")
                time.sleep(2)

        if not resp or resp.status_code != 200:
            err_msg = resp.text if resp else "No response"
            raise RuntimeError(f"Gemini API error: {resp.status_code if resp else 'None'} - {err_msg}")

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return []

        text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
        try:
            parsed = json.loads(text_content)
            events = parsed.get("events", [])
            for e in events:
                e["child_name"] = child_name.split()[0]
            return events
        except Exception as ex:
            print(f"Failed to parse Gemini JSON output: {ex}")
            return []
