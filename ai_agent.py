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
        schedule_by_day: Optional[Dict[str, List[str]]] = None,
        emails: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Synthesizes raw calendar events, UOL study plans, teacher messages, school emails, and daily timetables
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
            timeline_text = ""
            if "sections" in u:
                for s in u.get("sections", []):
                    for ov in s.get("overview", []):
                        overview_text += f"{ov.get('label')}: {ov.get('value')} | "
                    if s.get("type") == "timeline":
                        for e in s.get("entries", [])[:3]:
                            txt = clean_html(e.get("text", ""))
                            if txt:
                                timeline_text += f"{txt} | "
            details = (timeline_text or overview_text or clean_html(u.get("description", "")))[:600]
            clean_uol.append({
                "id": str(u.get("id")),
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

        clean_emails = []
        if emails:
            for em in emails[:8]:
                clean_emails.append({
                    "id": em.get("id"),
                    "date": em.get("date"),
                    "sender": em.get("sender"),
                    "subject": em.get("subject"),
                    "body": (em.get("body") or "")[:450]
                })

        # Extract first name cleanly (handles "Efternamn, Förnamn" as well as "Förnamn Efternamn")
        if "," in child_name:
            first_name = child_name.split(",")[1].strip()
        else:
            first_name = child_name.split()[0].strip()

        prompt = f"""
Du är en intelligent assistent som hjälper en familj att hålla ordning på skolaktiviteter i Google Calendar för eleven: {first_name}.

INDATA FRÅN SKOLA OCH INFOMENTOR:
1. KALENDERHÄNDELSER (prov, läxor, deadlines, aktiviteter):
{json.dumps(clean_cal, ensure_ascii=False, indent=2)}

2. PLANERINGAR / UOL (studieplaner, provområden, kapitel och vad eleven ska plugga på):
{json.dumps(clean_uol, ensure_ascii=False, indent=2)}

3. MEDDELANDEN FRÅN LÄRARE (senaste meddelanden, information om inställda lektioner, sena starter, packlistor, avvikande tider/lokaler för prov):
{json.dumps(clean_msgs, ensure_ascii=False, indent=2)}

4. SCHEMA OCH LEKTIONSÖVERSIKT PER DAG:
{json.dumps(schedule_by_day or {}, ensure_ascii=False, indent=2)}

5. E-POST OCH VECKOBREV FRÅN SKOLAN (viktiga utskick och information om schemabrytande dagar, provgrupper, gymnasiemässa etc):
{json.dumps(clean_emails, ensure_ascii=False, indent=2)}

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
   För prov, förhör, läxor, inlämningar och ämneshändelser ska tider alltid sättas smart:
   a) LÄRARMEDDELANDEN MED SPECIFIK TID:
      Om ett lärarmeddelande anger en specifik tid eller lokal (t.ex. "NP i Ma hålls kl 08:30-10:00 i Aulan" eller "Provet startar kl 13:00 i sal No 1"):
      Använd ALLTID tiden och salen från meddelandet! Sätt `is_all_day: false`, `start` och `end` som ISO 8601 "YYYY-MM-DDTHH:MM:SS+01:00", och `location` till den angivna lokalen.
   b) KALENDERPOSTER MED BEFINTLIG TID FRÅN LÄRARE:
      Om en kalenderpost i InfoMentor redan har `start_time` och `end_time` (t.ex. start_time: "10:00", end_time: "12:00" som för glosläxa):
      BEHÅLL OCH ANVÄND ALLTID DEN TIDEN! Sätt `is_all_day: false`, `start` och `end` i ISO 8601 ("YYYY-MM-DDTHH:MM:SS+01:00").
      Gör ALDRIG om en sådan händelse till heldag (`is_all_day: true`) bara för att ämnesnamnet inte står på schemat den dagen!
   c) HÄNDELSER PÅ EN SPECIFIK DAG UTAN KLOCKSLAG (Hitta lektion i schemat):
      Om händelsen gäller en viss dag men saknar klockslag (start_time är tom eller 00:00):
      Leta upp ämnet i dagens lektionsschema under punkt 4:
      - Engelska / Glosläxa -> Engelska (EN)
      - Matematik -> Matematik (MA)
      - Fysik, Kemi, Biologi -> Naturorientering (NO)
      - Tyska -> Tyska (SPV TY)
      - Spanska -> Spanska (SPV SP)
      - Franska -> Franska (SPV FR)
      - Svenska / Läsläxa -> Svenska (SV)
      - Slöjd -> Slöjd (SL)
      - Historia, Geografi, Samhällskunskap, Religion -> Samhällsorientering (SO)
      - Teknik -> Teknik (TK)
      - Idrott och hälsa -> Idrott och hälsa (IDH)
      Om ämnet finns i dagens schema:
      Sätt `start` och `end` till exakt lektionens start- och sluttid i ISO 8601 ("YYYY-MM-DDTHH:MM:SS+01:00"), sätt `is_all_day: false` och `location` till salen!
      Om ämnet INTE finns i dagens schema:
      - Kontrollera om läraren som håller i ämnet har en annan lektion med eleven den dagen (t.ex. läraren har Svenska kl 13:10). Slot då in händelsen under det passet.
      - Om inte, kolla vilken dag i samma vecka eleven har lektion i ämnet och slotta in händelsen under det lektionspasset.
      - Prov och läxor ska ALDRIG ligga som heldagshändelse utan tid om det finns schemalagda lektioner i ämnet!
   d) VECKO-BASERADE PROV OCH LÄXOR (t.ex. "Prov i Tyska v. 49", "Provvecka", "Läxa v. 46"):
      SKAPA ALDRIG EN FLERDAGARS HELDAGSHÄNDELSE SOM STRÄCKER SIG ÖVER HELA VECKAN!
      Leta upp alla lektioner för ämnet under den angivna veckan i schemat (punkt 4):
      - Om informationen anger att provet sker under flera/båda veckans lektionsdagar (t.ex. "sker under båda lektionsdagarna" eller delprov):
        Skapa ett separat event för varje lektionstillfälle under veckan med `is_all_day: false`, och sätt tiden exakt till lektionens start- och sluttid:
        Exempel:
        - Tisdag: "{first_name}: Prov i Tyska (Del 1)" (start: "YYYY-MM-DDT08:00:00+01:00", end: "YYYY-MM-DDT08:40:00+01:00", infomentor_id: "uol_<id>_YYYY-MM-DD")
        - Torsdag: "{first_name}: Prov i Tyska (Del 2)" (start: "YYYY-MM-DDT08:00:00+01:00", end: "YYYY-MM-DDT08:40:00+01:00", infomentor_id: "uol_<id>_YYYY-MM-DD")
      - Om det är ett enskilt prov eller läxa för veckan:
        Placera det på ämnets första ordinarie lektionspass under veckan med `is_all_day: false`.
   e) VILKA HÄNDELSER FÅR VARA HELDAGAR (`is_all_day: true`)?
      Endast genuina temadagar eller gemensamma heldagar utan lektionskoppling:
      T.ex. "Älta skolas dag", "Studiedag", "Skolavslutning", "Nobellunch", "Lovdag", "Friluftsdag" (om ingen samlingstid angetts).
      Sätt då `is_all_day: true`, och `start` samt `end` till formatet "YYYY-MM-DD".

4. SCHEMAÄNDRINGAR OCH INSTÄLLDA LEKTIONER:
   - Granska lärarens meddelanden. Om ett meddelande nämner att en lektion är inställd, att skoldagen börjar senare (t.ex. kl 09:00 i stället för 08:15) eller slutar tidigare:
     Skapa en kalenderhändelse för den berörda dagen/tiden med varningsemblemet ⚠️ så föräldern ser det direkt!
     Sätt `event_type: "SCHEDULE_CHANGE"`.

5. EXKLUDERA RUTIN- OCH DROP-IN-AKTIVITETER:
   - Ta INTE med vanliga schemalagda drop-in-studiestugor eller läxhjälp (t.ex. "Studion öppen") eller allmän fritidspassning om det inte rör sig om en specifik prov/inlämningsdeadline eller avvikelse.

6. ID OCH SPÅRBARHET:
   - Ange ett unikt `infomentor_id` för varje händelse så att vi kan uppdatera den utan dubbletter:
     T.ex. "cal_<id>" om baserad på kalenderpost, "msg_<id>" om baserad på meddelande, eller "uol_<id>".

7. E-POST OCH VECKOBREV (PUNKT 5):
   - Granska e-postmeddelandena från skolan noga. De innehåller ofta avgörande information om schemabrytande dagar, provgrupper (t.ex. vem som gör muntliga NP vilken tid), information om nationella prov i svenska/engelska, och gymnasiemässa.
   - Använd e-posten för att:
     a) Förstå vad generella schemapass som "Schemabrytande: NP" faktiskt handlar om (t.ex. om ett mail anger att åk 9 genomför muntliga NP i svenska vecka 47 och samlas kl 08:15 i matsalen).
     b) Skapa eller korrigera provhändelser med rätt ämne, tider och grupper för eleven {first_name}.

8. FORMAT:
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
