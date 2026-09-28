# Änderungsprotokoll

Alle Versionen von LD Maschinenlaufzeit, die neueste oben. Die Versionsnummer folgt dem Schema
`MAJOR.MINOR.PATCH`:

- MAJOR: Umstellungen, bei denen man selbst etwas tun muss (z. B. Einstellungen anpassen)
- MINOR: neue Funktionen
- PATCH: Fehlerbehebungen

Die laufende Version steht in der Oberfläche oben neben dem Namen und im Tab Konfiguration.
Dort ist auch dieses Protokoll zu sehen.

## [1.2.0] – 2026-09-28

### Neu
- Versionsnummer für den Softwarestand. Sie steht auf jeder Seite oben neben dem Namen, im Tab
  Konfiguration zusammen mit der Build-Kennung (Datum und Commit) und diesem Änderungsprotokoll.
- Jede Version gibt es als eigenes Docker-Image (z. B. `ghcr.io/bademeister89/ld-maschinenlaufzeit:1.2.0`).
  Damit lässt sich auf Unraid eine Version festhalten oder zu einer älteren zurückkehren.

### Behoben
- Nach einem Update zeigte der Browser teils noch alte Seiten aus seinem Zwischenspeicher. Zum
  Beispiel fehlte in der Auswertung der Reiter „Aufträge“. Seiten und Skripte werden jetzt bei
  jedem Aufruf auf Änderungen geprüft.

## [1.1.0] – 2026-09-28

### Neu
- Tab „Aufträge“. Programme nach dem Schema `JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM`
  (z. B. `26-21055-01-01`) werden automatisch einem Auftrag zugeordnet. Ein neuer Auftrag wird
  angelegt, sobald sein erstes Programm an einer Maschine auftaucht.
- Auftragsdetail mit Aufspannungen und Programmen, Ø Bearbeitungszeit je Teil, Laufzeit je Tag,
  allen Läufen, CSV-Export, Bezeichnung sowie Abschließen und Wieder öffnen.
- Die Live-Karte zeigt Auftrag, Aufspannung und Programm mit Link zum Auftrag.
- Die Simulation fährt Aufträge nach dem neuen Namensschema.
- Build-Kennung im Tab Konfiguration.

### Geändert
- Datenbank-Schema 5. Vorhandene Daten werden beim ersten Start automatisch den Aufträgen zugeordnet.

## [1.0.0] – 2026-09-27

### Neu
- Erfassung von Heidenhain-Steuerungen (iTNC 530) über LSV2 mit DNC-Login (Option 18), nur lesend.
- Live-Ansicht je Maschine: Zustand, Programm, Satznummer, Laufzeit, Restlaufzeit, Werkzeug, Override.
- Auswertung: Zeiten je Zustand, Tag und Programm, Auslastung, Zeitleiste, CSV-Export.
- Tab Konfiguration: Maschinen mit IP, Name, Bild, Notiz und Reihenfolge anlegen, dazu ein
  Verbindungstest.
- Prüfadresse am Standort: Fällt die Verbindung zum Standort aus (z. B. VPN), wird die Zeit als
  „Keine Daten“ gebucht und nicht als „Offline“.
- Betrieb als portable Windows-Version oder als Docker-Container (Unraid).
