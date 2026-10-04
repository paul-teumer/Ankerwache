# Ankerwache

Ein ausgedientes Android-Telefon wird zur Ankerwache: Es merkt, wenn das Boot vom Anker treibt, und alarmiert per SMS und Telegram. Steuerung per Telegram-Gruppe oder SMS, zusätzlich Track-Aufzeichnung und regelmäßige Lebenszeichen. Mit dem Befehl `DATEN AUS` läuft alles ohne mobile Daten, nur über SMS.

> **Haftungsausschluss:** Eigenentwicklung ohne Gewähr. Die Ankerwache ersetzt weder die Wache an Bord noch eine zugelassene Ankeralarmanlage. Testen Sie sie vor dem Einsatz ausgiebig (Abnahme: [Bauanleitung](Bauanleitung.md), Abschnitt 7).

## Was Sie brauchen

- Ein Android-Telefon (64 Bit/arm64), entwickelt und getestet mit einem Samsung Galaxy S7 unter Android 8
- Eine SIM-Karte im Telefon mit SMS-Guthaben, für Telegram zusätzlich mobile Daten
- Einen Windows-Rechner mit Internet und ein USB-Kabel
- Ein kostenloses Telegram-Konto (auf Ihrem eigenen Handy)
- Etwa 20 Minuten

## Einrichtung in 5 Schritten

1. **Telefon vorbereiten.** Falls Termux aus dem Play Store installiert ist, deinstallieren. Dann: *Einstellungen > Über das Telefon > Build-Nummer* siebenmal antippen, danach *Einstellungen > Entwickleroptionen > USB-Debugging* einschalten.
2. **Projekt herunterladen.** Auf dieser Seite oben rechts *Code > Download ZIP* wählen und das ZIP auf dem Rechner entpacken.
3. **Telefon per USB anschließen.** Am Telefon die Frage „USB-Debugging zulassen?“ mit *Immer zulassen* bestätigen.
4. **`Einrichten.bat` doppelklicken.** Der Assistent führt Sie durch alles Weitere. Er lädt die nötigen Programme selbst von den offiziellen Quellen, legt mit Ihnen den Telegram-Bot und die Gruppe an und fragt, welche Zusätze Sie wollen (Tabelle unten). Fragt Windows „Der Computer wurde durch Windows geschützt“, wählen Sie *Weitere Informationen > Trotzdem ausführen*; fragt Play Protect am Telefon nach, wählen Sie *Trotzdem installieren*.
5. **Fertig.** In Ihrer Telegram-Gruppe erscheint „Ankerwache gestartet“. Antworten Sie mit `POSITION`, um die Ankerwache zu prüfen.

Bei Telefonen von Huawei, Honor oder Xiaomi sind danach noch einige Energiespar-Schalter nötig, die der Assistent am Ende nennt (Details: [Bauanleitung](Bauanleitung.md), Abschnitt 2).

## Was der Assistent fragt

Wählen Sie bei der ersten Frage *1*, übernimmt der Assistent die empfohlenen Einstellungen ohne weitere Fragen. Mit *2* entscheiden Sie jeden Punkt selbst. Die Ankerwache selbst (Termux, Termux:API, Termux:Boot, GPSLogger) wird immer eingerichtet.

| Punkt | Empfehlung | Bedeutung |
|---|---|---|
| Fernzugriff: Tailscale, RustDesk, SSH | nein | Erreicht das Telefon aus der Ferne; bei Ja folgt eine Frage je Baustein. Für den Alarm nicht nötig |
| Zusätzliche nützliche Apps: F-Droid, Aurora Store, GPSTest | nein | Haben mit der Ankerwache nichts zu tun; jeweils einzeln wählbar, mit Erklärung im Assistenten |
| Mobile Daten per SMS schalten, GPS-Dienst selbst reparieren | ja | Aktiviert ADB im Telefon selbst; gilt bis zum nächsten Neustart des Telefons |
| Datensparmodus | nein | Nur Termux, Tailscale und RustDesk dürfen im Hintergrund mobile Daten nutzen, alle anderen Apps nicht |
| Nicht benötigte Apps deaktivieren | nein | Spart Akku, schaltet vorinstallierte Apps ab (nicht gelöscht, jederzeit wieder aktivierbar) |
| Huawei/Honor-Energiedienst entfernen | ja (nur auf diesen Telefonen gefragt) | Sonst beendet Android die Ankerwache im Hintergrund |
| Meldeweg und Lebenszeichen | Telegram, täglich | Ob Statusmeldungen per Telegram, SMS oder beidem kommen und wie oft |

Der Alarm bei Ankerdrift kommt immer per SMS und Telegram.

## Befehle (per Telegram oder SMS)

| Befehl | Wirkung |
|---|---|
| `SETZE ANKER` | Anker auf die aktuelle Position setzen |
| `ALARM EIN` / `ALARM EIN 80` | Überwachung einschalten, Radius automatisch oder 80 Meter |
| `ALARM AUS` | Überwachung ausschalten |
| `POSITION`, `STATUS`, `TRACK`, `HILFE` | Position, Zustand, Fahrtverlauf, Befehlsübersicht |

Alle Befehle und Meldungen: [Bauanleitung](Bauanleitung.md), Abschnitte 4 und 5.

## Wenn etwas nicht klappt

- **„Kein Gerät“:** Kabel prüfen, USB-Debugging einschalten, die Zugriffsfrage am Telefon bestätigen; nur ein Telefon anschließen.
- **„Dateien konnten nicht geladen werden“:** Internet am Rechner prüfen und `Einrichten.bat` erneut starten; bereits geladene Dateien bleiben erhalten. Alternativ die Dateien selbst in die Ordner `lokal/apps` und `lokal/platform-tools` legen.
- **Keine Meldung in der Gruppe:** Stand das Telefon im WLAN oder Mobilnetz? Zum Prüfen im Telefon Termux öffnen und `tail ~/ankerwache/daten/protokoll.txt` eingeben.
- **Nach einem Neustart des Telefons:** Die Ankerwache startet selbst. Für `DATEN EIN/AUS` und die GPS-Selbstreparatur das Telefon erneut per USB anschließen und `adb tcpip 5555` ausführen (Bauanleitung, Abschnitt 2).

## Aufbau des Projekts

| Pfad | Zweck |
|---|---|
| `Einrichten.bat` | Einstieg per Doppelklick |
| `einrichtung/` | Assistent für den Rechner (`einrichten.ps1`, `konfiguration-erstellen.ps1`, `dateien-herunterladen.ps1`), Skript für Termux (`einrichten.sh`), Liste der abschaltbaren Apps, GPSLogger-Patch |
| `Bauanleitung.md` | Ausführliche Anleitung: Befehle, Meldungen, Fernzugriff, Handeinrichtung |
| `programm/` | Die Ankerwache selbst (`ankerwache.py`), ihre Start- und Wächterskripte für Termux, die Konfigurationsvorlage und die automatischen Tests (`python -m pytest`) |
| `lokal/` | Nur auf Ihrem Rechner, nie im Repository: `konfiguration.json` (Telegram-Zugang, Rufnummern), `ssh-schlüssel.pub`, `pakete/` sowie die vom Assistenten geladenen Ordner `apps/` und `platform-tools/` |

## Fremdsoftware

Der Assistent lädt nur unveränderte Programme von den Herstellern bzw. von F-Droid: Termux, Termux:API, Termux:Boot und je nach Auswahl Tailscale, RustDesk, F-Droid, Aurora Store, GPSTest sowie die Android Platform-Tools von Google; jeweils unter deren eigenen Lizenzen. GPSLogger (GPLv2) wird in einer gepatchten Fassung verwendet, die einen Speicherfehler behebt, der unter Android 8 das Telefon nach etwa einer Stunde lahmlegt; die Änderung ist in [einrichtung/gpslogger-patch/README.md](einrichtung/gpslogger-patch/README.md) vollständig beschrieben. Die gepatchte Datei stammt aus den Releases dieses Projekts; dort liegt zusätzlich die unveränderte Originaldatei, und der Quelltext des Originals steht unter <https://github.com/mendhak/gpslogger> (Version 137).

## Lizenz

Copyright © 2026 Paul Teumer. Das Projekt steht unter der [GNU General Public License, Version 3](LICENSE): Sie dürfen es frei nutzen, ändern und weitergeben, müssen aber jede weitergegebene Fassung samt Quelltext unter derselben Lizenz offenlegen. Eine Verwendung in Closed-Source-Produkten ist damit ausgeschlossen. Wer den Code anders verwerten möchte, etwa in einem geschlossenen kommerziellen Produkt, braucht eine gesonderte Lizenz des Urhebers; Anfragen bitte über das GitHub-Profil [paul-teumer](https://github.com/paul-teumer).

Ausgenommen ist der Ordner `einrichtung/gpslogger-patch/`: Er beschreibt eine Änderung an GPSLogger und steht wie dieses unter der [GPLv2](einrichtung/gpslogger-patch/LICENSE).
