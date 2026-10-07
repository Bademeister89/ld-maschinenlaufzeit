# LD-Machine-Viewer

Bis Version 1.9.0 hieß die App „LD Maschinenlaufzeit“. Geändert hat sich nur der Anzeigename. Repo,
Docker-Image (`ghcr.io/bademeister89/ld-maschinenlaufzeit`), Unraid-Vorlage, appdata-Ordner und der
Name der portablen ZIP bleiben gleich, ebenso alle Daten.

Erfasst laufend den Zustand der Heidenhain-Steuerungen (iTNC 530) der DMG-Fräsen und speichert ihn für
spätere Laufzeitauswertungen:

- **Live:** Läuft die Maschine? Welches Programm, seit wann, welcher Satz, welches Werkzeug?
  - Die Poti-Stellung für Vorschub, Spindel und Eilgang (unter dem Vorschub) steht als Balken auf
    der Karte: Skala 0–150 %, ein Strich markiert 100 %.
  - Farben: unter 50 % rot, unter 100 % gelb, 100–120 % grün, über 120 % orange. Im Hintergrund des
    Balkens sind die Zonen blass zu sehen, der Wert steht immer als Zahl daneben.
  - Bei einem Palettenprogramm (`.P`) steht die Reihenfolge der Paletten und Programme mit
    erwarteter Zeit, Restzeit und voraussichtlichem Ende auf der Karte, siehe
    [Palettenprogramm](#palettenprogramm-ablaufliste).
- **Auswertung:** Laufzeit und Auslastung je Tag und Maschine, Zeitleiste, Programme mit Stückzeiten,
  CSV-Export für Excel.
- **Aufträge:** Aufträge aus dem Programmnamen (`26-21055-01-01`) automatisch anlegen, Zeit je Auftrag,
  Aufspannung und Programm, Ø Bearbeitungszeit je Teil.
- **Werkzeugauswertung:** Einsatzzeit je Werkzeug (T1–T1000) und Maschine, Werkzeugdaten, Maximallaufzeit mit
  Vorwarnung und roter Meldung, Zurücksetzen beim Werkzeugwechsel, Standzeit-Historie.
- **Konfiguration:** Maschinen mit Name, IP, Bild, Standort/Notiz und Reihenfolge anlegen, Verbindung
  testen; zeigt die laufende Version und das Änderungsprotokoll.

Die Anbindung läuft über das LSV2-Protokoll (TCP 19000) mit der Open-Source-Bibliothek
[pyLSV2](https://github.com/drunsinn/pyLSV2), also über dasselbe Protokoll wie TNCremo und das
Heidenhain-RemoTools-SDK, aber ohne Windows-COM-Komponente.
Es werden **ausschließlich lesende** Abfragen gestellt, siehe [Nur lesend](#nur-lesend-schreibschutz).

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
5. `verknuepfung-erstellen.cmd` ausführen (ohne Adminrechte). Das legt auf dem Desktop und im
   Startmenü die Verknüpfung **LD-Machine-Viewer** mit Symbol an. Ein Klick öffnet die
   Oberfläche; läuft die App noch nicht, startet sie sie vorher. Nach dem Verschieben des Ordners
   erneut ausführen.

| Datei | Zweck |
|---|---|
| `start.cmd` | Starten mit Konsolenfenster; das Fenster zu schließen beendet die Erfassung |
| `start-simulation.cmd` | Ausprobieren mit simulierten Maschinen (eigene Demo-Datenbank) |
| `autostart-einrichten.cmd` | Dauerbetrieb im Hintergrund einrichten (Aufgabenplanung + Firewall) |
| `autostart-entfernen.cmd` | Hintergrundbetrieb beenden und Autostart entfernen; die Daten bleiben |
| `verknuepfung-erstellen.cmd` | Verknüpfung mit Symbol auf Desktop und im Startmenü anlegen |
| `config.yaml` | Port, Abfrageintervall, Zeitzone (nach Änderung neu starten) |
| `data\` | Datenbank, Maschinen- und Auftragsbilder (`data\images\`), Log-Dateien (`data\logs\laufzeit.log`) |

**Update auf eine neue Version:**

1. `autostart-entfernen.cmd` ausführen, falls der Autostart eingerichtet ist.
2. Die neue ZIP über den alten Ordner entpacken. Der Ordner `data\` bleibt dabei erhalten, und die
   Maschinen stehen in der Datenbank, nicht in der `config.yaml`.
3. `autostart-einrichten.cmd` erneut ausführen.

Beim Update von 1.9.0 oder älter auf den neuen Namen räumen beide Skripte die alte Windows-Aufgabe
und Firewall-Regel „LD Maschinenlaufzeit“ mit ab, auch wenn Schritt 1 vergessen wurde.
`verknuepfung-erstellen.cmd` ersetzt die alte Verknüpfung „LD Maschinenlaufzeit“ durch
„LD-Machine-Viewer“, sofern sie auf diesen Ordner zeigt.

**Sicherung:** den ganzen Ordner `data\` kopieren (Datenbank und Bilder), am besten bei beendeter App.

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
- **Werkzeugplätze im Magazin** (optional), z. B. 30 oder 60: Die Werkzeugauswertung markiert dann
  so viele meistgebrauchte Werkzeuge dieser Maschine (siehe [Aufrufe](#werkzeugauswertung-tab-werkzeugauswertung)).
- **▲ ▼:** Reihenfolge der Karten auf der Live-Seite.
- **Entfernen:** Die Maschine wird nicht mehr erfasst. Ihre bisher erfassten Daten bleiben in der
  Datenbank erhalten.

Änderungen gelten sofort. Ändert sich die IP, wird neu verbunden.

Voraussetzungen an jeder Steuerung:

- **Option 18 (HEIDENHAIN DNC)** ist freigeschaltet. Ohne sie gibt es keinen Programmstatus.
- LSV2 ist in der Firewall der Steuerung für den PC erlaubt (MOD → Firewall).
- Der PC erreicht die Steuerung auf **TCP 19000**.

**Werkzeughersteller:** Im Tab Konfiguration steht die Liste der Hersteller für das Dropdown
„Hersteller“ im Werkzeug-Dialog.
- Hinzufügen, Umbenennen und Entfernen. Beim Umbenennen ändert sich der Name auch bei allen
  Werkzeugen mit diesem Hersteller.
- Entfernen löscht nur den Listeneintrag. Werkzeuge behalten den Hersteller, er steht dann nur nicht
  mehr zur Auswahl.
- Hersteller, die schon an Werkzeugen eingetragen waren, stehen nach dem Update automatisch in der Liste.

## Restlaufzeit und Satzanzahl

- **Satzanzahl:** Das angewählte NC-Programm wird einmal pro Lauf rein lesend von der Steuerung
  gelesen, über eine eigene Verbindung wie bei TNCremo. Die Live-Karte zeigt dann „Satz 340 / 2.705“.
  - Klartext-Programme (`.H`): Maßgeblich ist die Nummer des Satzes `END PGM`.
  - DIN/ISO-Programme (`.I`): Gezählt werden die Programmzeilen.
  - Neu geladen wird nur, wenn sich Größe oder Änderungsdatum der Datei ändern.
  - Programme über 20 MB werden übersprungen. Das lässt sich über `program_max_mb` und
    `fetch_programs` in der `config.yaml` einstellen. Ein zu großes Programm prüft die App danach
    nur noch einmal je Lauf auf Änderungen.
  - Makros des Maschinenherstellers (`PLC:\…`) liest die App nicht, dafür bräuchte sie PLC-Rechte.
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

### Palettenprogramm (Ablaufliste)

Ist eine Palettentabelle (`.P`) angewählt, zeigt die Live-Karte, welche Programme sie nacheinander
abarbeitet:

- Eine Zeile je Palette mit ihren Programmen, z. B. „Palette 3 · 26-21053-01-01 · DREH · ca. 16 min“.
  - ✓ heißt fertig, ▶ heißt läuft (das laufende Programm ist unterstrichen, darunter „seit 16:49 Uhr“).
  - Im Programmfeld oben auf der Karte steht dazu ein grüner **Marker**, z. B.
    „▶ Palette 8 · seit 16:49 Uhr (23 min) · 1 von 4“: die laufende Palette, seit wann sie bearbeitet
    wird (Beginn ihres ersten Programms) und die wievielte der nicht gesperrten Paletten sie ist.
  - Durchgestrichen und „gesperrt“ sind Zeilen, die die Steuerung überspringt: `*` in der Spalte
    `LOCK` (bei einer Palette oder einem Spannmittel alles darunter) oder `EMPTY`/`SKIP` in `W-STATE`.
- **Zeiten:**
  - Zuerst die übliche Laufzeit früherer Läufe des Programms (Median der letzten 5, wie bei der
    Restlaufzeit). Steht das Programm ohne Pfad in der Tabelle, gilt das Verzeichnis der Tabelle;
    sonst zählt derselbe Name auch in einem anderen Verzeichnis.
  - Sobald ein Programm in diesem Palettenprogramm einmal fertig ist, die gemessene Zeit bis zum
    nächsten Programm. Darin steckt der Palettenwechsel, ab der zweiten Palette wird die Restzeit
    also genauer.
  - Läuft ein Programm gerade zum ersten Mal, gilt für alle seine Zeilen die Prognose des laufenden
    Laufs (bisherige Laufzeit plus Restlaufzeit). Ist das nur die grobe Schätzung aus der
    Satznummer, ist auch die Liste entsprechend grob.
  - Ein Programm, das noch nie gelaufen ist und gerade nicht läuft, hat keine Zeit. Der Fuß nennt
    es („ohne Zeit: P-Ende“).
    - Ist es ein Auftragsprogramm, heißt es „mind.“ und „fertig frühestens“, denn dann fehlen
      womöglich Stunden.
    - Hilfsprogramme ohne Auftragsnummer wie Drehen oder P-Ende dauern Sekunden bis wenige Minuten.
      Ohne ihre Zeit bleibt es bei „ca.“ und einer festen Uhrzeit.
- Liegt das Ende nicht mehr am selben Tag, steht „morgen“ bzw. der Wochentag davor.
- **Kopf:** „noch ca. 1 h 11 min · fertig ca. 18:08 Uhr“, also die Restzeit des laufenden Programms
  plus die Zeiten der offenen Zeilen. Im Stopp steht „(pausiert)“. **Fuß:** Gesamtzeit aller
  Zeilen und die Programme ohne Zeit.
- **Wie die App die Stelle findet:** Die Steuerung meldet nur Hauptprogramm (die Tabelle) und
  aktuelles Programm, nicht die Zeile. Jeder Wechsel auf ein Programm der Tabelle rückt deshalb zur
  nächsten Zeile mit diesem Programm vor.
  - Nach einem Abbruch geht es an derselben Zeile weiter, zum Beispiel per Satzvorlauf. Startet ein
    anderes Programm, beginnt die Liste von vorn.
  - Startet die App neu, während das Palettenprogramm läuft (z. B. Update), sucht sie die Läufe
    dieses Durchgangs in der Datenbank: rückwärts bis zum letzten Halt (Bereit länger als 5 min,
    Handbetrieb/MDI oder ein anderes Programm). Daraus ergeben sich Palette und Beginn.
  - Folgt dasselbe Programm ohne anderes Programm dazwischen (nur Palettenwechsel), ist kein
    Wechsel zu sehen. In den bisherigen Tabellen steht immer DREH dazwischen.
- Den Text der Tabelle hebt die App auf (wenige KB). Die Diagnose-Datei enthält ihn unter
  `paletten/`.

## Aufträge (Tab „Aufträge“)

Programme nach dem Schema **`JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM`** werden automatisch einem Auftrag
zugeordnet, z. B. `26-21055-01-01`:

| Teil | Bedeutung |
|---|---|
| `26` | Jahr (2026) – zählt für den Auftrag nicht |
| `21055` | Auftragsnummer, 4- oder 5-stellig, optional mit Version: `21055V1`, `21055V2` (gehören zu 21055) |
| `01` | Aufspannung (1 = Spannung 1, 2 = Spannung 2 …); `08` und `09` = Vorrichtungsbau |
| `01` | Programmnummer, fortlaufend |

**Felgen** haben ein eigenes Schema **`BBDDBBZZ-SS`** mit optionalem Zusatz, z. B.
`10101018-01 tasche`:

| Teil | Bedeutung |
|---|---|
| `10` | Bauart: 10 = einteilig, 11 = dreiteilig |
| `10` | Design: 10 = 999, 20 = Z06, 30 = EK 1 … (Namen unter Konfiguration → Felgen-Designs) |
| `10` | Breite in Zoll: unter 20 ganze Zoll (10 = 10″), ab 20 Zehntel (85 = 8,5″) |
| `18` | Durchmesser in Zoll (bis 30″) |
| `01` | Spannung |
| ` tasche` | Zusatz (optional): weiteres Programm derselben Spannung, z. B. `einarm`, `normal` |

- Jede Felge (die achtstellige Nummer) ist ein eigener Eintrag im Tab „Aufträge“, z. B.
  „Felge 999 · einteilig · 10 × 18″“. Der Filter **Alle / Aufträge / Felgen** zeigt nur eine Art.
- Programme mit Zusatz (`-01`, `-01 tasche`, `-01 einarm`) stehen als eigene Zeilen unter
  „Spannung 1“. Sie laufen nacheinander für dieselbe Felge, deshalb ist die **Ø Bearbeitungszeit je
  Felge** die Summe aller Programme aller Spannungen.
- Felgen haben kein Jahr. Wie Aufträge schließen sie sich nach 7 Tagen ohne Lauf und öffnen sich
  beim nächsten Lauf wieder.
- Spannung 08/09 ist bei Felgen kein Vorrichtungsbau.
- Programmnamen ohne eines der beiden Schemata (z. B. `1301201.h`) zählen zu keinem Auftrag.

- Taucht ein Programm mit einer neuen Auftragsnummer an einer Maschine auf, wird der Auftrag
  **automatisch angelegt**. Schlüssel ist nur die Nummer (`21055`), das Jahr vorne im Programmnamen
  zählt nicht: `21-21055-01-01`, `25-21055-…` und `26-21055-…` gehören alle zum Auftrag 21055.
  Zusätze nach der Programmnummer (`26-21055-01-01_Schlichten.H`) und Unterstriche statt
  Bindestriche werden ebenfalls erkannt.
  - Bis Version 1.17.0 war das Jahr Teil des Schlüssels. Beim Update auf 1.18.0 werden Aufträge mit
    derselben Nummer aus verschiedenen Jahren einmalig zusammengeführt. Bezeichnung und Bild bleiben
    wie bei den Versionen erhalten.
- **Versionen:** `26-21053V1-01-01` und `26-21053V2-01-01` gehören zum Auftrag `21053`. Eine
  Version ist eine andere Ausführung des Teils.
  - Die Version steht direkt an der Nummer: `V` und eine ein- oder zweistellige Zahl. Ein kleines
    `v` zählt wie `V`.
  - In der Liste steht sie hinter der Nummer („21053 · V1, V2“). Die Suche findet den
    Auftrag auch über `21053V1`.
  - Laufzeit, Stopps und Läufe des Auftrags sind die Summe aller Versionen.
  - Im Detail hat jede Version einen eigenen Block („Grundversion“, „Version V1“ …) mit ihren
    Aufspannungen und ihrer **Ø Bearbeitungszeit je Teil**. Die Kachel nennt sie je Version.
  - Von Version 1.9.0 bis 1.16.0 waren Versionen eigene Aufträge. Beim Update auf 1.17.0 werden sie
    einmalig in den Grundauftrag übernommen. Bezeichnung und Bild des Grundauftrags bleiben; hat er
    keine, kommen sie von der Version.
- **Oberprogramme** werden übersprungen, z. B. ein Palettenprogramm auf der Automation, das die
  Auftragsprogramme per `CALL PGM` aufruft, oder eine **Palettentabelle** (`.P`), die sie abarbeitet:
  - Hat das angewählte Hauptprogramm keine Auftragsnummer, ruft aber ein Auftragsprogramm auf, zählen
    Lauf, Auftrag, Restlaufzeit und Werkzeugaufrufe für das aufgerufene Programm. Die Live-Karte zeigt
    „aufgerufen von PAL1“.
  - Kehrt die Steuerung ins Oberprogramm zurück, ist der Lauf des aufgerufenen Programms fertig.
  - Die Zeit im Oberprogramm selbst (z. B. Palettenwechsel zwischen zwei Aufrufen) ist Laufzeit
    der Maschine, gehört aber zu keinem Lauf und keinem Auftrag.
  - **Zwischenprogramme** wie eine Reinigung oder das Holen der nächsten Palette zählen genauso.
    Ein M30 im Auftragsprogramm ist dafür nicht nötig: Sobald die Steuerung ein anderes Programm
    meldet, entscheidet die App, wer es aufgerufen hat. Dazu liest sie die `CALL PGM`-Zeilen
    (auch `SEL PGM`, `SEL CYCLE` und Zyklus 12) aus den Programmdateien. Bei einer
    Palettentabelle liest sie die Spalte `NAME`, Pfade mit Leerzeichen eingeschlossen. Die Reihenfolge:
    1. Steht es in der Datei des Oberprogramms, endet der Lauf des Auftragsprogramms. Die Liste
       zählt nur, wenn auch das Auftragsprogramm darin steht, sonst ist sie unvollständig
       (z. B. Aufruf über Parameter).
    2. Steht es in der Datei des Auftragsprogramms, ist es ein Unterprogramm, und der Lauf geht weiter.
    3. Sonst kommt der Aufruf aus dem Oberprogramm, und der Lauf endet. Das gilt auch, wenn keine
       der beiden Dateien lesbar ist. Ausnahme: Programme auf `PLC:` (Makros des
       Maschinenherstellers) laufen im Auftragsprogramm weiter.
  - Programmdateien, die nicht gelesen werden konnten, versucht die App jede Minute erneut.
  - **Zur Kontrolle** zeigt die Live-Karte unter dem Programm, was über das Oberprogramm bekannt ist:
    „HAUPT.H ruft auf: …“ oder „HAUPT.H nicht gelesen: <Grund>“. Bei einer lesbaren
    Palettentabelle steht stattdessen die [Ablaufliste](#palettenprogramm-ablaufliste) auf der Karte.
    Der Verbindungstest zeigt es
    unter „Hauptprogramm lesen“, das Log beim Einlesen („ruft auf: …“).
  - Ruft das Oberprogramm dasselbe Auftragsprogramm mehrmals direkt hintereinander auf, ohne
    Zwischenprogramm, kann daraus ein einziger Lauf werden. Die Steuerung wird alle 2 s abgefragt,
    und die Zeilen dazwischen laufen meist schneller ab.
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
  - **Vorrichtung (Spannung 08 und 09):** Programme wie `26-21048-08-01` bauen eine Vorrichtung.
    - Sie stehen als eigene Karte „Vorrichtung (Spannung 08)“ im Auftrag.
    - Ihre Zeit zählt zur Laufzeit des Auftrags („davon Vorrichtung …“), aber nicht zur Ø-Bearbeitungszeit
      je Teil, denn sie ist ein einmaliger Aufwand.
  - Mehrere Fassungen desselben Programms (z. B. `26-21048-01-01` und `26-21048-01-01-neu`) zählen
    beide in die Ø-Zeit je Teil. Den alten Stand an der Maschine ersetzen oder seine Läufe hier
    löschen.
  - Laufzeit je Tag, alle Läufe, CSV-Export.
  - **Lauf löschen** (Programmdurchläufe → „Löschen“): für Fehlläufe oder ein Nachprogramm.
    - Der Lauf zählt danach nicht mehr zum Auftrag, zu den Ø-Stückzeiten (auch in der Auswertung
      je Programm) und zur Restlaufzeit-Prognose.
    - Die Laufzeit der Maschine bleibt in der Auswertung erhalten, denn die Maschine ist ja gelaufen.
      Die Zeit gehört dann zu keinem Lauf, wie bei verworfenen MDI-Läufen.
    - Nur beendete Läufe lassen sich löschen. Die App fragt vorher nach; rückgängig machen geht
      nicht.
    - Was gelöscht wurde, steht als Ereignis `run_deleted` in der Datenbank und in der
      Diagnose-Datei.
- **Bild je Auftrag** (fertiges Bauteil):
  - Am PC im Detail „Bild hinzufügen“, später „Bild ersetzen“ oder „Bild entfernen“.
  - **Aus der Zwischenablage:** ein Bild kopieren (z. B. Screenshot mit Win+Umschalt+S oder
    „Bild kopieren“ im Browser) und im geöffneten Auftrag Strg+V drücken. Ist schon ein Bild da,
    fragt die App vor dem Ersetzen. Text einfügen (z. B. in die Bezeichnung) funktioniert weiter
    wie gewohnt.
    - Der Button „Aus Zwischenablage“ erscheint nur, wenn der Browser ihn erlaubt: bei
      `http://localhost` (portable Version am selben PC) oder über https. Über die IP-Adresse
      (z. B. Unraid) geht Strg+V.
  - Am Handy zwei Buttons: „Foto aufnehmen“ öffnet direkt die Kamera, „Aus Galerie“ die
    Fotoauswahl. Getrennt deshalb, weil Android bei nur einem Button lediglich die Galerie zeigt.
  - Der Browser verkleinert das Foto vor dem Hochladen:
    - großes Bild: höchstens 1280 px an der längsten Kante, ca. 150–250 KB
    - Vorschaubild: 256 px, ca. 10–20 KB
  - Handyfotos erscheinen richtig herum. Die Kameradaten (EXIF, auch der GPS-Standort) werden
    dabei entfernt.
  - Kann der Browser eine Datei nicht als Bild lesen, erscheint ein Hinweis; ein vorhandenes
    Bild bleibt dann unverändert.
  - Das Vorschaubild steht in der Auftragsliste und auf der Live-Karte, solange der Auftrag an
    einer Maschine angewählt ist. Das große Bild erscheint nur im Detail; ein Klick öffnet es in
    voller Größe.
- **Automatisch abgeschlossen:** Läuft 7 Tage lang kein Programm eines Auftrags, schließt die App
  ihn selbst ab. Im Detail steht dann „abgeschlossen … (automatisch, 7 Tage ohne Programmlauf)“.
  - Gezählt wird ab dem Ende des letzten Laufs. Hat ein Auftrag noch nie ein Programm laufen
    lassen, zählt das Anlegen.
  - Ein laufender oder gestoppter Lauf hält den Auftrag offen, ein nur angewähltes Programm nicht.
  - Von Hand wieder geöffnet: Die 7 Tage beginnen neu.
  - Die App prüft das beim Start und dann stündlich.
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
- **Werkzeugdaten** (Bearbeiten bzw. + Werkzeug anlegen): Hersteller, Artikelnummer, Durchmesser
  und Radius in mm. Den Hersteller wählst du aus einer Liste, die du im Tab Konfiguration unter
  **Werkzeughersteller** pflegst. Sie stehen in der Liste unter dem Werkzeug, z. B. „Ø 10 mm · R 0,5 mm · Garant ·
  Art.-Nr. 202340“, und lassen sich suchen.
- **Standzeit** (Bearbeiten → Stunden, z. B. `100` oder `2,5`):
  - **Maximallaufzeit:** Der Balken zeigt, wie viel davon verbraucht ist.
  - **Vorwarnung bei:** Ab dieser Einsatzzeit wird das Werkzeug gelb markiert, im Balken zeigt ein
    Strich die Stelle. Sie muss kleiner als die Maximallaufzeit sein.
  - Neue Werkzeuge bekommen automatisch **100 h** mit Vorwarnung bei **80 h**, auch beim Anlegen von
    Hand.
  - Leer lassen heißt kein Limit bzw. Vorwarnung bei 90 % der Maximallaufzeit. So verhalten sich auch
    Werkzeuge, die vor Version 1.6.2 angelegt wurden, bis die Vorwarnung eingetragen ist.
  - Ab der Maximallaufzeit erscheint **„Über Limit“** in Rot: als Meldung oben auf der Seite, als rote
    Zahl am Reiter und auf der Live-Karte, solange das Werkzeug in der Spindel ist.
- **Zurücksetzen** nach dem Einspannen eines neuen Werkzeugs: Die Einsatzzeit beginnt wieder bei 0.
  Werkzeugdaten, Limit, Vorwarnung und Notiz bleiben. Der alte Stand kommt als **Standzeit** in die Historie (Bearbeiten →
  „Standzeiten bisher“, mit Ø Standzeit).
- **Aufrufe:** In jeder Zeile steht, wie oft das Werkzeug in die Spindel gewechselt wurde, z. B.
  „212× aufgerufen“.
  - Gezählt wird jeder Wechsel auf dieses Werkzeug, im Programm wie im Handbetrieb, seit Beginn der
    Erfassung. Zurücksetzen und Entfernen ändern daran nichts. Ein Wechsel aus der leeren Spindel
    (T0) zählt mit.
  - Nicht gezählt wird das Werkzeug, das beim Start der App schon in der Spindel steckt, sonst
    entstünde bei jedem Neustart ein Schein-Aufruf. Wechsel, während die App nicht läuft, fehlen.
  - **Meiste Aufrufe** sortiert jede Maschine absteigend nach Aufrufen. So siehst du, welche
    Werkzeuge du am häufigsten brauchst. Die Kachel „Meist aufgerufen“ zeigt das Spitzenwerkzeug.
- **Laufzeit gesamt:** Spalte neben den Aufrufen, die Einsatzzeit seit Beginn der Erfassung.
  - Anders als die Standzeit dahinter (seit dem letzten Zurücksetzen, mit Balken und Limit) beginnt
    sie beim Zurücksetzen nicht neu.
  - Die Kachel „Meiste Laufzeit“ zeigt das Werkzeug mit der längsten Laufzeit gesamt.
- **Sortieren** über die Spaltenköpfe direkt über der Liste jeder Maschine: „Werkzeug“ (T-Nummer
  aufsteigend), „Aufrufe“ oder „Laufzeit gesamt“ (jeweils die meisten oben).
  - Der Pfeil zeigt die aktive Sortierung. Sie gilt für alle Maschinen und steht in der Adresse
    (`?sort=calls` bzw. `?sort=runtime`).
  - Bei Gleichstand kommt die kleinere T-Nummer zuerst.
  - **Top-Werkzeuge fürs Magazin:** Sind in der Konfiguration die Werkzeugplätze der Maschine
    eingetragen (z. B. 30), tragen die 30 meistgebrauchten Werkzeuge die Marke „Top 30 · Platz 3“.
    Bei „Meiste Aufrufe“ zeigt eine gestrichelte Linie, wo das Magazin endet. Bei gleich vielen
    Aufrufen kommt die kleinere T-Nummer zuerst. Werkzeuge ohne Aufruf zählen nie zu den Top.
  - Beim Update auf 1.9.0 werden die Werkzeugwechsel nachgetragen, die seit Version 1.0
    gespeichert sind.
  - Jeder Aufruf wird mit Zeitpunkt gespeichert (Tabelle `tool_calls`), damit sich später auch
    Auswertungen je Zeitraum machen lassen.
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
- Filter nach Maschine und Status, Suche nach T-Nummer, Name, Hersteller, Artikelnummer oder Notiz,
  CSV-Export mit allen Werkzeugdaten (Aufrufe und Laufzeit gesamt in den letzten Spalten).

Ob beides an der iTNC 530 funktioniert, zeigt der Verbindungstest:

- Unter „Statusabfrage“ steht `Werkzeug T12` oder „Werkzeug: keine Angabe“.
- Unter „Werkzeugtabelle lesen“ steht z. B. „245 Werkzeuge mit Namen, in der Spindel: T12
  FRAESER_D16“.

## Nur lesend (Schreibschutz)

Die App verändert oder löscht nichts an der Maschine. Sie liest nur:
- Status: Programmstatus, Betriebsart, Programm und Satz, Override, Fehlermeldungen, Werkzeug in
  der Spindel
- Steuerungstyp und Softwarestand
- das angewählte NC-Programm (für die Satzanzahl)
- die Werkzeugtabelle `TOOL.T` (für die Werkzeugnamen)

Programm und Werkzeugtabelle werden auf den Rechner kopiert und nur dort ausgewertet.

**Technischer Schutz:** Jede Verbindung zur Steuerung läuft über einen Schreibschutz
(`app/adapters/lsv2_guard.py`). Er prüft jeden einzelnen LSV2-Befehl vor dem Senden gegen eine
Positivliste:

| erlaubt | Zweck |
|---|---|
| Anmelden / Abmelden (nur INSPECT, FILE, DNC) | Leserechte für diese Verbindung |
| R_VR, R_PR, R_CI | Version, Schnittstellenparameter, Systeminfo lesen |
| R_RI | Status lesen |
| R_FI, R_FL | Dateiinfo und Datei lesen |
| C_CC nur Puffergröße und Übertragungsart | Einstellung dieser Verbindung, keine Daten der Steuerung |

Alles andere wird blockiert, bevor es den Rechner verlässt, zum Beispiel Datei senden (C_FL),
löschen (C_FD), kopieren (C_FC), umbenennen (C_FR), Ordner anlegen oder löschen (C_DM, C_DD),
Maschinenparameter (C_MC), Tastendruck (C_EK), Tastatursperre (C_LK), Steuerung zurücksetzen und
Anmeldungen wie PLCDEBUG. Lässt sich der Schutz nicht einrichten (z. B. nach einem Update von
pyLSV2), baut die App keine Verbindung auf. Tests mit einer nachgebauten Steuerung prüfen das
(`tests/test_readonly.py`).

## Begriffe der Auswertung

| Zustand | Bedeutung (Heidenhain-Programmstatus) |
|---|---|
| **Läuft** | Programm gestartet (`STARTED`) |
| **Gestoppt** | NC-Stopp bzw. Programm unterbrochen (`STOPPED`, `INTERRUPTED`) |
| **Fehler** | Programm mit Fehler angehalten (`ERROR`) |
| **Bereit** | kein Programm aktiv (`IDLE`, `FINISHED`, `CANCELLED`, `ERROR_CLEARED`) |
| **Offline** | Steuerung nicht erreichbar (Maschine aus, Netzwerk weg) |
| **Keine Daten** | das Tool lief nicht oder der Standort war nicht erreichbar (Prüfadresse); die Zeit wird nicht geschätzt |

- **Programmdurchlauf:** beginnt bei „Läuft“ im Programmlauf und bleibt über Stopps und Fehler offen.
  Er endet, wenn die Maschine „Bereit“ meldet oder ein anderes Programm läuft. Kurze
  Verbindungsabbrüche beenden keinen Lauf. Ergebnis: fertig, abgebrochen, Fehler oder unterbrochen.
  - **Handbetrieb und MDI** („Positionieren mit Handeingabe“): Auch hier meldet die Steuerung
    „gestartet“, z. B. für einen MDI-Satz oder ein Makro. Daraus entsteht kein Lauf. Die Zeit zählt
    als Laufzeit der Maschine, aber zu keinem Programm und keinem Auftrag.
  - **Fertig** ist ein Lauf, wenn die Steuerung „beendet“ meldet. Die iTNC 530 meldet nach dem
    Programmende gleich „inaktiv“. Das zählt als fertig, wenn das Programm bis zuletzt im
    Programmlauf lief. Gestoppt und dann abgebrochen bleibt „unterbrochen“.
  - Ein Abbruch gleich nach dem Start geht bei der iTNC 530 ebenfalls direkt auf „inaktiv“. Kam das
    Programm nicht über seine ersten Sätze hinaus (5 %, höchstens die Hälfte), ist der Lauf
    „unterbrochen“, nicht fertig.
  - **Satzvorlauf:** Wird ein Programm dort per Satzvorlauf wieder gestartet, wo ein beendeter Lauf
    endete (z. B. nach einer Störung), läuft dieser Lauf weiter. Ein Teil ergibt so einen Lauf.
    - Das gilt bis zu 12 Stunden später und für einen der letzten 10 Läufe.
    - Dazwischen darf ein anderes Programm (z. B. die Vorrichtung) oder ein Fehlstart desselben
      Programms liegen.
    Beginnt ein Lauf sonst mitten im Programm (jenseits von 5 % der Sätze), ist er ein Teillauf und
    zählt nicht in die Ø-Stückzeiten und die Restlaufzeit.
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
- **Bilder** liegen als Dateien im Datenordner, nicht in der Datenbank; dort steht nur der
  Dateiname:
  - `images/`: Maschinenbilder
  - `images/orders/`: Auftragsbilder, je Auftrag ein großes Bild und ein Vorschaubild,
    z. B. `26-21055-1a2b3c4d.jpg` und `26-21055-1a2b3c4d-thumb.jpg`
  - Größenordnung: 600 Aufträge mit Bild ≈ 100–150 MB.
  - Fehlt eine Bilddatei, gilt der Auftrag als „ohne Bild“; es entsteht kein Fehler.
- **Sicherung:** immer den ganzen Datenordner sichern, also Datenbank **und** `images/`. Eine
  gesicherte `data.db` allein enthält nur die Dateinamen der Bilder.

## Fehler melden (Diagnose-Datei)

Unter **Konfiguration → Diagnose** lädt „Diagnose-Datei herunterladen“ eine ZIP-Datei
`ld-diagnose_<Datum>_<Uhrzeit>.zip` herunter. Sie gehört zu jeder Fehlermeldung. Zeitraum: 1, 7
oder 30 Tage, auf Wunsch mit der ganzen Datenbank. Inhalt:

| Datei | Inhalt |
|---|---|
| `info.json` | Version, Build, Startzeit, Einstellungen, Maschinen mit Verbindungsstatus |
| `live.json` | Live-Status aller Maschinen beim Erstellen |
| `mitschnitt_<maschine>.csv` | Jede Änderung der Steuerungsdaten seit dem Start der App (bis 5000 je Maschine): Programmstatus, Betriebsart, Haupt- und aktuelles Programm, Satz davor und danach, Werkzeug, Fehlermeldungen. Dazu, was die Erfassung daraus gemacht hat (gezähltes Programm, Oberprogramm, Lauf). |
| `laeufe.csv` | Läufe mit Ergebnis und Programmstatus am Laufende und danach |
| `zustaende.csv`, `ereignisse.csv` | Zustandsabschnitte und Ereignisse des Zeitraums |
| `programmdateien.csv` | Gelesene Programmdateien mit Satzanzahl, aufgerufenen Programmen und Fehlern |
| `paletten/<maschine>/` | Palettentabellen (`.P`) im Original, wie zuletzt von der Steuerung gelesen |
| `logs/` | Log-Dateien der App |
| `data.db` | Datenbank (nur wenn ausgewählt) |

Die Datei enthält Maschinenadressen und Programmnamen, aber keine Passwörter. Direkt abrufbar ist sie
auch über `/api/diagnose.zip?days=7&db=true`.

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
| `DELETE /api/orders/{key}/runs/{id}` | Beendeten Lauf aus dem Auftrag löschen (Zeit bleibt Maschinenzeit) |
| `GET /api/orders/{key}/image?size=full\|thumb` | Bild des Auftrags (großes Bild bzw. Vorschaubild) |
| `PUT/DELETE /api/orders/{key}/image` | Bild setzen/ersetzen bzw. entfernen. Upload: großes Bild und Vorschaubild (beide JPEG) hintereinander in einem Rumpf, Kopfzeile `X-Image-Length` = Länge des großen Bildes |
| `GET/POST/PUT/DELETE /api/config/...` | Konfiguration: Maschinen, Bild, Reihenfolge, Verbindungstest |
| `GET /api/tools`, `POST /api/tools` | Werkzeuge (Liste, von Hand anlegen) |
| `PUT/DELETE /api/tools/{maschine}/{nr}`, `POST …/reset` | Werkzeugdaten/Standzeit, Entfernen, Zurücksetzen |
| `GET /api/tools/export.csv` | Werkzeugliste als CSV |
| `GET /api/version` | Versionsnummer, Build-Kennung, Änderungsprotokoll |
| `GET /api/diagnose.zip?days=&db=` | Diagnose-Datei für Fehlermeldungen (siehe oben) |

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
   | Icon-URL (*Icon URL*) | `https://raw.githubusercontent.com/Bademeister89/ld-maschinenlaufzeit/main/app/static/icons/icon-512.png` |
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
- **Symbol bei einem schon angelegten Container:** Icon → **Bearbeiten** → erweiterte Ansicht →
  bei *Icon URL* die Adresse aus der Tabelle oben eintragen → **Anwenden**.
- **Version festhalten oder zurück:** Icon → **Bearbeiten**, bei *Repository* statt `:latest` die
  Versionsnummer eintragen, z. B. `ghcr.io/bademeister89/ld-maschinenlaufzeit:1.2.0` (feste Images
  gibt es ab Version 1.2.0), **Anwenden**.
  Zurück zu automatischen Updates mit `:latest`. Vor einem Wechsel auf eine ältere Version
  `appdata/ld-maschinenlaufzeit` sichern.
- **Sicherung:** `appdata/ld-maschinenlaufzeit` komplett sichern (Datenbank und Ordner `images/`),
  z. B. mit dem Plugin „Appdata Backup“.
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
| `portable/` | Start-, Autostart- und Verknüpfungs-Skripte der portablen Version |
| `app/static/icons/` | App-Symbol (Browser, Startbildschirm, Unraid, Windows-Verknüpfung) |
| `tools/` | `probe.py`, `seed_demo.py`, `build_portable.ps1` |
| `docker/`, `Dockerfile` | Container (Start ohne Root-Rechte) |
| `unraid/` | Vorlage für die Unraid-Oberfläche |
| `.github/workflows/` | Tests und Docker-Image (GitHub Actions) |

## Lizenz

MIT, siehe [LICENSE](LICENSE). Die Anbindung nutzt [pyLSV2](https://github.com/drunsinn/pyLSV2)
(ebenfalls MIT). HEIDENHAIN, TNC und iTNC sind Marken der DR. JOHANNES HEIDENHAIN GmbH; dieses
Projekt steht in keiner Verbindung zu HEIDENHAIN oder DMG MORI.
