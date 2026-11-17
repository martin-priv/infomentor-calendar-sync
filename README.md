# InfoMentor till Google Calendar Sync 🚀

Headless Python-tjänst som pollar **InfoMentor** för barnens aktiviteter, syntetiserar och berikar informationen med **Gemini AI**, och synkar direkt till en delad **Google Calendar**.

---

## 🌟 Funktioner

1. **Headless InfoMentor-inloggning:**
   - Loggar in automatiskt med användarnamn & lösenord via ASP.NET WebForm / OAuth-bridge till `hub.infomentor.se` (inget BankID krävs).
   - Växlar automatiskt mellan alla dina barn som finns registrerade på kontot.

2. **Multikälls-insamling & AI-syntes (Gemini):**
   - Samlar in data från 3 separata delar av InfoMentor:
     - `#/calendarv2` (Kalendern: datum, prov, aktiviteter)
     - `#/uolv2` (Planeringar: vad som ska pluggas på, kapitel, länkar)
     - `#/message` (Lärarmeddelanden: inställda lektioner, sena starter, packlistor)
   - Berikar provhändelser med kapitel, repetitionstips och glosor direkt i kalenderbeskrivningen.
   - Flaggor för inställda lektioner eller ändrade tider med varningssymboler (`⚠️`).

3. **Google Calendar API Integration:**
   - Ansluter via Service Account och skriver direkt till kalendern (**Barnens skola**).
   - **Idempotent:** Taggar händelser med unika `infomentor_id` via Google Calendars `extendedProperties`.
   - Inga dubbletter – befintliga händelser uppdateras sömlöst om schemat eller instruktionerna ändras.

---

## 📂 Filstruktur

- `infomentor_client.py` – InfoMentor scraping & API-klient (inloggning, barnväljare, kalender, planeringar, meddelanden).
- `ai_agent.py` – Gemini AI-agent som analyserar och kombinerar prov, studieguider och meddelanden till strukturerade Google Calendar-händelser.
- `google_calendar_client.py` – Google Calendar v3 API wrapper med upsert/idempotensstöd.
- `sync.py` – Huvudskriptet för synkning (stödjer `--dry-run`, `--child <namn>`, `--days <antal>`).
- `.env` – Autentiseringsuppgifter och inställningar (ignoreras av git).
- `service_account.json` – Google Cloud Service Account-nyckel (ignoreras av git).

---

## 🛠️ Användning

### Manuell körning (alla barn, 45 dagar framåt):
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
För att köra synkningen automatiskt varje morgon kl 06:00:
```bash
0 6 * * * cd /Users/martin/privat/home/infomentor-calendar-sync && /usr/bin/python3 sync.py >> /Users/martin/privat/home/infomentor-calendar-sync/sync.log 2>&1
```
