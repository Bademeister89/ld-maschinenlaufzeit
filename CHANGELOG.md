# Änderungsprotokoll

Alle Versionen von LD Maschinenlaufzeit, die neueste oben. Die Versionsnummer folgt dem Schema
`MAJOR.MINOR.PATCH`:

- MAJOR: Umstellungen, bei denen man selbst etwas tun muss (z. B. Einstellungen anpassen)
- MINOR: neue Funktionen
- PATCH: Fehlerbehebungen

Die laufende Version steht in der Oberfläche oben neben dem Namen und im Tab Konfiguration.
Dort ist auch dieses Protokoll zu sehen.

## [1.3.1] – 2026-09-28

### Geändert
- Werkzeugnummern gehen bis T1000 (bisher T600).
- Der Werkzeugname wird nur noch angezeigt, wenn es einen gibt. Die Steuerung liefert über die
  Abfrage „Werkzeug in der Spindel“ nur die Nummer, keinen Namen. Die Namen in der Simulation sind
  erfunden, eigene Bezeichnungen gehören in die Notiz.

## [1.3.0] – 2026-09-28

### Neu
- Tab „Werkzeugauswertung“. Er zeigt die Einsatzzeit je Werkzeug (T1–T600), getrennt je Maschine.
  Gezählt wird die Zeit, in der ein Programm läuft und das Werkzeug in der Spindel ist. Ein Werkzeug
  wird automatisch angelegt, sobald es zum ersten Mal in der Spindel auftaucht. Man kann es auch von
  Hand anlegen, um das Limit schon vorher einzutragen.
- Maximallaufzeit je Werkzeug in Stunden. Der Balken zeigt, wie viel davon verbraucht ist. Ab 90 %
  erscheint eine Vorwarnung, ab 100 % die Meldung „Über Limit“ in Rot, auch als Zahl am Reiter und
  auf der Live-Karte, solange das Werkzeug in der Spindel ist.
- Knopf „Zurücksetzen“ für ein neu eingespanntes Werkzeug. Die Einsatzzeit beginnt wieder bei 0,
  der alte Stand bleibt als Standzeit in der Historie (mit Ø Standzeit).
- Die Live-Karte zeigt beim Werkzeug die bisherige Einsatzzeit.
- CSV-Export der Werkzeugliste.

### Geändert
- Datenbank-Schema 6 (neue Tabellen für Werkzeuge). Das Update läuft beim Start automatisch.
- Die Simulation nutzt mehr Werkzeuge aus dem Bereich 1–600.

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
