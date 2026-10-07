# Änderungsprotokoll

Alle Versionen von LD-Machine-Viewer, die neueste oben. Die Versionsnummer folgt dem Schema
`MAJOR.MINOR.PATCH`:

- MAJOR: Umstellungen, bei denen man selbst etwas tun muss (z. B. Einstellungen anpassen)
- MINOR: neue Funktionen
- PATCH: Fehlerbehebungen

Die laufende Version steht in der Oberfläche oben neben dem Namen und im Tab Konfiguration.
Dort ist auch dieses Protokoll zu sehen.

## [1.19.0] – 2026-10-07

### Neu
- **CAM-Planzeiten aus der Tebis-Doku:** Im Tab „Aufträge“ liest „CAM-Doku importieren“ die
  PDF-Doku einer Aufspannung, auch mehrere Dateien auf einmal.
  - Auftrag bzw. Felge wird angelegt, falls es sie noch nicht gibt, und je Programm wird die
    Planzeit aus dem Programmablauf gespeichert.
  - Ist die Bezeichnung leer, kommt sie aus dem Namen der CAD-Datei, z. B. „Abdeckung rechts 1 cvo“.
  - Eine Meldung nennt die übernommenen Programme mit Zeit, Spannung und Maschine, danach öffnet
    sich der Auftrag.
- **Planzeit von Hand:**
  - Neue Spalte „Plan (CAM)“ mit „ändern“ (`4,5` oder `4:30`, leer = entfernen).
  - Für Programme ohne Zeile gibt es ein eigenes Feld, z. B. für eine Version ohne Doku.
- **Plan neben Ist im Auftrag:**
  - Programme mit Planzeit stehen schon vor dem ersten Lauf in ihrer Aufspannung.
  - Aufspannung, Version und die Kachel „Ø Bearbeitungszeit je Teil“ zeigen die Summe der
    Planzeiten.
- **Restlaufzeit beim ersten Lauf:** Die Live-Karte rechnet mit der Planzeit („Prognose aus der
  CAM-Planzeit“) statt der groben Schätzung aus der Satznummer.
- **Palettenliste:** Programme ohne frühere Läufe bekommen ihre Planzeit statt „ohne Zeit“. Der Fuß
  nennt sie („Zeit aus der CAM-Planung“), aus „fertig frühestens“ wird eine feste Uhrzeit.
- **Diagnose-Datei:** enthält jetzt `planzeiten.csv`.

### Geändert
- **Testanläufe zählen nicht mehr als übliche Laufzeit, wenn eine Planzeit bekannt ist.** Läufe unter
  25 % der Planzeit fließen nicht in Restlaufzeit und Palettenliste ein.
  - Beispiel: Bei `21-21053v2-02-01` stand ein Lauf mit 27 s als fertig, die Planzeit ist 2 h 21 min.
  - Die Ø-Zeiten im Auftrag zählen weiter alle fertigen Läufe. Einen Fehlstart entfernt man dort
    mit „Löschen“.
- **Neue Bibliothek `pypdf`:** Docker-Image und portable Version bringen sie mit.
- **Datenbank-Schema 15** mit der neuen Tabelle `program_plans`. Sie wird beim Start automatisch
  angelegt.

## [1.18.1] – 2026-10-07

### Behoben
- **Programm doppelt im Auftrag:** `26-21051-02-01.h` lief auch als Kopie aus dem Ordner von 21053
  und stand deshalb zweimal unter Aufspannung 2. Grundlage ist die Diagnose vom 7.10.
  - Ein Programm ist jetzt sein Name, nicht sein Ordner: Beide Speicherorte ergeben eine Zeile mit
    allen Läufen und einer gemeinsamen Ø-Zeit je Teil, dazu „· 2 Ordner“ mit den Pfaden im Tooltip.
  - Ebenso in der Auswertung je Programm.
  - Die Restlaufzeit der Kopie nutzt jetzt die Läufe des Originals. Bisher gab es dort nur die grobe
    Schätzung aus der Satznummer, obwohl schon vollständige Läufe da waren.

## [1.18.0] – 2026-10-07

### Geändert
- **Das Jahr im Programmnamen zählt nicht mehr:** `21-21053v1-02-01`, `25-21053-…` und
  `26-21053-01-01` gehören alle zum Auftrag 21053.
  - Beispiel: Ein Programm `21-21053v1-02-01` landete bisher als eigener Auftrag „21053 (2021)“.
    Jetzt steht es im Auftrag 21053 im Block „Version V1“.
  - Der Schlüssel eines Auftrags ist nur noch die Nummer (`21053` statt `26-21053`), Links und
    CSV-Dateien heißen entsprechend.
  - Das Jahr steht nicht mehr in Liste, Auftrag und Live-Karte („Auftrag 21053 V1 · Aufspannung 2 …“).
  - Beim Update werden Aufträge mit gleicher Nummer aus verschiedenen Jahren einmalig
    zusammengeführt. Bezeichnung und Bild bleiben erhalten, vorhandene Bilder funktionieren weiter.

## [1.17.0] – 2026-10-07

### Geändert
- **Versionen gehören zum Grundauftrag:** `26-21053V1-01-01`, `-V2-` … stehen jetzt im Auftrag
  21053 statt als eigene Aufträge.
  - Laufzeit, Stopps und Läufe des Auftrags zählen über alle Versionen.
  - Im Detail hat jede Version einen eigenen Block mit ihren Aufspannungen und ihrer
    Ø Bearbeitungszeit je Teil, denn eine Version ist eine andere Ausführung des Teils.
  - In der Liste steht die Version hinter der Nummer, z. B. „21053 · 2026 · V1, V2“. Die Suche
    findet den Auftrag auch über „21053V1“.
  - Die Live-Karte zeigt „Auftrag 21053 V1 (2026) · …“. Der CSV-Export hat eine Spalte „Version“.
  - Beim Update werden die bisherigen Versionsaufträge einmalig in den Grundauftrag übernommen.
    Bezeichnung und Bild des Grundauftrags bleiben; fehlen sie, kommen sie von der Version.

## [1.16.0] – 2026-10-07

### Neu
- **Felgen** werden erkannt und wie Aufträge erfasst, ohne die Programme umzubenennen.
  - Schema `BBDDBBZZ-SS[ Zusatz]`, z. B. `10101018-01 tasche` = einteilig, Design 999, 10 × 18″,
    Spannung 1, Zusatz „tasche“. Breite unter 20 in ganzen Zoll, ab 20 in Zehntel (85 = 8,5″).
  - Je Felge ein Eintrag im Tab „Aufträge“, mit Filter „Alle / Aufträge / Felgen“.
  - Die Programme einer Spannung (`-01`, `-01 tasche`, `-01 einarm`) stehen einzeln und ergeben
    zusammen die Ø Bearbeitungszeit je Felge.
  - Die Live-Karte zeigt z. B. „Felge 999 · einteilig · 10 × 18″ · Spannung 1 · tasche“.
  - Die Design-Namen sind unter Konfiguration → **Felgen-Designs** pflegbar, mit 10 = 999 bis
    90 = Sonder als Start.
- **Werkzeugauswertung sortieren** über die Spaltenköpfe direkt über der Liste jeder Maschine:
  nach T-Nummer, meisten Aufrufen oder meister Laufzeit.
  - Der Pfeil zeigt die aktive Sortierung. Die Umschalter oben in der Filterleiste entfallen dafür.
- **Laufzeit gesamt** je Werkzeug: Einsatzzeit seit Beginn der Erfassung, als eigene Spalte neben den
  Aufrufen, dazu die Kachel „Meiste Laufzeit“ und eine Spalte im CSV-Export.
  - Anders als die Standzeit beginnt sie beim Zurücksetzen nicht neu.
- **Aufträge schließen sich automatisch**, wenn 7 Tage lang kein Programm des Auftrags lief.
  - Im Detail steht dann „(automatisch, 7 Tage ohne Programmlauf)“.
  - Läuft der Auftrag wieder an oder wird er von Hand geöffnet, ist er wieder offen. Nach dem
    Öffnen von Hand beginnen die 7 Tage neu.

### Geändert
- Im Auftrag entfällt die Kachel „Durchlaufzeit“.
- Das voraussichtliche Ende im Auftrag („läuft gerade“) nennt wie auf der Live-Seite „morgen“ bzw.
  den Wochentag, wenn es nicht mehr heute ist.

## [1.15.1] – 2026-10-07

### Behoben
Grundlage ist die Diagnose-Datei vom 7.10.: Palettenprogramm pal2sp.p an der DMU 70 über Nacht.
Die Restzeit blieb die ganze Nacht eine Schätzung („mind.“, „fertig frühestens“).

- **Fehlstart als „fertig“ gezählt:** Um 17:31 lief 26-21051-02-01 nur bis Satz 34 von 514.998,
  dann kam der Neueinstieg. Die iTNC meldete direkt „inaktiv“, und der 14-s-Lauf zählte als fertig.
  - Kommt ein Programm nicht über seine ersten Sätze hinaus, ist der Lauf jetzt „unterbrochen“.
- **Neueinstieg per Satzvorlauf wurde ein Teillauf:** Der unterbrochene Lauf von 16:49 (bis Satz
  66.865) wurde bei Satz 66.851 nicht fortgesetzt. Dazwischen lagen die Vorrichtung und der
  Fehlstart.
  - Die App merkt sich jetzt die letzten 10 beendeten Läufe und setzt den passenden fort.
  - Palette 8 wird damit ein vollständiger Lauf und für Palette 9 die Grundlage der Prognose. Bisher
    gab es dort nur die grobe Schätzung aus der Satznummer.
  - In der Ablaufliste läuft Palette 8 seit dem ersten Start. Gemessen wird sie dann nicht, denn der
    Teil vor dem Neueinstieg fehlt in der Messung. Es gilt die Laufzeit des fertigen Laufs.
- **P-Ende ohne Zeit machte alles zur Mindestzeit:** P-Ende war noch nie gelaufen. Deshalb hieß es
  die ganze Nacht „mind.“ und „fertig frühestens“.
  - Fehlt nur die Zeit von Hilfsprogrammen ohne Auftragsnummer (Drehen, P-Ende), steht jetzt
    „ca.“ mit fester Uhrzeit. Der Fuß nennt sie weiter.
- Das echte Format der Palettentabelle (mit `#STRUCTBEGIN`-Kopf) ist jetzt als Test hinterlegt.

## [1.15.0] – 2026-10-06

### Neu
- **Marker für die laufende Palette:** Im Programmfeld der Live-Karte steht z. B.
  „▶ Palette 8 · seit 16:49 Uhr (23 min) · 1 von 4“. In der Ablaufliste steht bei der laufenden
  Palette zusätzlich „seit …“.
- **Programm zum ersten Mal auf der Palette:** Statt „mind. 47 s“ je Palette gilt die Prognose des
  laufenden Laufs (bisherige Laufzeit plus Rest) auch für die weiteren Paletten mit diesem Programm.
  Der Fuß der Liste nennt das („geschätzt aus dem laufenden Lauf“).
- Liegt das voraussichtliche Ende nicht mehr heute, steht „morgen“ bzw. der Wochentag davor, auch
  bei der Restlaufzeit des Programms.

### Behoben
- **Nach einem Neustart der App** (z. B. Update) mitten im Palettenprogramm war keine Palette
  markiert. Beobachtet an der DMU 70 mit pal2sp.p um 17:05.
  - Ursache: Die Palettentabelle war in dem Moment noch nicht neu eingelesen. Das laufende Programm
    galt danach als schon zugeordnet.
  - Außerdem bestimmt die App die Stelle jetzt aus den Läufen in der Datenbank, statt bei der ersten
    passenden Zeile anzufangen. Palette und Beginn stimmen damit auch nach einem Neustart.

## [1.14.0] – 2026-10-06

### Neu
- **Eilgang-Poti** als Balken auf der Live-Karte, unter dem Vorschub. Skala und Farben wie bei
  Vorschub und Spindel. Die Zeile „Eilgang (FMAX)“ bei den Angaben darunter entfällt dafür.
- **Auftragsbild aus der Zwischenablage:** Bild kopieren (z. B. Screenshot) und im geöffneten
  Auftrag Strg+V drücken.
  - Ist schon ein Bild da, fragt die App vor dem Ersetzen.
  - Am PC unter `localhost` gibt es dafür zusätzlich den Button „Aus Zwischenablage“.
- **Ablaufliste beim Palettenprogramm (.P):** Die Live-Karte zeigt, welche Programme die
  Palettentabelle nacheinander abarbeitet.
  - Eine Zeile je Palette mit Programmen und erwarteter Zeit, fertige mit ✓, die laufende mit ▶.
    Gesperrte Paletten sind durchgestrichen.
  - Darüber die Restzeit und das voraussichtliche Ende, darunter die Gesamtzeit.
  - Die Zeiten kommen aus früheren Läufen. Sobald ein Programm in diesem Palettenprogramm fertig ist,
    gilt die gemessene Zeit, dann mit Palettenwechsel.
  - Die Diagnose-Datei enthält die Palettentabellen jetzt unter `paletten/`.
  - Beim Update liest die App die Palettentabellen einmal neu ein.

## [1.13.0] – 2026-10-06

### Neu
- **Vorrichtung:** Spannung 08 und 09 sind Vorrichtungsbau, z. B. `26-21048-08-01`.
  - Im Auftrag stehen sie als eigene Karte „Vorrichtung (Spannung 08)“.
  - Ihre Zeit zählt zur Laufzeit des Auftrags („davon Vorrichtung …“), aber nicht mehr zur
    Ø-Bearbeitungszeit je Teil.
  - Beispiel 26-21048 vom 06.10.: 952,7 statt 966,2 min je Teil.

### Behoben
Grundlage ist die Diagnose-Datei vom 06.10.

- **Zu große Programme** (z. B. 26-21048-02-01 mit 57,8 MB an der DMU 105) und Makros wie
  `PLC:\PLC\Palett.H` wurden jede Minute neu versucht, jedes Mal mit eigener Verbindung zur
  Steuerung.
  - Jetzt prüft die App ein zu großes Programm nur einmal je Lauf auf Änderungen.
  - `PLC:\`-Makros liest sie gar nicht mehr, dafür bräuchte sie PLC-Rechte.
- **Log-Dateien** reichten nur rund 15 Stunden zurück.
  - Ursache: Die LSV2-Bibliothek schrieb bei jeder Abfrage der Fehlermeldungen zwei Zeilen
    („NO_NEXT_ERROR“), rund 75.000 Zeilen am Tag.
  - Jetzt stehen von ihr nur noch echte Fehler im Log. Die Diagnose-Datei enthält damit wieder
    mehrere Tage.
  - Auch „Werkzeugtabelle gelesen“ steht nur noch beim ersten Lesen und bei geänderten Namen im Log.

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
