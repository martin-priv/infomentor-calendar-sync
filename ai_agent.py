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
    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        openai_key = os.environ.get("OPENAI_API_KEY")
        gemini_key = os.environ.get("GEMINI_API_KEY")

        chosen_provider = provider or os.environ.get("AI_PROVIDER")
        if not chosen_provider:
            if openai_key and not gemini_key:
                chosen_provider = "openai"
            else:
                chosen_provider = "gemini"

        self.provider = chosen_provider.lower().strip()
        if self.provider in ("openai", "open-ai"):
            self.provider = "openai"
            self.api_key = api_key or openai_key
            if not self.api_key:
                raise ValueError("OPENAI_API_KEY is required when using OpenAI provider")
            self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        else:
            self.provider = "gemini"
            self.api_key = api_key or gemini_key
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY or OPENAI_API_KEY is required for InfomentorAIAgent")
            self.model = model or os.environ.get("GEMINI_MODEL", "models/gemini-flash-latest")
            self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/{self.model}:generateContent?key={self.api_key}"

    def _call_llm(self, prompt: str) -> str:
        """Executes the prompt against the configured LLM provider (OpenAI or Gemini)"""
        if self.provider == "openai":
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are an intelligent school assistant that outputs only valid JSON conforming strictly to the requested schema."},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2
            }
            url = f"{self.base_url}/chat/completions"
            for attempt in range(2):
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=120)
                    if resp.status_code == 200:
                        data = resp.json()
                        return data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                    else:
                        raise RuntimeError(f"OpenAI API error {resp.status_code}: {resp.text}")
                except Exception as ex:
                    if attempt == 1:
                        raise
                    print(f"   ⚠️ OpenAI request attempt {attempt + 1} failed ({ex}), retrying...")
                    time.sleep(2)
        else:
            # Gemini provider
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "response_mime_type": "application/json",
                    "temperature": 0.2
                }
            }
            for attempt in range(2):
                try:
                    resp = requests.post(self.endpoint, json=payload, timeout=120)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if not candidates:
                            return "{}"
                        return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
                    else:
                        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text}")
                except Exception as ex:
                    if attempt == 1:
                        raise
                    print(f"   ⚠️ Gemini request attempt {attempt + 1} failed ({ex}), retrying...")
                    time.sleep(2)
        return "{}"

    def synthesize_events(
        self,
        child_name: str,
        calendar_entries: List[Dict[str, Any]],
        uols: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        existing_calendar_events: Optional[List[Dict[str, Any]]] = None,
        schedule_by_day: Optional[Dict[str, List[str]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Synthesizes raw calendar events, UOL study plans, teacher messages, and daily timetables
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

        # Extract first name cleanly (handles "Efternamn, Förnamn" as well as "Förnamn Efternamn")
        if "," in child_name:
            first_name = child_name.split(",")[1].strip()
        else:
            first_name = child_name.split()[0].strip()

        prompt = f"""
Du är en intelligent assistent som hjälper en familj att hålla ordning på skolaktiviteter i Google Calendar för eleven: {first_name}.

INDATA FRÅN INFOMENTOR:
1. KALENDERHÄNDELSER (prov, läxor, deadlines, aktiviteter):
{json.dumps(clean_cal, ensure_ascii=False, indent=2)}

2. PLANERINGAR / UOL (studieplaner, provområden, kapitel och vad eleven ska plugga på):
{json.dumps(clean_uol, ensure_ascii=False, indent=2)}

3. MEDDELANDEN FRÅN LÄRARE (senaste meddelanden, information om inställda lektioner, sena starter, packlistor, avvikande tider/lokaler för prov):
{json.dumps(clean_msgs, ensure_ascii=False, indent=2)}

4. SCHEMA OCH LEKTIONSÖVERSIKT PER DAG:
{json.dumps(schedule_by_day or {}, ensure_ascii=False, indent=2)}

UPPGIFT:
Skapa en sammanhållen, berikad lista av kalenderhändelser att synka till Google Calendar.

REGLER OCH RIKTLINJER:
1. TITELFORMAT:
   Varje händelse ska inledas med barnets förnamn: "{first_name}: [Kort beskrivning]"
   Exempel:
   - "{first_name}: Prov i Matematik (Kapitel 2 Tal)"
   - "{first_name}: Engelska - Glosor & textläxa"
   - "{first_name}: Friluftsdag vid Hellasgården"
   - "{first_name}: ⚠️ Inställd lektion: Franska (börjar 09:15)"

2. SAMMANSLAGNING OCH BERIKNING:
   - Om en kalenderhändelse är ett prov eller en inlämning, leta upp relevant Planering (UOL) eller meddelande som beskriver vad provet handlar om (t.ex. kapitel, sidor, begrepp, länk till classroom).
   - Skriv en strukturerad och tydlig beskrivning:
     📌 Vad: [Beskrivning av provet/händelsen]
     📖 Vad ska pluggas på: [Specifika kapitel, sidnummer, glosor, instruktioner]
     ℹ️ Info från lärare: [Eventuella tips eller detaljer]
     🔗 Källa: InfoMentor [Planering eller Kalender]

3. TIDSSÄTTNING OCH LEKTIONSSLOTTING (MYCKET VIKTIGT):
   För prov, förhör, inlämningar och ämneshändelser:
   a) LÄRARMEDDELANDEN HAR HÖGSTA PRIORITET FÖR TID OCH SAL:
      Om ett lärarmeddelande anger en specifik tid eller lokal (t.ex. "NP i Ma hålls kl 08:30-10:00 i Aulan" eller "Provet startar kl 13:00 i sal No 1"):
      Använd ALLTID tiden och salen från meddelandet! Sätt `is_all_day: false`, `start` och `end` som ISO 8601 "YYYY-MM-DDTHH:MM:SS+01:00", och `location` till den angivna lokalen.
   b) OM INGET MEDDELANDE ANGER SPECIFIK TID:
      Leta upp ämnet i dagens lektionsschema under punkt 4 (t.ex. "Prov i Matematik" -> Matematik/MA, "Biologiprov" -> Naturorientering/NO, "Glosläxa" -> Engelska/EN, "Slutprov Slöjd" -> Slöjd/SL, "Prov Historia" -> Samhällsorientering/SO, "Idrottsprov" -> Idrott och hälsa/IDH).
      Sätt `start` och `end` till exakt lektionens start- och sluttid i ISO 8601 ("YYYY-MM-DDTHH:MM:SS+01:00"), och sätt `is_all_day: false`!
      Om lektionen anger en specifik sal (t.ex. "Sal: Blå 3"), sätt `location` till den salen!
   c) OM ÄMNET INTE FINNS I DAGENS SCHEMA eller om händelsen är en allmän dag (t.ex. studiedag, "Älta skolas dag", "Julavslutning", eller en deadline utan specifik lektion):
      Sätt `is_all_day: true`, och `start` samt `end` till formatet "YYYY-MM-DD".

4. SCHEMAÄNDRINGAR OCH INSTÄLLDA LEKTIONER:
   - Granska lärarens meddelanden. Om ett meddelande nämner att en lektion är inställd, att skoldagen börjar senare (t.ex. kl 09:00 i stället för 08:15) eller slutar tidigare:
     Skapa en kalenderhändelse för den berörda dagen/tiden med varningsemblemet ⚠️ så föräldern ser det direkt!
     Sätt `event_type: "SCHEDULE_CHANGE"`.

5. EXKLUDERA RUTIN- OCH DROP-IN-AKTIVITETER:
   - Ta INTE med vanliga schemalagda drop-in-studiestugor eller läxhjälp (t.ex. "Studion öppen") eller allmän fritidspassning om det inte rör sig om en specifik prov/inlämningsdeadline eller avvikelse.

6. ID OCH SPÅRBARHET:
   - Ange ett unikt `infomentor_id` för varje händelse så att vi kan uppdatera den utan dubbletter:
     T.ex. "cal_<id>" om baserad på kalenderpost, "msg_<id>" om baserad på meddelande, eller "uol_<id>".

7. FORMAT:
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

        text_content = self._call_llm(prompt)
        try:
            parsed = json.loads(text_content)
            events = parsed.get("events", [])
            for e in events:
                e["child_name"] = first_name
            return events
        except Exception as ex:
            print(f"Failed to parse {self.provider.upper()} JSON output: {ex}")
            return []
