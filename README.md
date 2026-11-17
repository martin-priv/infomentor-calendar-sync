# InfoMentor till Google Calendar Sync 🚀

Headless Python-tjänst som pollar **InfoMentor** för barnens aktiviteter, scheman och prov, syntetiserar och berikar informationen med **AI (OpenAI eller Google Gemini)**, och synkar direkt till en dedikerad **Google Calendar**.

---

## 🌟 Funktioner

1. **Headless InfoMentor-inloggning:**
   - Loggar in automatiskt med användarnamn & lösenord via ASP.NET WebForm / OAuth-bridge till `hub.infomentor.se` (inget BankID krävs).
   - Växlar automatiskt mellan alla dina barn som finns registrerade på kontot.

2. **Multikälls-insamling & AI-syntes (OpenAI & Gemini-kompatibel):**
   - Samlar in data från 4 separata delar av InfoMentor:
     - `#/calendarv2` (Kalendern: datum, prov, aktiviteter)
     - `#/timetable` (Schemat: varje lektion, tider, salar och lärare)
     - `#/uolv2` (Planeringar: provområden, kapitel och vad eleven ska plugga på)
     - `#/message` (Lärarmeddelanden: inställda lektioner, sena starter, packlistor)
   - Stödjer **OpenAI** (`gpt-4o-mini`, `gpt-4o`) samt **Google Gemini** (`gemini-flash-latest`), eller lokala modeller via **Ollama / LiteLLM / Groq / DeepSeek** (alla OpenAI-kompatibla endpoints).
   - **Smart lektionsplacering:** Matchar prov och glosläxor mot lektionsschemat så att de hamnar på exakt rätt lektionspass istället för oprecisa heldagar. Lärarmeddelanden ges företräde för specifika tider och salar.

3. **Kompakta dagliga skoltider:**
   - Genererar en diskret, sammanfattande 15-minuters rad vid skolstarten (t.ex. `Isak: Skola 08:00–14:05`) med hela dagens schema i beskrivningen (ämnen, tider, salar, lärare), vilket håller kalendern ren och luftig.

4. **Exkluderingsfilter & Auto-Purge:**
   - Filtrerar bort schemalagda drop-in-studiestugor och rutiner (t.ex. `Studion öppen`) och raderar automatiskt eventuella matchande händelser som tidigare synkats till Google Calendar.

5. **Google Calendar API Integration:**
   - Ansluter via Service Account och skriver direkt till kalendern.
   - **Idempotent:** Taggar händelser med unika `infomentor_id` via Google Calendars `extendedProperties`.
   - Inga dubbletter – befintliga händelser uppdateras sömlöst om schemat eller instruktionerna ändras.

---

## 📂 Filstruktur

- `infomentor_client.py` – InfoMentor scraping & API-klient (inloggning, barnväljare, kalender, schema, planeringar, meddelanden).
- `ai_agent.py` – AI-agent med stöd för både OpenAI och Gemini som analyserar och kombinerar prov, studieguider och scheman.
- `google_calendar_client.py` – Google Calendar v3 API wrapper med upsert/idempotensstöd.
- `sync.py` – Huvudskriptet för synkning (stödjer `--dry-run`, `--child <namn>`, `--days <antal>`, `--exclude <mönster>`).
- `.env` – Autentiseringsuppgifter och inställningar (ignoreras av git).
- `service_account.json` – Google Cloud Service Account-nyckel (ignoreras av git).

---

## ⚙️ Konfiguration (.env)

Kopiera `.env.example` till `.env` och fyll i dina uppgifter:

```env
# InfoMentor Credentials
INFOMENTOR_USERNAME=ditt_användarnamn
INFOMENTOR_PASSWORD=ditt_lösenord

# Google Calendar
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
GOOGLE_CALENDAR_ID=din_kalender_id@group.calendar.google.com

# Välj AI-leverantör (OpenAI eller Gemini)
# Alternativ A: OpenAI / OpenAI-kompatibel (Ollama, Groq, LiteLLM)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
# OPENAI_BASE_URL=https://api.openai.com/v1  # eller t.ex. http://localhost:11434/v1

# Alternativ B: Google Gemini
# GEMINI_API_KEY=din_gemini_nyckel
# GEMINI_MODEL=models/gemini-flash-latest

# Inställningar
SYNC_DAYS_AHEAD=14
EXCLUDE_PATTERNS=Studion öppen
SYNC_SCHOOL_HOURS=true
```

---

## 🛠️ Användning

### Manuell körning (alla barn, 14 dagar framåt):
```bash
python3 sync.py
```

### Dry-run (se vad som skulle synkas utan att skriva till Google Calendar):
```bash
python3 sync.py --dry-run
```

### Synka ett specifikt barn:
```bash
python3 sync.py --child "Förnamn"
```

### Automatisk schemaläggning (Cron):
För att köra synkningen automatiskt varje vardagsmorgon kl 06:30 och eftermiddag kl 16:30:
```bash
30 6,16 * * 1-5 cd /home/användare/infomentor-sync && /usr/bin/python3 -u sync.py >> /home/användare/logs/infomentor_sync.log 2>&1
0 19 * * 0 cd /home/användare/infomentor-sync && /usr/bin/python3 -u sync.py >> /home/användare/logs/infomentor_sync.log 2>&1
```
