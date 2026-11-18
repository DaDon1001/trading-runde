# Trading-Runde: Multi-Agent-Chat mit Grok

Ein Chat im Browser, in dem mehrere Grok-Agenten mit unterschiedlichen Rollen gemeinsam mit dir über Trading diskutieren. Jeder Agent liest, was die anderen geschrieben haben, und reagiert darauf.

**Die Agenten:**
- 📈 **Chartist** – technische Analyse
- 🌍 **Makro** – Fundamentaldaten, Zinsen, News
- 🛡️ **Risiko** – Positionsgröße, Stop-Loss, Chance/Risiko
- 😈 **Skeptiker** – sucht immer die Gegenposition
- 🧭 **Moderator** – zieht auf Knopfdruck ein Fazit

**So läuft's:** Du schreibst eine Frage → alle aktiven Agenten antworten nacheinander. Mit **„Weiter diskutieren“** gibt's eine weitere Runde ohne dass du etwas schreibst, mit **„Fazit ziehen“** fasst der Moderator zusammen. In der Seitenleiste schaltest du Agenten an und aus.

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
4. In der Datei `.env` deinen Key bei `XAI_API_KEY=` eintragen.
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
Set-Content .env "XAI_API_KEY=xai-dein-echter-key"
streamlit run app.py
```

Ohne Key ausprobieren: in `.env` `MOCK=1` setzen, dann antworten die Agenten mit Platzhaltern.

## Anpassen
Rollen stehen in `agents.py`. Du kannst dort Agenten umbenennen, ihre Rolle umschreiben oder neue hinzufügen (z. B. „Krypto-Spezialist“ oder „Optionen-Trader“).

## Hinweise
- Kosten: Jede Agenten-Antwort ist ein API-Aufruf. Eine Runde mit 4 Agenten = 4 Aufrufe.
- **Recherche:** Die Agenten suchen selbst im Web und auf X nach News, Kursen und Stimmung und hängen ihre Quellen als Links an. In der Seitenleiste kannst du Web- und X-Suche einzeln abschalten (spart Kosten).
- Alles ist Meinung von KI-Modellen, **keine Finanzberatung**.
