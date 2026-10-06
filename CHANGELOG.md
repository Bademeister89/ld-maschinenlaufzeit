# Änderungsprotokoll

Alle Versionen von LD-Machine-Viewer, die neueste oben. Die Versionsnummer folgt dem Schema
`MAJOR.MINOR.PATCH`:

- MAJOR: Umstellungen, bei denen man selbst etwas tun muss (z. B. Einstellungen anpassen)
- MINOR: neue Funktionen
- PATCH: Fehlerbehebungen

Die laufende Version steht in der Oberfläche oben neben dem Namen und im Tab Konfiguration.
Dort ist auch dieses Protokoll zu sehen.

## [1.12.0] – 2026-10-06

### Neu
- **Lauf löschen** im Auftrag (Programmdurchläufe → „Löschen“), z. B. für einen Fehllauf oder ein
  Nachprogramm.
  - Der Lauf zählt danach nicht mehr zum Auftrag, zu den Ø-Stückzeiten und zur
    Restlaufzeit-Prognose.
  - Die Laufzeit der Maschine in der Auswertung bleibt erhalten.
  - Nur beendete Läufe, mit Rückfrage. Das Löschen wird als Ereignis protokolliert.

### Behoben
- Die Liste „Programmdurchläufe“ klappte beim automatischen Aktualisieren alle 20 s wieder zu.
- Ein neuer Auftrag, dessen erster Lauf noch lief, zeigte in der Auftragsliste bei „Fertige Läufe“
  „null“ statt 0.

## [1.11.1] – 2026-10-05

### Behoben
Grundlage ist die Diagnose-Datei aus dem Feldtest an DMU 70 und DMU 105.

- **Handbetrieb und MDI** erzeugen keine Läufe mehr.
  - MDI-Sätze und Makros im Handbetrieb wurden als kurze Läufe des angewählten Programms gebucht
    und seit 1.11.0 sogar als „fertig“. Allein an der DMU 105 waren das am 5.10. über 50 Läufe von
    2–11 s.
  - Sie verfälschten Ø-Zeit je Teil, Restlaufzeit und die Zahl der fertigen Läufe.
- **Programmende:** Fertig ist ein Lauf, der bis zuletzt im Programmlauf lief und dann „inaktiv“
  meldet. Die 5-%-Regel aus 1.11.0 entfällt, denn sie hat gestoppte und dann abgebrochene Läufe
  kurz vor dem Ende als fertig gezählt.
- **Satzvorlauf nach einer Störung:** Startet das Programm dort wieder, wo sein letzter Lauf
  endete, läuft dieser Lauf weiter.
  - Beispiel DMU 105: Störung „WZW Klappe“ bei Satz 91.049, danach zwei Satzvorläufe. Bisher
    ergab das drei Läufe, jetzt einen.
  - Ein anderer Start mitten im Programm gilt als Teillauf und zählt nicht in die Ø-Stückzeiten.
- **Palettentabellen** (`.P`) bekommen nie einen eigenen Lauf, auch wenn sie nur einen
  Palettenwechsel ausführen.
- **Einmalige Bereinigung** beim ersten Start nach dem Update. Für die schon erfassten Läufe gelten
  dieselben Regeln:
  - Läufe im Handbetrieb, unter MDI und von Palettentabellen werden verworfen. Die Zeit bleibt
    als Maschinenzeit erhalten.
  - Programmenden mit „inaktiv“ werden zu „fertig“, gestoppte und dann abgebrochene Läufe zu
    „unterbrochen“.
  - Fertige Läufe, die per Satzvorlauf mitten im Programm begannen, werden zu Teilläufen.
  - Das Log zeigt, wie viele Läufe geändert wurden.

## [1.11.0] – 2026-10-05

### Neu
- **Diagnose-Datei** für Fehlermeldungen: Konfiguration → Diagnose → „Diagnose-Datei herunterladen“.
  - Inhalt der ZIP-Datei:
    - Logs
    - Version und Einstellungen
    - Live-Status
    - ein **Mitschnitt** jeder Änderung der Steuerungsdaten seit dem Start der App
    - Läufe mit Programmstatus am Laufende
    - Zustandsabschnitte, Ereignisse und gelesene Programmdateien des Zeitraums
  - Zeitraum 1, 7 oder 30 Tage, auf Wunsch mit der ganzen Datenbank.
  - Die Datei enthält Maschinenadressen und Programmnamen, aber keine Passwörter.

### Behoben
- Läufe, die bis zum Programmende liefen, standen als „unterbrochen“ in den Programmläufen.
  - **Ursache:** Die Steuerung meldet nach dem Programmende gleich „inaktiv“ statt „beendet“
    (z. B. ohne M30).
  - **Jetzt:** Das zählt als fertig, wenn das Programm bis zuletzt lief oder zuletzt in seinen
    letzten 5 % Sätzen stand.
  - Ein Abbruch über NC-Stopp mitten im Programm bleibt „unterbrochen“.
  - Schon erfasste Läufe bleiben unverändert.

## [1.10.4] – 2026-10-05

### Behoben
- Palettentabellen (`.P`) als Hauptprogramm, z. B. `pal1sp.p` an der DMU 70: Die App las sie wie
  ein Klartext-Programm. Die Live-Karte zeigte deshalb „ruft auf: – (kein CALL PGM erkannt)“.
  - Jetzt liest die App die Programme aus der Spalte `NAME`, auch volle Pfade mit Leerzeichen
    (`TNC:\Programme\21 Motor\…\26-21053-01-01.h`). Zusätzlich erkennt sie jeden Dateinamen auf
    `.H`/`.I` in der Tabelle.
  - Damit ist eindeutig, was die Palettentabelle selbst aufruft (Auftragsprogramm, `DREH.H`,
    Palettenwechsel). Unterprogramme des Auftragsprogramms bleiben in dessen Lauf, auch wenn
    seine Datei nicht lesbar ist.
  - Schon gelesene Palettentabellen werden nach dem Update einmal neu eingelesen.
- Verbindungstest: „Hauptprogramm lesen“ meldet eine Palettentabelle ohne Satzanzahl nicht mehr
  als Fehler.

## [1.10.3] – 2026-10-05

### Behoben
- Palettenbetrieb: Der Auftragslauf lief über Reinigung und Palettenwechsel hinweg weiter, wenn die
  App die Aufrufe im Hauptprogramm nicht kannte.
  - **Ursachen:** Die Datei war nicht lesbar, enthielt keine erkennbare `CALL PGM`-Zeile, oder
    das Lesen ist einmal fehlgeschlagen. Ein zweiter Versuch kam erst beim nächsten Lauf, und der
    begann so nie.
  - **Jetzt:** Die App prüft zusätzlich die Datei des **Auftragsprogramms**. Ein Programm, das das
    Auftragsprogramm nicht selbst aufruft (z. B. `DREH.H`), beendet den Auftragslauf. Das gilt auch,
    wenn gar keine Datei lesbar ist. Ausnahme: Makros auf `PLC:`.
  - Nicht lesbare Programmdateien werden jede Minute erneut gelesen. Nach einer abgebrochenen
    Übertragung liest die App die Datei ganz neu, statt nur auf Änderungen zu prüfen.
  - Erkannt werden zusätzlich `SEL CYCLE` und Programmnamen in Anführungszeichen.
- Live-Karte: Unter dem Programm steht beim Palettenbetrieb, welche Programme das Hauptprogramm
  laut Datei aufruft, oder warum die Datei nicht gelesen werden konnte.

## [1.10.2] – 2026-10-05

### Behoben
- Palettenprogramme mit Zwischenprogramm, z. B. einer Reinigung zwischen den Auftragsprogrammen:
  - **Fehler:** Die Reinigung wurde dem Auftragsprogramm davor zugerechnet. Lief danach
    dasselbe Auftragsprogramm noch einmal, wurden beide Teile samt Reinigung zu einem einzigen Lauf.
  - **Jetzt:** Die App liest aus der Datei des Palettenprogramms, welche Programme es selbst
    aufruft (`CALL PGM`, `SEL PGM`, Zyklus 12).
    - Ruft das Palettenprogramm ein Programm ohne Auftragsnummer auf, endet der Lauf des
      Auftragsprogramms, und die Reinigung zählt zu keinem Auftrag.
    - Unterprogramme, die nur das Auftragsprogramm aufruft, bleiben in dessen Lauf.
- Verbindungstest: Neuer Schritt „Hauptprogramm lesen“, wenn das Hauptprogramm gerade ein
  anderes Programm aufruft, mit der Liste „ruft auf: …“.
- Datenbank-Schema 10: Schon gelesene Programmdateien werden nach dem Update einmal neu
  eingelesen.

## [1.10.1] – 2026-10-05

### Behoben
- Palettenprogramme auf der Automation: Ruft ein Oberprogramm ohne Auftragsnummer die
  Auftragsprogramme per `CALL PGM` auf, zählt jetzt das aufgerufene Programm.
  - Bisher wurden Lauf, Laufzeit und Werkzeugaufrufe dem Palettenprogramm zugeordnet, und beim
    eigentlichen Auftrag kam nichts an.
  - Lauf, Auftrag, Restlaufzeit und Werkzeugaufrufe gehören jetzt zum aufgerufenen
    Auftragsprogramm. Die Live-Karte zeigt „aufgerufen von …“.
  - Ruft das Auftragsprogramm selbst Unterprogramme auf, bleibt es beim Auftragsprogramm.
  - Kehrt die Steuerung ins Palettenprogramm zurück, ist der Lauf des aufgerufenen Programms
    „fertig“. Damit gibt es auch für diese Programme eine Restlaufzeit und eine Ø-Zeit je Teil.
  - Die Zeit im Palettenprogramm selbst (z. B. Palettenwechsel) bleibt Laufzeit der Maschine,
    zählt aber zu keinem Lauf und keinem Auftrag.
- Schon abgeschlossene Läufe der Palettenprogramme bleiben, wie sie sind. Sie werden nicht
  nachträglich auf die Aufträge umgebucht.

## [1.10.0] – 2026-10-02

### Geändert
- Neuer Name: **LD-Machine-Viewer** (bisher „LD Maschinenlaufzeit“). Geändert ist der Anzeigename:
  - Kopfzeile, Name auf dem Handy-Startbildschirm, Konsolenfenster
  - Desktop-Verknüpfung, Windows-Aufgabe für den Autostart
- Gleich bleiben Docker-Image, Unraid-Vorlage, appdata-Ordner, ZIP-Name und alle Daten.
- Portable Version: `autostart-einrichten.cmd` entfernt die alte Aufgabe und Firewall-Regel
  „LD Maschinenlaufzeit“ automatisch. `verknuepfung-erstellen.cmd` ersetzt die alte Verknüpfung.

## [1.9.0] – 2026-10-02

### Neu
- Aufrufzähler in der Werkzeugauswertung: In jeder Zeile steht, wie oft das Werkzeug in die
  Spindel gewechselt wurde, z. B. „212× aufgerufen“.
  - Gezählt wird jeder Wechsel auf das Werkzeug, im Programm wie im Handbetrieb, je Maschine
    getrennt und seit Beginn der Erfassung. Zurücksetzen ändert daran nichts.
  - Sortierung „Meiste Aufrufe“: zeigt oben die Werkzeuge, die am häufigsten gebraucht werden.
  - Neue Kachel „Meist aufgerufen“ und Spalte „Aufrufe“ im CSV-Export (letzte Spalte).
- Werkzeugplätze je Maschine (Konfiguration → Bearbeiten, z. B. 30 oder 60): Die Werkzeugauswertung
  markiert so viele meistgebrauchte Werkzeuge mit „Top 30 · Platz 3“. Bei „Meiste Aufrufe“ zeigt
  eine Linie, wo das Magazin endet.
- Auftragsversionen: Programme wie `26-21053V1-01-01` und `26-21053V2-01-01` werden als eigene
  Aufträge `21053V1` und `21053V2` erkannt. Bisher wurden sie keinem Auftrag zugeordnet.
  Schon erfasste Läufe solcher Programme werden beim Update nachträglich zugeordnet.
- Live-Karte: Poti-Stellung für Vorschub und Spindel als zwei Balken mit Prozentzahl.
  - Skala 0–150 %, ein Strich markiert 100 %.
  - Farbe je Stellung: unter 50 % rot, unter 100 % gelb, 100–120 % grün, über 120 % orange.
  - Der Eilgang (FMAX) steht weiter als Kennzahl.
  - Die bisher gespeicherten Werkzeugwechsel werden beim Update einmalig nachgetragen, die Zahlen
    starten also nicht bei 0.

## [1.8.1] – 2026-10-02

### Behoben
- Auftragsbild am Android-Handy: Es ließ sich nur ein Bild aus der Galerie wählen, die Kamera
  ging nicht auf. Am Handy gibt es jetzt zwei Buttons: „Foto aufnehmen“ öffnet direkt die
  Kamera, „Aus Galerie“ die Fotoauswahl. Am PC bleibt es bei „Bild hinzufügen“.

## [1.8.0] – 2026-10-02

### Neu
- Ein Bild je Auftrag (fertiges Bauteil):
  - Im Auftragsdetail „Bild hinzufügen“, „Bild ersetzen“ und „Bild entfernen“. Am Handy bietet
    der Browser Kamera oder Galerie an.
  - Der Browser verkleinert das Foto vor dem Hochladen auf höchstens 1280 px (ca. 150–250 KB)
    und erzeugt ein Vorschaubild mit 256 px (ca. 10–20 KB).
  - Handyfotos erscheinen richtig herum. Die Kameradaten (EXIF, auch GPS) werden entfernt.
  - Das Vorschaubild steht in der Auftragsliste und klein auf der Live-Karte, solange der Auftrag
    an der Maschine angewählt ist.
  - Die Bilder liegen als Dateien im Datenordner unter `images/orders/`, nicht in der Datenbank.
    600 Aufträge mit Bild brauchen etwa 100–150 MB. Zur Sicherung den ganzen Datenordner sichern.

### Geändert
- Maschinenbilder nutzen dieselbe Verkleinerung. Handyfotos werden dabei ausdrücklich nach der
  Ausrichtung der Kamera gedreht.

## [1.7.0] – 2026-09-28

### Geändert
- Live-Ansicht überarbeitet:
  - Größere Maschinenkarten mit großem Maschinenbild (bzw. Kürzel ohne Bild).
  - Der Zustand steht als großer farbiger Balken mit Symbol neben dem Bild, zum Beispiel
    „▶ Läuft · 1 min seit 15:42 Uhr“.
  - Darunter aufgeräumt: Programm mit Auftrag und Laufdauer, Fortschritt und Restlaufzeit,
    Kennzahlen, Tagesverlauf.
  - Auf dem Handy steht das Bild klein neben dem Namen, der Statusbalken geht über die volle Breite.

### Behoben
- Bei stehender Maschine (z. B. „Bereit“) stand auf der Live-Karte das Wort „null“.

## [1.6.3] – 2026-09-28

### Neu
- Technischer Schreibschutz für alle Verbindungen zur Steuerung. Jeder Befehl wird vor dem Senden
  gegen eine Liste reiner Lesebefehle geprüft. Alles andere wird blockiert, bevor es den Rechner
  verlässt, zum Beispiel Dateien senden, löschen, umbenennen oder kopieren, Ordner anlegen,
  Maschinenparameter oder PLC schreiben, Tasten senden, die Tastatur sperren oder die Steuerung
  zurücksetzen. Lässt sich der Schutz nicht einrichten, baut die App keine Verbindung auf.
  Die App war schon vorher rein lesend; der Schutz sichert das zusätzlich technisch ab.

## [1.6.2] – 2026-09-28

### Neu
- Werkzeugdaten beim Anlegen und Bearbeiten: Hersteller, Artikelnummer, Durchmesser und Radius (mm).
  Sie stehen in der Werkzeugliste, lassen sich suchen und sind im CSV-Export enthalten.
- Herstellerliste im Tab Konfiguration („Werkzeughersteller“: hinzufügen, umbenennen, entfernen).
  Im Werkzeug-Dialog wählst du den Hersteller per Dropdown aus der Liste.
- Einstellbare Vorwarnzeit je Werkzeug („Vorwarnung bei“, z. B. 80 h bei 100 h Maximallaufzeit).
  Der Balken zeigt die Stelle mit einem Strich. Neue Werkzeuge bekommen 100 h mit Vorwarnung bei
  80 h.

### Geändert
- Datenbank-Schema 7. Das Update läuft beim Start automatisch. Werkzeuge ohne eingetragene
  Vorwarnzeit warnen wie bisher bei 90 % der Maximallaufzeit.

## [1.6.0] – 2026-09-28

### Neu
- Eigenes App-Symbol im Browser-Tab, bei Lesezeichen und oben neben dem Namen.
- Die Oberfläche lässt sich auf Handy und Tablet mit dem App-Symbol auf den Startbildschirm legen.
- Symbol im Unraid-Reiter DOCKER (über die Vorlage oder das Feld „Icon URL“).
- Portable Version: `verknuepfung-erstellen.cmd` legt die Verknüpfung „LD Maschinenlaufzeit“ mit
  Symbol auf dem Desktop und im Startmenü an. Ein Klick öffnet die Oberfläche und startet die App
  vorher, falls sie nicht läuft.

### Geändert
- `start.cmd` öffnet nur den Browser, wenn die App schon läuft (z. B. über den Autostart). Das
  Konsolenfenster bleibt dann nicht mehr mit einer Meldung stehen.

## [1.5.0] – 2026-09-28

### Neu
- Jedes neu angelegte Werkzeug bekommt automatisch eine Maximallaufzeit von 100 h. Das gilt für
  automatisch angelegte Werkzeuge und beim Anlegen von Hand (Feld mit 100 vorbelegt). Das Limit
  lässt sich je Werkzeug unter Bearbeiten ändern oder leeren. Bereits angelegte Werkzeuge behalten
  ihr bisheriges Limit.

## [1.4.0] – 2026-09-28

### Neu
- Werkzeugnamen aus der Werkzeugtabelle `TOOL.T` der Steuerung (Spalte NAME). Die App liest die
  Tabelle nur lesend nach dem Start und prüft dann alle 10 Minuten, ob sie sich geändert hat.
  Taucht ein Werkzeug ohne bekannten Namen auf, prüft sie schon nach einer Minute.
- Die Live-Karte zeigt den Werkzeugnamen.
- Der Verbindungstest hat einen neuen Schritt „Werkzeugtabelle lesen“.

### Geändert
- Die Simulation verhält sich wie die echte Steuerung: Die Spindelabfrage liefert nur die Nummer,
  die Namen kommen aus einer simulierten Werkzeugtabelle.

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
