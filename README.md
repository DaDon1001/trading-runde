# Trading-Runde: Multi-Agent-Chat mit Grok

Ein Chat im Browser, in dem mehrere Grok-Agenten mit unterschiedlichen Rollen gemeinsam mit dir über Trading diskutieren. Jeder Agent liest, was die anderen geschrieben haben, und reagiert darauf. Über dem Chat liegen Kurs und Chart des gewählten Symbols, und dieselben Zahlen bekommen die Agenten als Kontext.

**Die Agenten:**
- 📈 **Chartist** – technische Analyse
- 🌍 **Makro** – Fundamentaldaten, Zinsen, News
- 🪙 **Krypto** – Zyklen, Funding, Spot gegen Perps
- 🎯 **Optionen** – implizite Volatilität, Greeks, Strukturen
- 🌊 **Flow** – Positionierung, Open Interest, Orderflow
- 📅 **Katalysator** – anstehende Termine (Zahlen, Notenbank, Verfall)
- 🛡️ **Risiko** – Positionsgröße, Stop-Loss, Chance/Risiko
- 😈 **Skeptiker** – sucht immer die Gegenposition
- 🧭 **Moderator** – zieht auf Knopfdruck ein Fazit

**So läuft's:** Du wählst ein Symbol, siehst Kurs und Chart und schreibst eine Frage. Alle aktiven Agenten antworten nacheinander. Mit **„Weiter diskutieren“** gibt's eine weitere Runde ohne dass du etwas schreibst, mit **„Fazit ziehen“** fasst der Moderator zusammen. In der Seitenleiste schaltest du Agenten an und aus.

## Kurse und Chart
Über dem Chat wählst du ein Symbol oder tippst ein Yahoo-Kürzel (`NVDA`, `SAP.DE`, `^GDAXI`, `BTC-USD`) und einen Zeitraum. Die App zeigt den letzten Kurs, die Veränderung und einen Chart. Dieselben Zahlen stehen im Prompt, damit die Runde nicht raten muss.

Die Daten kommen ohne API-Key von **Yahoo Finance** (Tagesdaten). `BTC`, `ETH` und `SOL` werden zu den USD-Paaren, `DAX` zum Index `^GDAXI`. Ein erfolgreicher Abruf wird etwa zwei Minuten wiederverwendet, ein Fehlschlag nicht. **Aktualisieren** holt sofort neu. Unter dem Chart stehen Datenalter und Marktstatus. Aktien und Indizes sind nur die reguläre Handelszeit; bei Krypto gilt die Tagesveränderung seit 00:00 UTC. Fehlt bei Yahoo der heutige Schluss, ergänzt die App den laufenden Tag aus dem letzten Kurs. Klappt der Abruf nicht (falsches Symbol, kein Netz, Yahoo kurz nicht erreichbar), erscheint eine Warnung und die Runde diskutiert ohne Kurse weiter.

Mit `MOCK=1` gibt es keine Live-Kurse und keine Grok-Aufrufe: der Chart zeigt stabile Beispieldaten, die Agenten antworten mit Platzhaltern. Dafür brauchst du weder Key noch Netz.

## Einrichtung (einmalig, ca. 10 Minuten)
1. **Python installieren** (3.10 oder neuer): https://www.python.org/downloads/
2. **xAI-API-Key holen:** auf https://console.x.ai anmelden, Guthaben aufladen, unter „API Keys“ einen Key erstellen.
3. **Diesen Ordner herunterladen**, im Terminal hineinwechseln und ausführen:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env             # Windows: copy .env.example .env
   ```
4. In der Datei `.env` den Platzhalter `<DEIN-KEY>` durch deinen Key ersetzen. Optional steht dort auch `TRADER_NAME` (Standard: Daniel).
5. Starten:
   ```bash
   streamlit run app.py
   ```
   Der Chat öffnet sich im Browser unter http://localhost:8501.

### Windows (PowerShell)
```powershell
cd $HOME\trading-runde              # Ordner mit app.py, agents.py, requirements.txt
python -m venv .venv
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # nur beim ersten Mal, mit J bestätigen
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Set-Content .env "XAI_API_KEY=<DEIN-KEY>"
streamlit run app.py
```

Ohne Key ausprobieren: in `.env` `MOCK=1` setzen, dann antworten die Agenten mit Platzhaltern und der Chart mit Beispieldaten. Den echten Kurs siehst du auch ohne Key; nur die Diskussion braucht ihn.

## Anpassen
Rollen stehen in `agents.py` (`AGENTS` und `MODERATOR`). Du kannst Namen, Emoji und Rolle ändern. Ein neuer Eintrag in `AGENTS` ist in der Seitenleiste automatisch anschaltbar. Der gemeinsame Ton steht in `COMMON`; die Platzhalter `{trader}` und `{research}` am besten stehen lassen.

## Arbeiten mit Claude Code und Grok Bot

Am Repo arbeiten zwei Assistenten:

- **Claude Code** lokal auf dem Rechner, direkt im Checkout.
- **Grok Bot** gibt größere Änderungen an Cursor-Cloud-Agents weiter. Die arbeiten auf einem eigenen Branch und öffnen einen Pull Request.

So geraten sie sich nicht in die Quere:

1. In Claude Code zuerst auf `main` wechseln und `git pull`, dann erst anfangen.
2. Größere Änderungen über den Cloud Agent laufen lassen und per Pull Request holen, nicht dieselben Dateien parallel lokal umschreiben.
3. Den PR ansehen und mergen, danach lokal wieder `git pull`, und erst dann in Claude Code weitermachen.

## Hinweise
- Kosten: Jede Agenten-Antwort ist ein API-Aufruf. Eine Runde mit allen 8 Agenten = 8 Aufrufe. Wen du nicht brauchst, schaltest du links aus.
- **Recherche:** Die Diskussions-Agenten suchen selbst im Web und auf X nach News und Stimmung und hängen ihre Quellen als Links an die Anzeige. Diese Linklisten gehen nicht erneut in den Prompt. Der Moderator fasst nur zusammen und recherchiert nicht. In der Seitenleiste kannst du Web- und X-Suche einzeln abschalten (spart Kosten). Kurszahlen aus dem Chart haben Vorrang vor selbst gesuchten Kursen.
- Alles ist Meinung von KI-Modellen, **keine Finanzberatung**.
