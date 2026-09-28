# LD Maschinenlaufzeit

Erfasst laufend den Zustand der Heidenhain-Steuerungen (iTNC 530) der DMG-Fräsen und speichert ihn für
spätere Laufzeitauswertungen:

- **Live:** Läuft die Maschine? Welches Programm, seit wann, welcher Satz, welches Werkzeug, welcher Override?
- **Auswertung:** Laufzeit und Auslastung je Tag und Maschine, Zeitleiste, Programme mit Stückzeiten,
  CSV-Export für Excel.
- **Aufträge:** Aufträge aus dem Programmnamen (`26-21055-01-01`) automatisch anlegen, Zeit je Auftrag,
  Aufspannung und Programm, Ø Bearbeitungszeit je Teil.
- **Werkzeugauswertung:** Einsatzzeit je Werkzeug (T1–T1000) und Maschine, Maximallaufzeit mit
  Vorwarnung und roter Meldung, Zurücksetzen beim Werkzeugwechsel, Standzeit-Historie.
- **Konfiguration:** Maschinen mit Name, IP, Bild, Standort/Notiz und Reihenfolge anlegen, Verbindung
  testen; zeigt die laufende Version und das Änderungsprotokoll.

Die Anbindung läuft über das LSV2-Protokoll (TCP 19000) mit der Open-Source-Bibliothek
[pyLSV2](https://github.com/drunsinn/pyLSV2), also über dasselbe Protokoll wie TNCremo und das
Heidenhain-RemoTools-SDK, aber ohne Windows-COM-Komponente.
Es werden **ausschließlich lesende** Abfragen gestellt.

## Portable Version für den Windows-PC im Betrieb

Die portable Version ist ein Ordner mit eigenem Python. Sie läuft ohne Installation, ohne Adminrechte
und ohne Internetzugang. Voraussetzung ist Windows 10/11 (64 Bit).

**Einrichten:**

1. ZIP `LD-Maschinenlaufzeit-<Version>.zip` auf den PC kopieren und entpacken, am besten nach
   `C:\LD-Maschinenlaufzeit`. Der Ordner muss lokal liegen; ein Netzlaufwerk oder ein Cloud-Ordner
   funktioniert nicht, weil die Datenbank dort beschädigt werden kann.
2. `start.cmd` doppelklicken. Der Browser öffnet sich mit der Oberfläche.
3. Im Tab **Konfiguration** die Maschinen anlegen und je Maschine **Verbindung testen**.
4. Für den Dauerbetrieb `autostart-einrichten.cmd` ausführen (fragt nach Adminrechten):
   - Die App startet dann beim Hochfahren im Hintergrund, auch ohne Anmeldung, und wird nach einem
     Absturz neu gestartet.
   - Der Port der Oberfläche wird in der Windows-Firewall freigegeben.
   - Andere PCs im Netz erreichen die Oberfläche unter `http://<PC-Name>:8000`.

| Datei | Zweck |
|---|---|
| `start.cmd` | Starten mit Konsolenfenster; das Fenster zu schließen beendet die Erfassung |
| `start-simulation.cmd` | Ausprobieren mit simulierten Maschinen (eigene Demo-Datenbank) |
| `autostart-einrichten.cmd` | Dauerbetrieb im Hintergrund einrichten (Aufgabenplanung + Firewall) |
| `autostart-entfernen.cmd` | Hintergrundbetrieb beenden und Autostart entfernen; die Daten bleiben |
| `config.yaml` | Port, Abfrageintervall, Zeitzone (nach Änderung neu starten) |
| `data\` | Datenbank, Maschinenbilder, Log-Dateien (`data\logs\laufzeit.log`) |

**Update auf eine neue Version:**

1. `autostart-entfernen.cmd` ausführen, falls der Autostart eingerichtet ist.
2. Die neue ZIP über den alten Ordner entpacken. Der Ordner `data\` bleibt dabei erhalten, und die
   Maschinen stehen in der Datenbank, nicht in der `config.yaml`.
3. `autostart-einrichten.cmd` erneut ausführen.

**Sicherung:** den Ordner `data\` kopieren, am besten bei beendeter App.

**Portable Version bauen** (auf dem Entwicklungsrechner, lädt einmalig Python von python.org):

```bat
powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
```

Das Ergebnis liegt in `C:\Users\<Name>\ld-mainmachine\dist\`: der Ordner (ca. 45 MB) und die ZIP
(ca. 20 MB).

## Maschinen einrichten (Tab „Konfiguration“)

- **+ Maschine hinzufügen:** Name, IP-Adresse der Steuerung, Port (Standard 19000), Standort/Notiz und
  Bild. Große Fotos werden beim Hochladen automatisch verkleinert.
- **Verbindung testen:** prüft rein lesend Schritt für Schritt: Netzwerk → LSV2-Anmeldung und
  Steuerungsversion → Option 18 → Statusabfrage, danach (optional) angewähltes Programm und
  Werkzeugtabelle lesen. Bei Fehlern nennt der Test mögliche Ursachen. Der Test funktioniert auch
  schon vor dem Speichern.
- **Prüfadresse am Standort** (optional), z. B. der Router vor Ort: Antworten weder die Steuerung
  noch diese Adresse, ist die Verbindung zum Standort weg (etwa das VPN). Die Karte zeigt dann
  „Standort nicht erreichbar“, und die Zeit wird als „Keine Daten“ gebucht statt als „Offline“.
  Ohne Prüfadresse gilt jede nicht erreichbare Steuerung als „Offline“.
- **▲ ▼:** Reihenfolge der Karten auf der Live-Seite.
- **Entfernen:** Die Maschine wird nicht mehr erfasst. Ihre bisher erfassten Daten bleiben in der
  Datenbank erhalten.

Änderungen gelten sofort. Ändert sich die IP, wird neu verbunden.

Voraussetzungen an jeder Steuerung:

- **Option 18 (HEIDENHAIN DNC)** ist freigeschaltet. Ohne sie gibt es keinen Programmstatus.
- LSV2 ist in der Firewall der Steuerung für den PC erlaubt (MOD → Firewall).
- Der PC erreicht die Steuerung auf **TCP 19000**.

## Restlaufzeit und Satzanzahl

- **Satzanzahl:** Das angewählte NC-Programm wird einmal pro Lauf rein lesend von der Steuerung
  gelesen, über eine eigene Verbindung wie bei TNCremo. Die Live-Karte zeigt dann „Satz 340 / 2.705“.
  - Klartext-Programme (`.H`): Maßgeblich ist die Nummer des Satzes `END PGM`.
  - DIN/ISO-Programme (`.I`): Gezählt werden die Programmzeilen.
  - Neu geladen wird nur, wenn sich Größe oder Änderungsdatum der Datei ändern.
  - Programme über 20 MB werden übersprungen. Das lässt sich über `program_max_mb` und
    `fetch_programs` in der `config.yaml` einstellen.
- **Restlaufzeit:** Die Live-Karte zeigt Fortschritt, Restzeit und voraussichtliches Ende. Welches
  Verfahren verwendet wurde, steht darunter. Es greift das erste passende:
  1. **Satzverlauf früherer Läufe** (genauestes Verfahren): Bei jedem Lauf wird mitgeschrieben,
     wann welcher Satz erreicht war. Beim nächsten Lauf desselben Programms zeigt die aktuelle
     Satznummer, welcher Anteil der Laufzeit bei früheren Läufen an dieser Stelle schon vergangen
     war. Das funktioniert auch, wenn einzelne Sätze sehr lange dauern. Läuft die Maschine
     langsamer oder schneller als sonst (z. B. Override), wird die Prognose nachgeführt.
  2. **Laufzeit früherer Läufe:** typische Laufzeit (Median der letzten 5 Läufe) minus bisherige
     Laufzeit.
  3. **Erster Lauf eines Programms:** grobe Hochrechnung aus Satznummer und Satzanzahl.
- Alle Prognosen rechnen mit reiner Laufzeit. Künftige NC-Stopps oder Störungen kann niemand
  vorhersehen; während eines Stopps zeigt die Karte „pausiert“ und das Ende verschiebt sich.
- **An der echten Maschine prüfen:** Der Verbindungstest liest im Schritt „Programm lesen“ das
  angewählte Programm und zeigt Satzanzahl und aktuellen Satz. Die Satznummer sollte der Anzeige
  an der Steuerung entsprechen.

## Aufträge (Tab „Aufträge“)

Programme nach dem Schema **`JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM`** werden automatisch einem Auftrag
zugeordnet, z. B. `26-21055-01-01`:

| Teil | Bedeutung |
|---|---|
| `26` | Jahr (2026) |
| `21055` | Auftragsnummer, 4- oder 5-stellig |
| `01` | Aufspannung (1 = Spannung 1, 2 = Spannung 2 …) |
| `01` | Programmnummer, fortlaufend |

- Taucht ein Programm mit einer neuen Auftragsnummer an einer Maschine auf, wird der Auftrag
  **automatisch angelegt**. Schlüssel ist Jahr + Nummer (`26-21055`), falls eine Nummer in einem
  späteren Jahr wieder vorkommt. Zusätze nach der Programmnummer (`26-21055-01-01_Schlichten.H`)
  und Unterstriche statt Bindestriche werden ebenfalls erkannt.
- **Gezählt wird die Zeit der Programmdurchläufe:** Laufzeit sowie Stopps und Fehler innerhalb der
  Läufe. Zeit, in der ein Programm nur angewählt ist, zählt nicht, sonst würde ein übers Wochenende
  angewähltes Programm dem Auftrag Tage gutschreiben.
- **Liste:** Status (läuft gerade / offen / abgeschlossen), Aufspannungen, Programme, Laufzeit,
  Stopps, fertige Läufe, Maschinen, letzte Aktivität. Suche nach Nummer oder Bezeichnung.
- **Detail:**
  - Bezeichnung (z. B. Kunde, Bauteil), Abschließen / Wieder öffnen.
  - Welche Maschine den Auftrag gerade fährt, mit Restlaufzeit.
  - Je Aufspannung die Programme mit Läufen, Laufzeit und **Ø Laufzeit je Teil**.
  - **Ø Bearbeitungszeit je Teil:** die Summe der Ø-Laufzeiten aller Programme über alle
    Aufspannungen, gerechnet nur aus vollständig erfassten, fertigen Läufen.
  - Laufzeit je Tag, alle Läufe, CSV-Export.
- Läuft ein abgeschlossener Auftrag wieder an, wird er automatisch wieder geöffnet.
- Daten, die vor der Auftragsauswertung erfasst wurden, werden beim Start einmalig nachgetragen.

## Werkzeugauswertung (Tab „Werkzeugauswertung“)

- **Werkzeuge werden automatisch angelegt**, sobald ein Werkzeug an einer Maschine zum ersten Mal in
  der Spindel ist. Mit **+ Werkzeug anlegen** lässt sich ein Werkzeug (T1–T1000) auch vorab anlegen,
  um die Maximallaufzeit schon vor dem ersten Einsatz einzutragen.
- **Je Maschine getrennt:** T100 an der einen und T100 an der anderen Maschine sind zwei Werkzeuge
  mit eigener Zeit, eigenem Limit und eigenem Zurücksetzen.
- **Einsatzzeit** = Zeit, in der ein Programm läuft (Zustand „Läuft“) und das Werkzeug in der
  Spindel ist. Stopps, Störungen, Einrichten und stehende Programme zählen nicht. Die Werkzeugnummer
  kommt alle 2 s von der Steuerung. Ein Wechsel wird also auf etwa 2 s genau erfasst.
- **Maximallaufzeit** (Bearbeiten → Stunden, z. B. `100` oder `2,5`): Der Balken zeigt, wie viel
  davon verbraucht ist. Neue Werkzeuge bekommen automatisch **100 h**. Das gilt auch beim Anlegen von
  Hand; leer lassen heißt kein Limit.
  - Ab 90 % erscheint eine gelbe **Vorwarnung**.
  - Ab 100 % erscheint **„Über Limit“** in Rot: als Meldung oben auf der Seite, als rote Zahl am
    Reiter und auf der Live-Karte, solange das Werkzeug in der Spindel ist.
- **Zurücksetzen** nach dem Einspannen eines neuen Werkzeugs: Die Einsatzzeit beginnt wieder bei 0.
  Limit und Notiz bleiben. Der alte Stand kommt als **Standzeit** in die Historie (Bearbeiten →
  „Standzeiten bisher“, mit Ø Standzeit).
- **Entfernen** löscht den Eintrag samt Historie. Taucht das Werkzeug wieder auf, wird es neu
  angelegt und zählt ab dann.
- **Name aus der Werkzeugtabelle:** Die Abfrage „Werkzeug in der Spindel“ liefert nur die Nummer.
  Den Namen liest die App aus der Werkzeugtabelle `TNC:\TOOL.T` der Steuerung (Spalte `NAME`).
  - Das geschieht nur lesend über eine zweite Verbindung, wie bei den Programmdateien.
  - Nach dem Start und dann alle 10 Minuten prüft die App, ob sich die Tabelle geändert hat, und
    liest sie nur dann neu ein.
  - Taucht ein Werkzeug ohne bekannten Namen auf, prüft sie schon nach einer Minute wieder.
  - Indizierte Werkzeuge (T5.1 …) zählen zur Nummer des Hauptwerkzeugs.
- **Notiz:** eigene Bezeichnung, z. B. „VHM D10, Hersteller X“, unter Bearbeiten.
- Filter nach Maschine und Status, Suche nach T-Nummer, Name oder Notiz, CSV-Export.

Ob beides an der iTNC 530 funktioniert, zeigt der Verbindungstest:

- Unter „Statusabfrage“ steht `Werkzeug T12` oder „Werkzeug: keine Angabe“.
- Unter „Werkzeugtabelle lesen“ steht z. B. „245 Werkzeuge mit Namen, in der Spindel: T12
  FRAESER_D16“.

## Begriffe der Auswertung

| Zustand | Bedeutung (Heidenhain-Programmstatus) |
|---|---|
| **Läuft** | Programm gestartet (`STARTED`) |
| **Gestoppt** | NC-Stopp bzw. Programm unterbrochen (`STOPPED`, `INTERRUPTED`) |
| **Fehler** | Programm mit Fehler angehalten (`ERROR`) |
| **Bereit** | kein Programm aktiv (`IDLE`, `FINISHED`, `CANCELLED`, `ERROR_CLEARED`) |
| **Offline** | Steuerung nicht erreichbar (Maschine aus, Netzwerk weg) |
| **Keine Daten** | das Tool lief nicht oder der Standort war nicht erreichbar (Prüfadresse); die Zeit wird nicht geschätzt |

- **Programmdurchlauf:** beginnt bei „Läuft“ und bleibt über Stopps und Fehler offen. Er endet, wenn
  die Maschine „Bereit“ meldet oder ein anderes Programm läuft. Kurze Verbindungsabbrüche beenden
  keinen Lauf. Ergebnis: fertig, abgebrochen, Fehler oder unterbrochen.
- **„Start beobachtet“ / ≥:** Lief ein Programm schon, als die Erfassung begann, ist der echte Start
  unbekannt. Solche Läufe zählen nicht in die Ø-Stückzeiten.
- **Auslastung:** Laufzeit geteilt durch die Einschaltzeit (Zeit, in der die Steuerung erreichbar war).
  **Auslastung Kalenderzeit:** Laufzeit geteilt durch den ganzen Zeitraum.
- **Ø Laufzeit je Teil:** reine Zeit im Zustand „Läuft“ je fertigem Lauf. **Ø Durchlauf je Teil:**
  Start bis Ende, inklusive Stopps.

Das Zustandsmapping steht an einer Stelle in [app/state.py](app/state.py). Falls die echte Steuerung
andere Werte liefert, wird nur dort angepasst.

## Daten

- **Datenordner:** portabel `<Ordner>\data\`; in der Entwicklung `C:\Users\<Name>\ld-mainmachine\data\`
  (bewusst außerhalb von Nextcloud). Abweichend einstellbar über `LDM_DATA_DIR`, `DB_PATH` oder
  `db_path` in der `config.yaml`.
- **`data.db`:** echte Daten. **`demo.db`:** nur für die Simulation; die beiden werden nie gemischt.
- In der Datenbank stehen die Maschinen (`machines`), Zustandsabschnitte (`state_intervals`),
  Programmdurchläufe (`program_runs`) und Ereignisse (`events`: Werkzeugwechsel, NC-Fehlermeldungen,
  Verbindung auf/ab, Konfigurationsänderungen).

## API

| Endpunkt | Inhalt |
|---|---|
| `GET /api/machines` | Live-Status aller Maschinen |
| `GET /api/stats?from=&to=` | Zeiten je Zustand, Tag und Programm, Auslastung |
| `GET /api/machines/{id}/timeline?from=&to=` | Zustandsabschnitte für die Zeitleiste |
| `GET /api/runs?machine=&from=&to=` | Programmdurchläufe |
| `GET /api/events?machine=&from=&to=` | Ereignisse |
| `GET /api/export.csv?kind=intervals\|runs&from=&to=` | CSV für Excel (`;`, Dezimalkomma) |
| `GET /api/orders?status=`, `GET/PUT /api/orders/{key}` | Aufträge (Liste, Detail, Bezeichnung/Status) |
| `GET /api/orders/{key}/export.csv` | Läufe eines Auftrags als CSV |
| `GET/POST/PUT/DELETE /api/config/...` | Konfiguration: Maschinen, Bild, Reihenfolge, Verbindungstest |
| `GET /api/tools`, `POST /api/tools` | Werkzeuge (Liste, von Hand anlegen) |
| `PUT/DELETE /api/tools/{maschine}/{nr}`, `POST …/reset` | Limit/Notiz, Entfernen, Zurücksetzen |
| `GET /api/tools/export.csv` | Werkzeugliste als CSV |
| `GET /api/version` | Versionsnummer, Build-Kennung, Änderungsprotokoll |

`from`/`to` sind Unix-Sekunden oder ISO-Zeitpunkte. Ohne Angabe gilt: heute 0 Uhr bis jetzt.
Interaktive Doku: <http://localhost:8000/docs>.

## Unraid (Docker über die Weboberfläche)

Das Image wird bei jedem Push auf `main` von GitHub Actions gebaut und liegt unter
`ghcr.io/bademeister89/ld-maschinenlaufzeit:latest`. Auf Unraid ist kein Terminal nötig.

### Container anlegen (Schritt für Schritt, geprüft mit Unraid 6.12.15)

1. **Freien Port wählen:** Reiter **DOCKER**, Spalte *Port-Zuordnungen*. Ist `8000` schon belegt
   (z. B. durch Paperless), einen anderen Host-Port nehmen, z. B. `8030`.
2. Reiter **DOCKER** → ganz unten **Container hinzufügen** (*Add Container*).
3. Oben rechts **Erweiterte Ansicht** (*Advanced View*) einschalten, sonst fehlt das Feld „WebUI“.
4. Oberes Formular:

   | Feld | Eintrag |
   |---|---|
   | Vorlage (*Template*) | leer lassen |
   | Name | `ld-maschinenlaufzeit` |
   | Repository | `ghcr.io/bademeister89/ld-maschinenlaufzeit:latest` |
   | WebUI | `http://[IP]:[PORT:8000]/` (bleibt so, auch bei anderem Host-Port) |
   | Netzwerktyp (*Network Type*) | `Bridge` (mit VPN-Tunnel siehe unten) |

5. **Port:** unten „Einen weiteren Pfad, Port, Variable, Label oder Gerät hinzufügen“ →
   Typ **Port**, Name `Oberfläche`, Container-Port `8000`, Host-Port `8000` bzw. `8030`, TCP → **Hinzufügen**.
6. **Pfad:** gleicher Link → Typ **Pfad**, Name `Daten`, Container-Pfad `/data`,
   Host-Pfad `/mnt/user/appdata/ld-maschinenlaufzeit`, Read/Write → **Hinzufügen**.
7. **Variablen:** gleicher Link, je Typ **Variable**:

   | Name | Schlüssel | Wert |
   |---|---|---|
   | Zeitzone | `TZ` | `Europe/Berlin` |
   | Simulation | `SIMULATE` | `1` zum Ausprobieren, `0` für echten Betrieb |
   | PUID | `PUID` | `99` |

8. **Anwenden** (*Apply*), warten bis das Image geladen ist, **Fertig**.
9. **Prüfen:** Container steht auf *started* → Icon anklicken → **WebUI**. Im Simulationsmodus
   erscheinen zwei simulierte Maschinen. Unter Icon → **Protokolle** steht
   „Start (Version …, Build …): 2 Maschine(n), SIMULATION“.

**Auf echten Betrieb umstellen:** Icon → **Bearbeiten**, `SIMULATE` = `0`, **Anwenden**. Die Simulation
schreibt in eine eigene `demo.db`; echte Daten landen in `data.db`.

**Alternative mit Vorlage:** `unraid/ld-maschinenlaufzeit.xml` über die Flash-Freigabe nach
`config\plugins\dockerMan\templates-user\` kopieren, dann unter *Vorlage* auswählen (nicht geprüft).

- **Update:** Nach jedem Push baut GitHub ein neues Image; im Reiter DOCKER erscheint beim Container
  „Update“ (ggf. unten *Nach Updates suchen*). Ein Klick aktualisiert – kein Terminal, kein Script.
  Daten und Maschinen bleiben beim Update erhalten. Welche Version läuft, steht oben neben dem
  Namen (z. B. `v1.2.0`), siehe [Versionen](#versionen).
- **Version festhalten oder zurück:** Icon → **Bearbeiten**, bei *Repository* statt `:latest` die
  Versionsnummer eintragen, z. B. `ghcr.io/bademeister89/ld-maschinenlaufzeit:1.2.0` (feste Images
  gibt es ab Version 1.2.0), **Anwenden**.
  Zurück zu automatischen Updates mit `:latest`. Vor einem Wechsel auf eine ältere Version
  `appdata/ld-maschinenlaufzeit` sichern.
- **Sicherung:** `appdata/ld-maschinenlaufzeit` sichern, z. B. mit dem Plugin „Appdata Backup“.
- **Keine Anmeldung:** Die Oberfläche hat keinen Login. Nicht ins Internet freigeben;
  Fernzugriff z. B. über Tailscale.

### Maschinennetz über WireGuard (nur dieser Container)

Die Maschinen stehen in einem anderen Netz (Maschinenstandort) als der Unraid-Server (Heimnetz).
Nur dieser Container bekommt einen WireGuard-Tunnel zur FRITZ!Box am Maschinenstandort. Das Heimnetz und die übrigen Container bleiben vom Maschinennetz getrennt.

1. **FRITZ!Box am Maschinenstandort** (ab FRITZ!OS 7.50): *Internet → Freigaben → VPN (WireGuard)*
   → Verbindung hinzufügen → **Einzelgerät verbinden**, Name z. B. `unraid-laufzeit`.
   Die Konfigurationsdatei herunterladen. Die IP, die die FRITZ!Box dem Tunnel gibt
   (steht in der Datei unter `Address`), notieren.
2. **Unraid:** *Settings → VPN Manager → Import Config* mit dieser Datei. Als Verbindungstyp
   **„VPN tunneled access for docker“** wählen und den Tunnel aktivieren.
3. **Container bearbeiten:** *Network Type* = **Custom: wg0** (bzw. die Nummer des Tunnels).
4. **Jede Steuerung:** in der Firewall (MOD → Firewall) LSV2 für die Tunnel-IP aus Schritt 1
   freigeben.
5. Oberfläche öffnen → **Konfiguration** → je Maschine **Verbindung testen**.

Ist die Oberfläche nach Schritt 3 aus dem Heimnetz nicht mehr erreichbar, zuerst unter
*Settings → Docker* (erweiterte Ansicht) „Host access to custom networks“ prüfen. Hilft das
nicht, ist der Container **Gluetun** (Community Apps) die Alternative: Gluetun baut den
WireGuard-Tunnel auf (Provider „custom“, Werte aus der FRITZ!Box-Datei), dieser Container
bekommt *Network Type* = `Container: gluetun`, und Port 8000 wird bei Gluetun veröffentlicht.

**Tunnel-Ausfall:** Damit ein Ausfall des Tunnels nicht als „Offline“ (Maschine aus) gebucht wird,
im Tab Konfiguration je Maschine die **Prüfadresse** eintragen, z. B. die IP der FRITZ!Box am
Standort. Antwortet auch sie nicht, zeigt die Karte „Standort nicht erreichbar“, und die Zeit
erscheint in der Auswertung als „Keine Daten“.

### Ohne Unraid (docker compose)

```bash
docker compose up -d
```

Daten liegen in `./data`. Die `docker-compose.yml` verwendet das fertige Image; zum lokalen
Bauen dort `build: .` eintragen.

## Versionen

Die Versionsnummer folgt dem Schema `MAJOR.MINOR.PATCH`: MINOR für neue Funktionen, PATCH für
Fehlerbehebungen, MAJOR für Umstellungen, bei denen man selbst etwas anpassen muss.

- **Anzeige:** auf jeder Seite oben neben dem Namen (`v1.2.0`). Ein Klick darauf öffnet im Tab
  Konfiguration den Abschnitt **Versionen** mit allen Änderungen. Unter **Allgemein** steht dort
  zusätzlich die **Build-Kennung** (Build-Datum und Commit, z. B. `2026-09-28-1a2b3c4`). Sie ändert
  sich mit jedem Image, auch ohne neue Versionsnummer.
- **Änderungsprotokoll:** [`CHANGELOG.md`](CHANGELOG.md).
- **Docker-Images:** `latest` folgt dem Branch `main`; jede freigegebene Version gibt es zusätzlich
  fest als `:<Version>` (z. B. `:1.2.0`) und `:<MAJOR.MINOR>` (z. B. `:1.2`).
- **Portable Version:** Die ZIP heißt `LD-Maschinenlaufzeit-<Version>.zip`.

**Neue Version freigeben** (Entwicklung):

1. Nummer in `app/__init__.py` (`__version__`) erhöhen und oben in `CHANGELOG.md` beschreiben.
   Ein Test schlägt fehl, wenn beides nicht zusammenpasst.
2. Committen, dann Tag setzen und beides pushen:

   ```bash
   git tag v1.2.0
   git push origin main v1.2.0
   ```

   GitHub Actions baut `latest` (aus `main`) und `1.2.0`/`1.2` (aus dem Tag). Passt der Tag nicht
   zur Nummer in `app/__init__.py`, bricht der Build ab.

Nach einem Update fragt der Browser Seiten und Skripte bei jedem Aufruf neu an (ab Version 1.2.0).
Beim Wechsel von 1.1.0 auf 1.2.0 kann einmalig **Strg+F5** nötig sein, falls der Browser noch alte
Dateien zwischengespeichert hat.

## Entwicklung

```bat
start.cmd -Simulate
```

Der Befehl legt beim ersten Mal die Python-Umgebung unter `C:\Users\<Name>\ld-mainmachine\venv` an.
Demo-Historie (drei Wochen, Schichtbetrieb) und Tests:

```bat
%USERPROFILE%\ld-mainmachine\venv\Scripts\python.exe tools\seed_demo.py --days 21 --force
%USERPROFILE%\ld-mainmachine\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
%USERPROFILE%\ld-mainmachine\venv\Scripts\python.exe -m pytest
```

| Pfad | Inhalt |
|---|---|
| `app/adapters/` | `lsv2_adapter.py` (echte Steuerung), `sim_adapter.py` (Simulator) |
| `app/collector.py` | Abfrageschleife, Zustandsabschnitte, Läufe, Ereignisse |
| `app/registry.py` | Maschinenverwaltung zur Laufzeit (Konfigurations-Tab) |
| `app/probe.py` | Verbindungstest |
| `app/stats.py` | Auswertung |
| `app/orders.py`, `app/orders_api.py` | Aufträge aus Programmnamen |
| `app/tools.py`, `app/tools_api.py` | Werkzeugauswertung |
| `app/api.py`, `app/config_api.py`, `app/main.py` | Web-API und Start (`python -m app`) |
| `app/__init__.py`, `CHANGELOG.md` | Versionsnummer und Änderungsprotokoll |
| `app/static/` | Oberfläche (HTML/CSS/JS ohne Build-Schritt) |
| `portable/` | Start- und Autostart-Skripte der portablen Version |
| `tools/` | `probe.py`, `seed_demo.py`, `build_portable.ps1` |
| `docker/`, `Dockerfile` | Container (Start ohne Root-Rechte) |
| `unraid/` | Vorlage für die Unraid-Oberfläche |
| `.github/workflows/` | Tests und Docker-Image (GitHub Actions) |

## Lizenz

MIT, siehe [LICENSE](LICENSE). Die Anbindung nutzt [pyLSV2](https://github.com/drunsinn/pyLSV2)
(ebenfalls MIT). HEIDENHAIN, TNC und iTNC sind Marken der DR. JOHANNES HEIDENHAIN GmbH; dieses
Projekt steht in keiner Verbindung zu HEIDENHAIN oder DMG MORI.
