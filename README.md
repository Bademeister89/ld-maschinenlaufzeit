# LD Maschinenlaufzeit

Erfasst laufend den Zustand der Heidenhain-Steuerungen (iTNC 530) der DMG-Fräsen und speichert ihn für
spätere Laufzeitauswertungen:

- **Live:** Läuft die Maschine? Welches Programm, seit wann, welcher Satz, welches Werkzeug, welcher Override?
- **Auswertung:** Laufzeit und Auslastung je Tag und Maschine, Zeitleiste, Programme mit Stückzeiten,
  CSV-Export für Excel.
- **Konfiguration:** Maschinen mit Name, IP, Bild, Standort/Notiz und Reihenfolge anlegen, Verbindung testen.

Die Anbindung läuft über das LSV2-Protokoll (TCP 19000) mit der Open-Source-Bibliothek
[pyLSV2](https://github.com/drunsinn/pyLSV2), also über dasselbe Protokoll wie TNCremo und das
Heidenhain-RemoTools-SDK, aber ohne Windows-COM-Komponente.
Es werden **ausschließlich lesende** Abfragen gestellt.

## Portable Version für den Windows-PC im Betrieb

Die portable Version ist ein Ordner mit eigenem Python. Sie läuft ohne Installation, ohne Adminrechte
und ohne Internetzugang. Voraussetzung ist Windows 10/11 (64 Bit).

**Einrichten:**

1. ZIP `LD-Maschinenlaufzeit-<Datum>.zip` auf den PC kopieren und entpacken, am besten nach
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
  Steuerungsversion → Option 18 → Statusabfrage. Bei Fehlern nennt der Test mögliche Ursachen. Der
  Test funktioniert auch schon vor dem Speichern.
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
| `GET/POST/PUT/DELETE /api/config/...` | Konfiguration: Maschinen, Bild, Reihenfolge, Verbindungstest |

`from`/`to` sind Unix-Sekunden oder ISO-Zeitpunkte. Ohne Angabe gilt: heute 0 Uhr bis jetzt.
Interaktive Doku: <http://localhost:8000/docs>.

## Unraid (Docker über die Weboberfläche)

Das Image wird bei jedem Push auf `main` von GitHub Actions gebaut und liegt unter
`ghcr.io/bademeister89/ld-maschinenlaufzeit:latest`. Auf Unraid ist kein Terminal nötig.

### Container anlegen

Am einfachsten über die Vorlage aus dem Repo: Auf dem Tab **Docker** unter *Template Repositories*
`https://github.com/Bademeister89/ld-maschinenlaufzeit` eintragen und speichern. Danach steht unter
**Add Container** das Template `ld-maschinenlaufzeit` bereit. Alternativ die Datei
`unraid/ld-maschinenlaufzeit.xml` über die Flash-Freigabe nach
`config\plugins\dockerMan\templates-user\` kopieren oder die Felder von Hand ausfüllen:

| Feld | Wert |
|---|---|
| Name | `ld-maschinenlaufzeit` |
| Repository | `ghcr.io/bademeister89/ld-maschinenlaufzeit:latest` |
| Network Type | `bridge`, mit VPN-Tunnel siehe unten |
| WebUI | `http://[IP]:[PORT:8000]/` |
| Port | Container `8000` → Host `8000` |
| Pfad | Container `/data` → `/mnt/user/appdata/ld-maschinenlaufzeit` |
| Variable | `TZ` = `Europe/Berlin` |

Optional: `PUID`/`PGID` (Standard 99/100 = nobody:users), `SIMULATE=1` zum Ausprobieren.

- **Update:** In der Docker-Übersicht auf „Update“ klicken, sobald ein neues Image gebaut wurde.
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
| `app/api.py`, `app/config_api.py`, `app/main.py` | Web-API und Start (`python -m app`) |
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
