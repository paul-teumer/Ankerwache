"""Ankerwache: Ankeralarm und Tracking auf einem Android-Gerät unter Termux.

Alarm per SMS und Telegram. Lebenszeichen, Warnungen und Befehle wahlweise per SMS oder über eine Telegram-Gruppe.
"""

import csv
import errno
import html
import http.client
import json
import logging
import math
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROGRAMMORDNER = Path(__file__).resolve().parent
DATENORDNER = PROGRAMMORDNER / "daten"
GPSLOGGER_ORDNER = Path("/storage/emulated/0/Android/data/com.mendhak.gpslogger/files")
GPSLOGGER_HÖCHSTALTER_SEKUNDEN = 60
GPSLOGGER_AUFBEWAHRTE_DATEIEN = 2
# GPSLogger kann abstürzen und startet dann nicht von selbst; ohne GPS-Empfang hilft ein Neustart nicht,
# deshalb nur in großem Abstand.
GPSLOGGER_NEUSTART_ABSTAND_SEKUNDEN = 600
# GPSLogger hält das GPS mit einer Messung je Sekunde an; jüngere Positionen liefert Termux:API sofort.
LETZTE_POSITION_HÖCHSTALTER_SEKUNDEN = 10
# "termux-location -r once" wartet ohne Empfang endlos; Android beendet Termux:API dann nach 60 Sekunden und mit ihm
# jeden anderen laufenden Aufruf. Länger warten, damit der nächste Aufruf erst das neu gestartete Termux:API trifft.
EINZELABRUF_ZEITLIMIT_SEKUNDEN = 70
EINZELABRUF_PAUSE_NACH_FEHLSCHLAG_SEKUNDEN = 600
# Ein Android-Fehler lässt die Weitergabe der GPS-Rohdaten im Systemprozess auflaufen: Ein HwBinder-Thread läuft
# dann dauerhaft unter Volllast und blockiert alle Standortanfragen. Im Normalbetrieb liegt seine Auslastung nahe null.
GPS_HÄNGER_MESSDAUER_SEKUNDEN = 10
GPS_HÄNGER_AUSLASTUNG = 0.5
GPS_AUS_ZUR_BEHEBUNG_SEKUNDEN = 30
# Zeiteinheit der Rechenzeiten in /proc (USER_HZ des Linux-Kernels).
TAKTE_PRO_SEKUNDE = 100

ERDRADIUS_METER = 6371008.8
METER_PRO_SEKUNDE_JE_KNOTEN = 1852 / 3600
AUFEINANDERFOLGENDE_POSITIONEN_FÜR_ALARM = 3
HÖCHSTALTER_POSITION_FÜR_BEFEHLE_SEKUNDEN = 120
GPS_ABFRAGEABSTAND_SEKUNDEN = 3
MESSDAUER_SETZE_ANKER_SEKUNDEN = 15
# Entwarnung erst nach stabilem Empfang, damit ein schwankendes Signal nicht ständig Meldungen erzeugt.
GPS_STABIL_FÜR_ENTWARNUNG_SEKUNDEN = 1800
GPS_HÖCHSTE_LÜCKE_BEI_STABILEM_EMPFANG_SEKUNDEN = 120
# Später zugestellte Meldungen tragen ihren Entstehungszeitpunkt, weil ihr Inhalt dann veraltet sein kann.
VERSPÄTUNG_FÜR_HINWEIS_SEKUNDEN = 300
HÄNGENDE_MESSSCHLEIFE_SEKUNDEN = 300
HÖCHSTALTER_BEFEHL_SEKUNDEN = 300
# Jede Abfrage bei Telegram kostet Daten; Alarme, Meldungen und SMS-Befehle sind vom Abstand nicht betroffen.
TELEGRAM_ABRUFABSTAND_SEKUNDEN = 300
BATTERIE_ENTWARNUNG_PROZENT = 30
TRACK_ZEITRAUM_SEKUNDEN = 7 * 86400
# Bei Netz- oder Serverfehlern verdoppelt sich die Pause bis zur Höchstdauer, damit eine lange Störung kaum Daten kostet.
ERSTE_WARTEZEIT_NACH_FEHLER_SEKUNDEN = 600
HÖCHSTE_WARTEZEIT_NACH_FEHLER_SEKUNDEN = 1800
WARTEZEIT_OHNE_NETZ_SEKUNDEN = 60
# Fehlende oder gerade wegfallende Netzverbindung, etwa kurz nach DATEN EIN oder DATEN AUS.
FEHLERCODES_OHNE_NETZ = {errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH, errno.ECONNABORTED}
# DATEN EIN beendet die Pause nach Fehlern; so oft wird geprüft, ob die Daten eingeschaltet wurden.
PRÜFABSTAND_WÄHREND_PAUSE_SEKUNDEN = 10
# Eine Absturzschleife soll nicht bei jedem Neustart eine Meldung senden.
STARTMELDUNG_MINDESTABSTAND_SEKUNDEN = 3600
SMS_ABFRAGEABSTAND_SEKUNDEN = 20
SMS_ABGEFRAGTE_NACHRICHTEN = 20
# Eine SMS, die das Modem während der Übertragung einer anderen erhält, überträgt es wiederholt, und das Netz berechnet
# sie doppelt. Die nächste SMS geht daher erst raus, wenn Android die vorige als gesendet abgelegt hat.
SMS_SENDEBESTÄTIGUNG_HÖCHSTDAUER_SEKUNDEN = 60
# Android hält eine SMS ohne Netz nicht zurück; wartende SMS gehen spätestens so lange nach Rückkehr des Netzes raus.
SMS_WIEDERHOLUNGSABSTAND_SEKUNDEN = 30
# Nach ITU-T E.164 hat die Ländervorwahl 1 bis 3 Ziffern, die ganze Nummer höchstens 15; 0 für Absender,
# die ohne + mit Ländervorwahl ankommen.
LÄNDERVORWAHL_ZIFFERN = range(0, 4)
RUFNUMMER_ZIFFERN = range(8, 16)
# Die Antwort auf DATEN AUS per Telegram muss vor dem Abschalten versendet sein.
DATEN_AUS_VERZÖGERUNG_SEKUNDEN = 20
ADB_ZIEL = "127.0.0.1:5555"
FERNZUGRIFF_PAKETE = ("com.carriez.flutter_hbb", "com.tailscale.ipn")
# Beschriftungen der deutschen Oberfläche von RustDesk bis zum gestarteten Dienst.
RUSTDESK_STARTSCHRITTE = ("Bildschirm freigeben", "Vermittlungsdienst starten", "OK")
OBERFLÄCHE_WARTEZEIT_SEKUNDEN = 4
TAILSCALE_STARTVERSUCHE = 3
TAILSCALE_VERBINDUNGSAUFBAU_SEKUNDEN = 10
MUSTER_BEDIENELEMENT = re.compile(r'content-desc="([^"]*)".*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')

KANAL_TELEGRAM, KANAL_SMS = "telegram", "sms"
MELDEWEGE = {"TELEGRAM": "telegram", "SMS": "sms", "BEIDE": "beide"}
MELDEWEG_TEXT = {"telegram": "per Telegram", "sms": "per SMS", "beide": "per SMS und Telegram"}
LEBENSZEICHEN_HÄUFIGKEITEN = {"TAEGLICH": "täglich", "WOECHENTLICH": "wöchentlich", "MONATLICH": "monatlich"}
LEBENSZEICHEN_TERMIN_TEXT = {"täglich": "täglich", "wöchentlich": "montags", "monatlich": "am 1. des Monats"}
# SMS nur in ASCII, damit jede Nachricht in das 7-Bit-Alphabet mit 160 Zeichen je SMS passt.
GSM_ERWEITERUNGSZEICHEN = "|^{}[]~\\€"
UMSCHRIFT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "–": "-"})

CSV_KOPFZEILE = ["Zeitpunkt", "Breite", "Länge", "Genauigkeit", "Geschwindigkeit", "Abstand", "Gültig"]

BEFEHLSÜBERSICHT = """<b>Befehle</b> (per Telegram oder per SMS von einem SMS-Empfänger, Antwort auf demselben Weg)
<b>POSITION</b> – Position, Abstand zum Anker, Status
<b>SETZE ANKER</b> – Anker auf aktuelle Position
<b>SETZE ANKER 43.123456, -5.678901</b> – Anker auf Koordinaten (Dezimalgrad, Süd und West negativ)
<b>ALARM EIN</b> – Radius = aktueller Abstand + {zuschlag} Meter
<b>ALARM EIN 80</b> – Radius 80 Meter
<b>ALARM AUS</b> – Überwachung aus
<b>GENAUIGKEIT 20</b> – nur Positionen mit 20 Metern Genauigkeit oder besser zählen für den Alarm
<b>GPS WARNUNG 30</b> – Warnung, wenn 30 Minuten lang keine gültige Position kommt (5 bis 240)
<b>TRACK</b> – Track der letzten 7 Tage als GPX-Datei; per SMS der weiteste Abstand vom Anker
<b>MELDUNGEN TELEGRAM</b> – Lebenszeichen, Start und Warnungen per Telegram; <b>SMS</b> nur per SMS, <b>BEIDE</b> auf beiden Wegen. Der Alarm kommt immer per SMS.
<b>LEBENSZEICHEN TÄGLICH</b> – Status täglich um {uhrzeit} Uhr; <b>WÖCHENTLICH</b> montags, <b>MONATLICH</b> am 1. des Monats
<b>DATEN AUS</b> – mobile Daten und WLAN aus, danach Befehle und Meldungen nur per SMS; <b>DATEN EIN</b> schaltet beides ein
<b>FERNZUGRIFF AUS</b> – RustDesk und Tailscale deaktivieren, sie nutzen dann keine Daten; <b>FERNZUGRIFF EIN</b> startet beide wieder
<b>EMPFÄNGER HINZU +491701234567</b> – SMS-Empfänger hinzufügen (mit Ländervorwahl); <b>EMPFÄNGER ENTFERNEN +491701234567</b> entfernt ihn. Nur SMS-Empfänger dürfen per SMS Befehle senden.
<b>STATUS</b> oder <b>GERÄTESTATUS</b> – Netz, WLAN, Telegram, SMS-Empfänger, SMS im Monat, Batterie, Einstellungen
<b>HILFE</b> – diese Übersicht"""

SMS_BEFEHLSÜBERSICHT = ("POSITION | SETZE ANKER [Breite, Laenge] | ALARM EIN [Radius] | ALARM AUS | "
                        "GENAUIGKEIT 1-100 | GPS WARNUNG 5-240 | TRACK (weitester Abstand) | "
                        "MELDUNGEN SMS/TELEGRAM/BEIDE | LEBENSZEICHEN TAEGLICH/WOECHENTLICH/MONATLICH | "
                        "DATEN, FERNZUGRIFF EIN/AUS | "
                        "EMPFAENGER HINZU/ENTFERNEN +49 | STATUS")

MUSTER_ANKER_KOORDINATEN = re.compile(r"^SETZE ANKER (-?\d+(?:\.\d+)?)(?: ?[,;] ?| )(-?\d+(?:\.\d+)?)$")
MUSTER_ALARM_RADIUS = re.compile(r"^ALARM EIN (\d+)$")
MUSTER_GENAUIGKEIT = re.compile(r"^GENAUIGKEIT (\d+)$")
GENAUIGKEITSGRENZE_BEREICH_METER = range(1, 101)
MUSTER_GPS_WARNUNG = re.compile(r"^GPS WARNUNG (\d+)$")
GPS_WARNUNG_BEREICH_MINUTEN = range(5, 241)
MUSTER_MELDUNGEN = re.compile(rf"^MELDUNGEN ({'|'.join(MELDEWEGE)})$")
MUSTER_LEBENSZEICHEN = re.compile(rf"^LEBENSZEICHEN ({'|'.join(LEBENSZEICHEN_HÄUFIGKEITEN)})$")
MUSTER_EMPFÄNGER = re.compile(r"^EMPFAENGER (HINZU|ENTFERNEN) ([+\d][\d ]*)$")
DATENVERBINDUNG_TEXT = {"connected": "verbunden", "connecting": "verbindet", "disconnected": "getrennt",
                        "suspended": "unterbrochen"}

protokoll = logging.getLogger("ankerwache")


@dataclass
class Position:
    breite: float
    länge: float
    genauigkeit: float | None
    geschwindigkeit: float | None
    zeitpunkt: float


def abstand_meter(breite1, länge1, breite2, länge2):
    breitenwinkel1, breitenwinkel2 = math.radians(breite1), math.radians(breite2)
    breitendifferenz = breitenwinkel2 - breitenwinkel1
    längendifferenz = math.radians(länge2 - länge1)
    haversinus = (math.sin(breitendifferenz / 2) ** 2
                  + math.cos(breitenwinkel1) * math.cos(breitenwinkel2) * math.sin(längendifferenz / 2) ** 2)
    return 2 * ERDRADIUS_METER * math.asin(math.sqrt(haversinus))


def peilung_grad(breite1, länge1, breite2, länge2):
    breitenwinkel1, breitenwinkel2 = math.radians(breite1), math.radians(breite2)
    längendifferenz = math.radians(länge2 - länge1)
    ostanteil = math.sin(längendifferenz) * math.cos(breitenwinkel2)
    nordanteil = (math.cos(breitenwinkel1) * math.sin(breitenwinkel2)
                  - math.sin(breitenwinkel1) * math.cos(breitenwinkel2) * math.cos(längendifferenz))
    return (math.degrees(math.atan2(ostanteil, nordanteil)) + 360) % 360


def koordinaten_text(breite, länge):
    return f"{breite:.6f}, {länge:.6f}"


def fahrt_text(position):
    if position.geschwindigkeit is None:
        return "unbekannt"
    return f"{position.geschwindigkeit / METER_PRO_SEKUNDE_JE_KNOTEN:.1f} Knoten"


def alter_text(sekunden):
    if sekunden < 120:
        return f"{sekunden:.0f} Sekunden"
    if sekunden < 7200:
        return f"{sekunden / 60:.0f} Minuten"
    return f"{sekunden / 3600:.0f} Stunden"


def alter_kurztext(sekunden):
    if sekunden < 120:
        return f"{sekunden:.0f}s"
    if sekunden < 7200:
        return f"{sekunden / 60:.0f}min"
    return f"{sekunden / 3600:.0f}h"


def kartenverweis(breite, länge):
    return f"https://www.google.com/maps?q={breite:.6f},{länge:.6f}"


def utc_zeitstempel(zeitpunkt):
    return datetime.fromtimestamp(zeitpunkt, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_datum(zeitpunkt):
    return datetime.fromtimestamp(zeitpunkt, timezone.utc).strftime("%Y-%m-%d")


def sms_fassung(text):
    return re.sub(r"<[^>]+>", "", text).translate(UMSCHRIFT)


def sms_anzahl(text):
    """Zahl der abgerechneten SMS: Eine lange Nachricht wird in Teile zu 153 Zeichen zerlegt; Zeichen der
    GSM-Erweiterungstabelle belegen zwei Zeichen."""
    länge = len(text) + sum(text.count(zeichen) for zeichen in GSM_ERWEITERUNGSZEICHEN)
    return 1 if länge <= 160 else math.ceil(länge / 153)


def sms_hindernis(telefonie):
    """Grund, warum gerade keine SMS gesendet werden kann, oder None."""
    if not telefonie:
        return "Mobilfunkstatus unbekannt"
    if telefonie.get("sim_state") != "ready":
        return "keine SIM-Karte bereit"
    if not telefonie.get("network_operator_name"):
        return "kein Mobilfunknetz"
    return None


def internationale_rufnummer(eingabe):
    """Liefert die Nummer als +Ländervorwahl und Ziffern oder None, wenn die Ländervorwahl fehlt."""
    ziffern = re.sub(r"\D", "", eingabe)
    if eingabe.strip().startswith("+"):
        nummer = ziffern
    elif ziffern.startswith("00"):
        nummer = ziffern[2:]
    else:
        return None
    return f"+{nummer}" if len(nummer) in RUFNUMMER_ZIFFERN else None


def gleiche_rufnummer(absender, empfänger):
    """Vergleicht einen Absender in beliebiger Schreibweise (+49…, 0049…, 49…, 0…) mit einer Nummer mit
    Ländervorwahl. Ohne Ländervorwahl muss der Absender der Nummer nach deren Ländervorwahl entsprechen."""
    ziel = re.sub(r"\D", "", empfänger)
    if (international := internationale_rufnummer(absender)) is not None:
        return international == f"+{ziel}"
    ziffern = re.sub(r"\D", "", absender).removeprefix("0")
    return bool(ziffern) and ziel.endswith(ziffern) and len(ziel) - len(ziffern) in LÄNDERVORWAHL_ZIFFERN


def wartezeit_nach_fehlern(fehlversuche, fehler):
    # Ohne Netz scheitern Namensauflösung oder Verbindungsaufbau, ohne dass Daten fließen; so antwortet die Wache
    # kurz nach Rückkehr des Netzes.
    if isinstance(fehler, socket.gaierror) or getattr(fehler, "errno", None) in FEHLERCODES_OHNE_NETZ:
        return WARTEZEIT_OHNE_NETZ_SEKUNDEN
    wartezeit = min(ERSTE_WARTEZEIT_NACH_FEHLER_SEKUNDEN * 2 ** (fehlversuche - 1), HÖCHSTE_WARTEZEIT_NACH_FEHLER_SEKUNDEN)
    if isinstance(fehler, TelegramFehler):
        wartezeit = max(wartezeit, fehler.parameter.get("retry_after", 0))
    return wartezeit


def schreibe_json_atomar(pfad, daten):
    zwischendatei = pfad.with_suffix(".tmp")
    zwischendatei.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(zwischendatei, pfad)


class TermuxGerät:
    """Zugriff auf GPS, Batterie und SMS über Termux:API."""

    def __init__(self):
        # Termux:API arbeitet Aufrufe nacheinander ab; wartende Aufrufe gelten nach 60 Sekunden als hängend und
        # Android beendet Termux:API. Deshalb nie mehr als ein Aufruf gleichzeitig.
        self._api_sperre = threading.Lock()
        self._letzter_gpslogger_neustart = 0
        self._nächster_einzelabruf = 0
        # Hängende Standortabfragen lassen Android Termux:API beenden und Fehlerberichte schreiben, was den
        # Systemprozess zusätzlich belastet; nach einer Zeitüberschreitung deshalb vorerst keine weitere.
        self._standortabfrage_gesperrt_bis = 0
        # Bis zur ersten Prüfung über ADB angenommen; scheitert sie, übernimmt der Einzelabruf von Termux:API.
        self._gpslogger_läuft = True

    def _aufruf(self, befehl, zeitlimit):
        with self._api_sperre:
            return self._aufruf_ohne_sperre(befehl, zeitlimit)

    @staticmethod
    def _aufruf_ohne_sperre(befehl, zeitlimit):
        # Eigene Prozessgruppe, damit bei Zeitüberschreitung auch der Hilfsprozess termux-api endet.
        prozess = subprocess.Popen(befehl, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   start_new_session=True)
        try:
            return prozess.communicate(timeout=zeitlimit)[0]
        except subprocess.TimeoutExpired:
            os.killpg(prozess.pid, signal.SIGKILL)
            prozess.wait()
            raise

    @staticmethod
    def _gpslogger_position():
        dateien = sorted(GPSLOGGER_ORDNER.glob("*.csv"))
        if not dateien:
            return None
        for alte_datei in dateien[:-GPSLOGGER_AUFBEWAHRTE_DATEIEN]:
            alte_datei.unlink(missing_ok=True)
        with open(dateien[-1], "rb") as datei:
            spalten = datei.readline().decode().strip().split(",")
            datei.seek(max(0, os.path.getsize(dateien[-1]) - 4096))
            zeilen = datei.read().decode(errors="replace").splitlines()
        if len(zeilen) < 2 or zeilen[-1].startswith("time,"):
            return None
        werte = dict(zip(spalten, zeilen[-1].split(",")))
        zeitpunkt = int(werte["timestamp_ms"]) / 1000
        if time.time() - zeitpunkt > GPSLOGGER_HÖCHSTALTER_SEKUNDEN:
            return None
        return Position(float(werte["lat"]), float(werte["lon"]), float(werte["accuracy"]) if werte["accuracy"] else None,
                        float(werte["speed"]) if werte["speed"] else None, zeitpunkt)

    def _termux_position(self, abfrage, zeitlimit):
        try:
            daten = json.loads(self._aufruf(["termux-location", "-p", "gps", "-r", abfrage], zeitlimit))
        except subprocess.TimeoutExpired:
            self._standortabfrage_gesperrt_bis = time.time() + EINZELABRUF_PAUSE_NACH_FEHLSCHLAG_SEKUNDEN
            return None
        except json.JSONDecodeError:
            return None
        if "latitude" not in daten:
            return None
        return Position(daten["latitude"], daten["longitude"], daten.get("accuracy"),
                        daten.get("speed"), time.time() - daten.get("elapsedMs", 0) / 1000)

    def position(self):
        # GPSLogger liefert die Positionen ohne Termux:API.
        try:
            if position := self._gpslogger_position():
                return position
        except (OSError, ValueError, KeyError):
            protokoll.exception("GPSLogger-Datei nicht lesbar")
        # GPSLogger schreibt nicht jede Messung in die Datei; die letzte Messung des GPS kostet keine Wartezeit.
        if time.time() >= self._standortabfrage_gesperrt_bis and (position := self._termux_position("last", 30)) and \
                time.time() - position.zeitpunkt <= LETZTE_POSITION_HÖCHSTALTER_SEKUNDEN:
            return position
        if time.time() - self._letzter_gpslogger_neustart >= GPSLOGGER_NEUSTART_ABSTAND_SEKUNDEN:
            self._letzter_gpslogger_neustart = time.time()
            # Eigener Thread, damit die Messung nicht auf ADB wartet.
            threading.Thread(target=self._prüfe_gpslogger, daemon=True).start()
        # Ersatz, falls GPSLogger nicht läuft: schaltet das GPS selbst ein. Solange Positionen kommen, bei jeder
        # Messung, nach einem Fehlschlag erst nach einer Pause, weil jeder Fehlschlag Termux:API beenden lässt.
        if self._gpslogger_läuft or time.time() < max(self._nächster_einzelabruf, self._standortabfrage_gesperrt_bis):
            return None
        position = self._termux_position("once", EINZELABRUF_ZEITLIMIT_SEKUNDEN)
        if position is None:
            self._nächster_einzelabruf = time.time() + EINZELABRUF_PAUSE_NACH_FEHLSCHLAG_SEKUNDEN
        return position

    def _prüfe_gpslogger(self):
        self._gpslogger_läuft = self._starte_gpslogger_neu()
        if self._gpslogger_läuft and self._gps_hängt():
            self._behebe_gps_hänger()

    @staticmethod
    def _adb(argumente, zeitlimit):
        subprocess.run(["adb", "connect", ADB_ZIEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        return subprocess.run(["adb", "-s", ADB_ZIEL, *argumente], capture_output=True, text=True, timeout=zeitlimit, check=False)

    @classmethod
    def _hwbinder_rechenzeit(cls):
        """Summierte Rechenzeit der HwBinder-Threads des Systemprozesses in Sekunden."""
        ergebnis = cls._adb(["shell", "p=$(pidof system_server); for t in /proc/$p/task/*; do read n < $t/comm;"
                                      " case $n in HwBinder*) cat $t/stat;; esac; done"], 30)
        # Felder 14 und 15 von /proc/…/stat: Rechenzeit im Nutzer- und im Systemmodus.
        return sum(int(felder[13]) + int(felder[14]) for zeile in ergebnis.stdout.splitlines()
                   if len(felder := zeile.split()) > 14) / TAKTE_PRO_SEKUNDE

    @classmethod
    def _gps_hängt(cls):
        try:
            beginn, vorher = time.monotonic(), cls._hwbinder_rechenzeit()
            time.sleep(GPS_HÄNGER_MESSDAUER_SEKUNDEN)
            return (cls._hwbinder_rechenzeit() - vorher) / (time.monotonic() - beginn) > GPS_HÄNGER_AUSLASTUNG
        except (OSError, subprocess.TimeoutExpired, ValueError) as fehler:
            protokoll.warning("Prüfung auf hängendes GPS fehlgeschlagen: %r", fehler)
            return False

    @classmethod
    def _behebe_gps_hänger(cls):
        protokoll.warning("GPS hängt im Systemprozess, GPS wird vorübergehend ausgeschaltet")
        # Ohne eingeschaltetes GPS kommen keine neuen Rohdaten nach und der Rückstau wird abgearbeitet. GPSLogger
        # hängt danach in seiner Standortanfrage und muss neu gestartet werden.
        befehl = (f"settings put secure location_providers_allowed -gps; sleep {GPS_AUS_ZUR_BEHEBUNG_SEKUNDEN};"
                  " settings put secure location_providers_allowed +gps; am force-stop com.mendhak.gpslogger;"
                  " am start-foreground-service -n com.mendhak.gpslogger/.GpsLoggingService --ez immediatestart true")
        try:
            cls._adb(["shell", befehl], GPS_AUS_ZUR_BEHEBUNG_SEKUNDEN + 60)
            # Ein Neustart des Handys bleibt aus, weil danach die lokale ADB-Verbindung für DATEN EIN/AUS fehlt; die
            # nächste Prüfung versucht die Behebung erneut.
            if cls._gps_hängt():
                protokoll.error("GPS hängt weiterhin")
            else:
                protokoll.warning("GPS-Hänger behoben")
        except (OSError, subprocess.TimeoutExpired) as fehler:
            protokoll.error("GPS-Hänger nicht behebbar: %r", fehler)

    @classmethod
    def _starte_gpslogger_neu(cls):
        """Startet den GPSLogger-Dienst, falls er nicht läuft; liefert, ob er danach läuft."""
        # Den Dienst eines anderen Programms darf nur die Shell starten, also über ADB. Ohne GPS-Empfang läuft der
        # Dienst weiter; ein Startbefehl an den laufenden Dienst lässt GPSLogger nicht mehr reagieren.
        befehl = ("dumpsys activity services com.mendhak.gpslogger | grep -q '[*] ServiceRecord.*GpsLoggingService'"
                  " && echo laeuft || am start-foreground-service -n com.mendhak.gpslogger/.GpsLoggingService"
                  " --ez immediatestart true")
        try:
            ergebnis = cls._adb(["shell", befehl], 30)
        except (OSError, subprocess.TimeoutExpired) as fehler:
            protokoll.warning("GPSLogger liefert keine Positionen, Neustart fehlgeschlagen: %r", fehler)
            return False
        if ergebnis.stdout.strip() == "laeuft":
            return True
        if ergebnis.returncode == 0 and "Error" not in ergebnis.stdout + ergebnis.stderr:
            protokoll.warning("GPSLogger liefert keine Positionen, neu gestartet")
            return True
        protokoll.warning("GPSLogger liefert keine Positionen, Neustart fehlgeschlagen: %s",
                          (ergebnis.stdout + ergebnis.stderr).strip()[-200:])
        return False

    def batterie(self):
        try:
            daten = json.loads(self._aufruf(["termux-battery-status"], 30))
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return None
        return daten["percentage"], daten["plugged"] != "UNPLUGGED"

    def sende_sms(self, nummer, text):
        # Der Ordner "Gesendet" führt Sekunden; die abgeschnittenen Bruchteile dürfen die eigene SMS nicht ausschließen.
        beginn = int(time.time())
        self._aufruf(["termux-sms-send", "-n", nummer, text], 60)
        frist = time.time() + SMS_SENDEBESTÄTIGUNG_HÖCHSTDAUER_SEKUNDEN
        while time.time() < frist:
            try:
                gesendete = json.loads(self._aufruf(["termux-sms-list", "-t", "sent", "-l", "5"], 30))
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                gesendete = []
            if any(gleiche_rufnummer(nachricht.get("address") or nachricht["number"], nummer) and
                   datetime.strptime(nachricht["received"], "%Y-%m-%d %H:%M:%S").timestamp() >= beginn
                   for nachricht in gesendete):
                return
            time.sleep(1)
        protokoll.warning("SMS an %s ohne Sendebestätigung", nummer)

    def sms_hindernis(self):
        # termux-sms-send meldet auch ohne SIM-Karte oder Netz Erfolg, deshalb vorher prüfen.
        try:
            return sms_hindernis(json.loads(self._aufruf(["termux-telephony-deviceinfo"], 30)))
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return sms_hindernis({})

    def gerätedaten(self):
        """Mobilfunk, WLAN und Batterie wie von Termux:API geliefert, nicht abrufbare Teile leer."""
        daten = {}
        for schlüssel, befehl in (("telefonie", "termux-telephony-deviceinfo"), ("wlan", "termux-wifi-connectioninfo"),
                                  ("batterie", "termux-battery-status")):
            try:
                daten[schlüssel] = json.loads(self._aufruf([befehl], 30))
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                daten[schlüssel] = {}
        daten["schaltweg"] = self._schaltweg_mobile_daten()
        return daten

    @staticmethod
    def _schaltweg_mobile_daten():
        try:
            # Termux bringt immer ein su mit, das ohne Root nur eine Fehlermeldung ausgibt.
            if subprocess.run(["su", "-c", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10, check=False).returncode == 0:
                return "Root"
            subprocess.run(["adb", "connect", ADB_ZIEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            verbindung = subprocess.run(["adb", "-s", ADB_ZIEL, "get-state"], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return "ADB" if verbindung.stdout.strip() == "device" else None

    def empfangene_sms(self):
        try:
            return json.loads(self._aufruf(["termux-sms-list", "-t", "inbox", "-l", str(SMS_ABGEFRAGTE_NACHRICHTEN)], 30))
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return []

    def schalte_daten(self, ein):
        """Schaltet WLAN und mobile Daten; liefert, ob das Schalten der mobilen Daten gelungen ist."""
        # Mobile Daten darf nur die Shell oder Root schalten: zuerst über su, sonst über ADB, das auf dem Gerät
        # selbst per TCP lauscht. Auf demselben Weg auch das WLAN, weil termux-wifi-enable beim Einschalten hängen kann.
        schalter = "enable" if ein else "disable"
        svc = f"svc data {schalter}; svc wifi {schalter}"
        for befehle in ([["su", "-c", svc]], [["adb", "connect", ADB_ZIEL], ["adb", "-s", ADB_ZIEL, "shell", svc]]):
            try:
                if all(subprocess.run(befehl, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=30, check=False).returncode == 0 for befehl in befehle):
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
        try:
            self._aufruf(["termux-wifi-enable", "true" if ein else "false"], 30)
        except subprocess.TimeoutExpired:
            protokoll.warning("WLAN schalten über Termux:API hängt")
        return False

    def schalte_fernzugriff(self, ein):
        """Aktiviert oder deaktiviert RustDesk und Tailscale; liefert, ob danach beide wie gewünscht stehen."""
        # Deaktivierte Apps kann weder Android noch die App selbst starten, sie nutzen also keine Daten.
        try:
            if not ein:
                return all(self._adb(["shell", f"pm disable-user --user 0 {paket}"], 30).returncode == 0
                           for paket in FERNZUGRIFF_PAKETE)
            for paket in FERNZUGRIFF_PAKETE:
                self._adb(["shell", f"pm enable {paket}"], 30)
            # RustDesk zuerst, weil dessen Oberfläche den Verbindungsaufbau von Tailscale unterbricht.
            rustdesk_läuft = self._starte_rustdesk()
            return self._starte_tailscale() and rustdesk_läuft
        except (OSError, subprocess.TimeoutExpired) as fehler:
            protokoll.warning("Fernzugriff schalten fehlgeschlagen: %r", fehler)
            return False

    @classmethod
    def _starte_tailscale(cls):
        # Tailscale verbindet sich beim Start der App von selbst, direkt nach dem Aktivieren aber nicht immer.
        for _ in range(TAILSCALE_STARTVERSUCHE):
            cls._adb(["shell", "monkey -p com.tailscale.ipn -c android.intent.category.LAUNCHER 1"], 30)
            time.sleep(TAILSCALE_VERBINDUNGSAUFBAU_SEKUNDEN)
            if cls._adb(["shell", "ifconfig tun0"], 30).returncode == 0:
                cls._adb(["shell", "input keyevent KEYCODE_HOME"], 30)
                return True
        return False

    @classmethod
    def _rustdesk_dienst_läuft(cls):
        return "MainService" in cls._adb(["shell", "dumpsys activity services com.carriez.flutter_hbb"], 30).stdout

    @classmethod
    def _starte_rustdesk(cls):
        # Den Dienst von RustDesk startet nur dessen eigene Oberfläche; sie braucht einen entsperrten Bildschirm.
        cls._adb(["shell", "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard;"
                           " am start -n com.carriez.flutter_hbb/.MainActivity"], 30)
        for beschriftung in RUSTDESK_STARTSCHRITTE:
            time.sleep(OBERFLÄCHE_WARTEZEIT_SEKUNDEN)
            if cls._rustdesk_dienst_läuft() or not cls._tippe_auf(beschriftung):
                break
        time.sleep(OBERFLÄCHE_WARTEZEIT_SEKUNDEN)
        läuft = cls._rustdesk_dienst_läuft()
        cls._adb(["shell", "input keyevent KEYCODE_HOME"], 30)
        return läuft

    @classmethod
    def _tippe_auf(cls, beschriftung):
        """Tippt auf das Bedienelement, dessen Beschreibung so beginnt; liefert, ob es angezeigt wurde."""
        ansicht = cls._adb(["shell", "uiautomator dump /sdcard/ansicht.xml >/dev/null && cat /sdcard/ansicht.xml"],
                           30).stdout
        for element in re.findall(r"<node [^>]*>", ansicht):
            if (treffer := MUSTER_BEDIENELEMENT.search(element)) and \
                    html.unescape(treffer.group(1)).startswith(beschriftung):
                links, oben, rechts, unten = map(int, treffer.group(2, 3, 4, 5))
                cls._adb(["shell", f"input tap {(links + rechts) // 2} {(oben + unten) // 2}"], 30)
                return True
        return False


class TelegramFehler(Exception):
    """Telegram hat die Anfrage abgelehnt."""

    def __init__(self, code, beschreibung, parameter):
        super().__init__(f"{code} {beschreibung}")
        self.code = code
        self.parameter = parameter

    @property
    def dauerhaft(self):
        # Ungültige Anfrage, falscher Token oder fehlende Rechte ändern sich durch Wiederholen nicht.
        return self.code in (400, 401, 403, 404)


class Telegram:
    def __init__(self, token, gruppe, bei_neuer_gruppe):
        self._pfad = f"/bot{token}/"
        self.gruppe = gruppe
        self._bei_neuer_gruppe = bei_neuer_gruppe
        # Je Thread eine offen gehaltene Verbindung: Ein neuer TLS-Aufbau je Anfrage kostet ein Vielfaches an Daten.
        self._verbindungen = threading.local()

    def _anfrage(self, methode, körper, inhaltstyp, zeitlimit):
        while True:
            verbindung = getattr(self._verbindungen, "verbindung", None)
            wiederverwendet = verbindung is not None
            if not wiederverwendet:
                verbindung = http.client.HTTPSConnection("api.telegram.org")
            verbindung.timeout = zeitlimit
            if verbindung.sock:
                verbindung.sock.settimeout(zeitlimit)
            try:
                verbindung.request("POST", self._pfad + methode, körper, {"Content-Type": inhaltstyp})
                daten = json.loads(verbindung.getresponse().read().decode("utf-8"))
                self._verbindungen.verbindung = verbindung
                break
            except (OSError, http.client.HTTPException, ValueError):
                verbindung.close()
                self._verbindungen.verbindung = None
                # Eine offen gehaltene Verbindung kann inzwischen vom Server oder Mobilfunknetz getrennt sein.
                if not wiederverwendet:
                    raise
        if not daten["ok"]:
            raise TelegramFehler(daten["error_code"], daten.get("description", ""), daten.get("parameters", {}))
        return daten["result"]

    def übernehme_neue_gruppe(self, gruppe):
        protokoll.warning("Telegram-Gruppe hat die neue Nummer %s", gruppe)
        self.gruppe = gruppe
        self._bei_neuer_gruppe(gruppe)

    def _an_gruppe(self, senden):
        try:
            return senden(self.gruppe)
        except TelegramFehler as fehler:
            # Wird eine Gruppe zur Supergruppe, vergibt Telegram eine neue Nummer und nennt sie in der Ablehnung.
            neue_gruppe = fehler.parameter.get("migrate_to_chat_id")
            if neue_gruppe is None:
                raise
            self.übernehme_neue_gruppe(neue_gruppe)
            return senden(neue_gruppe)

    def sende_nachricht(self, text):
        def senden(gruppe):
            # Alle Meldungen sind HTML; sie enthalten keine Nutzereingaben, die maskiert werden müssten.
            körper = urllib.parse.urlencode({"chat_id": gruppe, "text": text, "parse_mode": "HTML",
                                             "link_preview_options": '{"is_disabled": true}'}).encode("utf-8")
            return self._anfrage("sendMessage", körper, "application/x-www-form-urlencoded", 30)
        self._an_gruppe(senden)

    def sende_datei(self, dateiname, inhalt, beschriftung):
        def senden(gruppe):
            grenze, körper = mehrteiliger_körper({"chat_id": str(gruppe), "caption": beschriftung},
                                                 "document", dateiname, inhalt)
            return self._anfrage("sendDocument", körper, f"multipart/form-data; boundary={grenze}", 60)
        self._an_gruppe(senden)

    def hole_aktualisierungen(self, versatz):
        körper = urllib.parse.urlencode({"offset": versatz}).encode("utf-8")
        return self._anfrage("getUpdates", körper, "application/x-www-form-urlencoded", 30)


def mehrteiliger_körper(felder, dateifeld, dateiname, inhalt):
    grenze = uuid.uuid4().hex
    teile = [f'--{grenze}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{wert}\r\n'.encode()
             for name, wert in felder.items()]
    teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="{dateifeld}"; filename="{dateiname}"\r\n'
                 f"Content-Type: application/gpx+xml\r\n\r\n".encode() + inhalt + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    return grenze, b"".join(teile)


class Ankerwache:
    def __init__(self, konfiguration, gerät, telegram, uhr=time.time, schlafen=time.sleep, datenordner=DATENORDNER):
        self._konfiguration = konfiguration
        self._gerät = gerät
        self._telegram = telegram
        self._uhr = uhr
        self._schlafen = schlafen
        self._datenordner = Path(datenordner)
        self._datenordner.mkdir(exist_ok=True)
        self._zustandsdatei = self._datenordner / "zustand.json"
        self._sperre = threading.RLock()
        self._postausgang = queue.Queue()
        self._sms_wartet = threading.Event()
        self._sms_zurückgestellt = False
        self._erste_messung_fertig = threading.Event()

        # Letzte gültige Position und GPS-Warnung überstehen einen Neustart, damit keine Warnung doppelt kommt.
        # Die letzte gelesene SMS-Kennung verhindert, dass ein Befehl nach einem Neustart erneut ausgeführt wird.
        # Noch nicht zugestellte Telegram-Meldungen und SMS überstehen einen Neustart, damit kein Alarm verloren geht.
        self.zustand = {"ankerBreite": None, "ankerLänge": None, "ankerGesetztUm": None, "radius": None,
                        "alarmEin": False, "letzteGültigePosition": None, "gpsWarnungGesendet": False,
                        "postausgang": [], "smsAusgang": [],
                        "letzteStartmeldung": None, "letzteSmsKennung": None, "datenAus": False,
                        "fernzugriffEin": False,
                        "genauigkeitsgrenzeMeter": konfiguration["genauigkeitsgrenzeMeter"],
                        "gpsWarnungNachSekunden": konfiguration["gpsWarnungNachSekunden"],
                        "meldeweg": konfiguration["meldeweg"],
                        "lebenszeichenHäufigkeit": konfiguration["lebenszeichenHäufigkeit"],
                        "smsEmpfänger": konfiguration["smsEmpfänger"], "smsMonat": None, "smsImMonat": 0}
        if self._zustandsdatei.exists():
            self.zustand.update(json.loads(self._zustandsdatei.read_text(encoding="utf-8")))

        gespeichert = self.zustand["letzteGültigePosition"]
        self.letzte_gültige_position = Position(**gespeichert) if gespeichert else None
        self.letzte_position = None
        self._gps_stabil_seit = None
        self.batterie = None
        self.letzter_messversuch = uhr()
        self._programmstart = uhr()
        self._zähler_außerhalb = 0
        self._alarmzeit = None
        self._zweite_meldung_gesendet = False
        self._batteriewarnung_gesendet = False
        self._letztes_lebenszeichen = None
        self._daten_aus_fällig_um = None
        self._daten_eingeschaltet_um = None
        self._letzter_telegram_kontakt = None
        for meldung in self.zustand["postausgang"]:
            self._postausgang.put(meldung)
        if self.zustand["smsAusgang"]:
            self._sms_wartet.set()

    # Zustand

    def _speichere_zustand(self):
        schreibe_json_atomar(self._zustandsdatei, self.zustand)

    def _setze_alarmzustand_zurück(self):
        self._zähler_außerhalb = 0
        self._alarmzeit = None
        self._zweite_meldung_gesendet = False
        # Nach neuem Scharfmachen oder Ausschalten sind wartende SMS überholt und würden nur Kosten verursachen.
        if verworfen := len(self.zustand["smsAusgang"]):
            self.zustand["smsAusgang"].clear()
            protokoll.warning("%d wartende SMS verworfen, Alarm neu eingestellt", verworfen)

    def _anker_gesetzt(self):
        return self.zustand["ankerBreite"] is not None

    def _abstand_zum_anker(self, position):
        return abstand_meter(self.zustand["ankerBreite"], self.zustand["ankerLänge"], position.breite, position.länge)

    def _peilung_vom_anker(self, position):
        return peilung_grad(self.zustand["ankerBreite"], self.zustand["ankerLänge"], position.breite, position.länge)

    def _aktuelle_position(self):
        position = self.letzte_gültige_position
        if position and self._uhr() - position.zeitpunkt <= HÖCHSTALTER_POSITION_FÜR_BEFEHLE_SEKUNDEN:
            return position
        return None

    def _ist_gültig(self, position):
        return position.genauigkeit is not None and position.genauigkeit <= self.zustand["genauigkeitsgrenzeMeter"]

    # Messung und Alarm

    def genaueste_position(self, dauer):
        """Fragt über die Dauer wiederholt ab und liefert die Position mit der besten Genauigkeit."""
        ende = self._uhr() + dauer
        beste = None
        while True:
            position = self._gerät.position()
            if position and position.genauigkeit is not None and (beste is None or position.genauigkeit < beste.genauigkeit):
                beste = position
            if self._uhr() + GPS_ABFRAGEABSTAND_SEKUNDEN > ende:
                return beste
            self._schlafen(GPS_ABFRAGEABSTAND_SEKUNDEN)

    def verarbeite_position(self, position):
        with self._sperre:
            if position is None:
                return
            self.letzte_position = position
            gültig = self._ist_gültig(position)
            abstand = self._abstand_zum_anker(position) if self._anker_gesetzt() else None
            self._schreibe_trackzeile(position, abstand, gültig)
            if not gültig:
                return
            vorherige = self.letzte_gültige_position
            if (self._gps_stabil_seit is None or vorherige is None
                    or position.zeitpunkt - vorherige.zeitpunkt > GPS_HÖCHSTE_LÜCKE_BEI_STABILEM_EMPFANG_SEKUNDEN):
                self._gps_stabil_seit = position.zeitpunkt
            self.letzte_gültige_position = position
            self.zustand["letzteGültigePosition"] = asdict(position)
            if (self.zustand["gpsWarnungGesendet"]
                    and position.zeitpunkt - self._gps_stabil_seit >= GPS_STABIL_FÜR_ENTWARNUNG_SEKUNDEN):
                self.zustand["gpsWarnungGesendet"] = False
                self.melde(f"GPS wieder verfügbar, seit {GPS_STABIL_FÜR_ENTWARNUNG_SEKUNDEN // 60} Minuten stabil.")
            self._speichere_zustand()
            if not (self.zustand["alarmEin"] and abstand is not None):
                return

            self._zähler_außerhalb = self._zähler_außerhalb + 1 if abstand > self.zustand["radius"] else 0
            if self._zähler_außerhalb < AUFEINANDERFOLGENDE_POSITIONEN_FÜR_ALARM:
                return
            if self._alarmzeit is None:
                self._alarmzeit = position.zeitpunkt
                self._löse_alarm_aus(position, erneut=False)
            elif (not self._zweite_meldung_gesendet
                  and position.zeitpunkt - self._alarmzeit >= self._konfiguration["zweiteMeldungNachSekunden"]):
                self._zweite_meldung_gesendet = True
                self._löse_alarm_aus(position, erneut=True)

    def _alarmwerte(self, position, erneut):
        return {
            "überschrift": f"ANKERALARM{' ERNEUT' if erneut else ''}",
            "boot": koordinaten_text(position.breite, position.länge),
            "abstand": f"{self._abstand_zum_anker(position):.0f} Meter",
            "radius": f"{self.zustand['radius']} Meter",
            "peilung": f"{self._peilung_vom_anker(position):.0f} Grad",
            "fahrt": fahrt_text(position),
            "batterie": f"{self.batterie[0]} Prozent" if self.batterie else "unbekannt",
            "uhrzeit": datetime.fromtimestamp(position.zeitpunkt).strftime("%H:%M"),
        }

    def alarmtext(self, position, erneut):
        """Einzeilig für die SMS: höchstens 160 Zeichen, ohne Umlaute."""
        werte = self._alarmwerte(position, erneut)
        return (f"{werte['überschrift']} {werte['boot']}, Abstand {werte['abstand']}, Radius {werte['radius']}, "
                f"Peilung {werte['peilung']}, Fahrt {werte['fahrt']}, Batterie {werte['batterie']}, {werte['uhrzeit']} Uhr")

    def telegram_alarmtext(self, position, erneut):
        werte = self._alarmwerte(position, erneut)
        return "\n".join([
            f"<b>{werte['überschrift']}</b>",
            f"Boot: {werte['boot']}",
            f"Abstand: <b>{werte['abstand']}</b> (Radius {werte['radius']})",
            f"Peilung vom Anker: {werte['peilung']}",
            f"Fahrt: {werte['fahrt']}",
            f"Batterie: {werte['batterie']}",
            f"Zeit: {werte['uhrzeit']} Uhr",
            kartenverweis(position.breite, position.länge),
        ])

    def _löse_alarm_aus(self, position, erneut):
        text = self.alarmtext(position, erneut)
        protokoll.warning(text)
        for nummer in self.zustand["smsEmpfänger"]:
            self.sende_sms(nummer, text)
        if not self.zustand["datenAus"]:
            self.sende_telegram(self.telegram_alarmtext(position, erneut))

    def verarbeite_batterie(self, messung):
        if messung is None:
            return
        with self._sperre:
            self.batterie = messung
            prozent = messung[0]
            if prozent < self._konfiguration["batteriewarnungProzent"] and not self._batteriewarnung_gesendet:
                self._batteriewarnung_gesendet = True
                self.melde(f"<b>Batterie niedrig:</b> {prozent} Prozent.")
            elif prozent > BATTERIE_ENTWARNUNG_PROZENT:
                self._batteriewarnung_gesendet = False

    def prüfe_gps_ausfall(self):
        with self._sperre:
            letzte_gültige = self.letzte_gültige_position.zeitpunkt if self.letzte_gültige_position else self._programmstart
            dauer = self._uhr() - letzte_gültige
            if dauer >= self.zustand["gpsWarnungNachSekunden"] and not self.zustand["gpsWarnungGesendet"]:
                self.zustand["gpsWarnungGesendet"] = True
                self._speichere_zustand()
                self.melde(f"<b>Keine gültige GPS-Position</b> seit {alter_text(dauer)}. "
                           "Der Ankeralarm kann so nicht auslösen.")

    def prüfe_lebenszeichen(self, jetzt_lokal):
        heute = jetzt_lokal.date()
        häufigkeit = self.zustand["lebenszeichenHäufigkeit"]
        fälliger_tag = (häufigkeit == "täglich" or (häufigkeit == "wöchentlich" and heute.weekday() == 0)
                        or (häufigkeit == "monatlich" and heute.day == 1))
        if (fälliger_tag and jetzt_lokal.strftime("%H:%M") >= self._konfiguration["lebenszeichenUhrzeit"]
                and self._letztes_lebenszeichen != heute):
            self._letztes_lebenszeichen = heute
            self.melde(self.statusmeldung("Lebenszeichen"), self.sms_statusmeldung("Lebenszeichen"))

    def prüfe_daten_aus(self):
        if self._daten_aus_fällig_um is not None and self._uhr() >= self._daten_aus_fällig_um:
            self._daten_aus_fällig_um = None
            self.melde(self._schalte_daten(ein=False))

    # Meldungen

    def statusmeldung(self, überschrift):
        with self._sperre:
            zeilen = [f"<b>{überschrift}</b>"]
            if self.zustand["alarmEin"]:
                zeilen.append(f"Alarm: <b>ein</b>, Radius {self.zustand['radius']} Meter")
            else:
                zeilen.append("Alarm: <b>aus</b>")
            zeilen.append(f"Genauigkeitsgrenze: {self.zustand['genauigkeitsgrenzeMeter']} Meter")
            zeilen.append(f"GPS-Warnung nach: {self.zustand['gpsWarnungNachSekunden'] // 60} Minuten")
            zeilen.append(f"Meldungen: {MELDEWEG_TEXT[self.zustand['meldeweg']]}")
            zeilen.append(f"Lebenszeichen: {LEBENSZEICHEN_TERMIN_TEXT[self.zustand['lebenszeichenHäufigkeit']]} "
                          f"um {self._konfiguration['lebenszeichenUhrzeit']} Uhr")
            if self._anker_gesetzt():
                zeilen.append(f"Anker: {koordinaten_text(self.zustand['ankerBreite'], self.zustand['ankerLänge'])}")
            else:
                zeilen.append("Anker: nicht gesetzt")
            # Gezeigt wird die jüngste Messung, auch wenn sie zu ungenau für den Alarm ist.
            position = self.letzte_position or self.letzte_gültige_position
            gültige = self.letzte_gültige_position
            if position:
                zeilen.append(f"Boot: {koordinaten_text(position.breite, position.länge)}")
                if self._anker_gesetzt():
                    zeilen.append(f"Abstand: {self._abstand_zum_anker(position):.0f} Meter")
                    zeilen.append(f"Peilung vom Anker: {self._peilung_vom_anker(position):.0f} Grad")
                genauigkeit = f"Genauigkeit: {position.genauigkeit:.0f} Meter"
                if not self._ist_gültig(position):
                    genauigkeit += " (zu ungenau für den Alarm)"
                zeilen.append(genauigkeit)
                zeilen.append(f"Fahrt: {fahrt_text(position)}")
                zeilen.append(f"Gemessen: vor {alter_text(self._uhr() - position.zeitpunkt)}")
            else:
                zeilen.append("Boot: noch keine Position gemessen")
            if gültige is None:
                zeilen.append("Letzte gültige Position: keine")
            elif gültige is not position:
                zeilen.append(f"Letzte gültige Position: vor {alter_text(self._uhr() - gültige.zeitpunkt)}")
            if self.batterie:
                zeilen.append(f"Batterie: {self.batterie[0]} Prozent, Ladung {'aktiv' if self.batterie[1] else 'unterbrochen'}")
            if position:
                zeilen.append(kartenverweis(position.breite, position.länge))
            return "\n".join(zeilen)

    def sms_statusmeldung(self, überschrift):
        """Kurzfassung der Statusmeldung, im Regelfall eine einzige SMS."""
        with self._sperre:
            teile = [f"Alarm ein {self.zustand['radius']}m" if self.zustand["alarmEin"] else "Alarm aus"]
            position = self.letzte_position or self.letzte_gültige_position
            if not self._anker_gesetzt():
                teile.append("Anker nicht gesetzt")
            elif position:
                teile.append(f"Abstand {self._abstand_zum_anker(position):.0f}m")
                teile.append(f"Peilung {self._peilung_vom_anker(position):.0f} Grad")
            if position:
                teile.append(f"Boot {koordinaten_text(position.breite, position.länge)}")
                genauigkeit = f"Genauigkeit {position.genauigkeit:.0f}m"
                teile.append(genauigkeit if self._ist_gültig(position) else genauigkeit + " (zu ungenau)")
                if position.geschwindigkeit is not None:
                    teile.append(f"{position.geschwindigkeit / METER_PRO_SEKUNDE_JE_KNOTEN:.1f}kn")
                teile.append(f"vor {alter_text(self._uhr() - position.zeitpunkt)}")
            else:
                teile.append("keine Position")
            if self.batterie:
                teile.append(f"Batterie {self.batterie[0]}%{'' if self.batterie[1] else ' ohne Ladung'}")
            if self.zustand["datenAus"]:
                teile.append("Daten aus")
            return sms_fassung(f"{überschrift}: " + ", ".join(teile))

    def befehlsübersicht(self):
        return BEFEHLSÜBERSICHT.format(zuschlag=self._konfiguration["sicherheitszuschlagMeter"],
                                       uhrzeit=self._konfiguration["lebenszeichenUhrzeit"])

    def sende_telegram(self, text):
        meldung = [text, self._uhr()]
        with self._sperre:
            self.zustand["postausgang"].append(meldung)
            self._speichere_zustand()
        self._postausgang.put(meldung)

    def sende_sms(self, nummer, text):
        with self._sperre:
            self.zustand["smsAusgang"].append([nummer, sms_fassung(text), self._uhr()])
            self._speichere_zustand()
        self._sms_wartet.set()

    def melde(self, text, sms_text=None):
        """Versendet eine Meldung auf dem gewählten Weg; bei ausgeschalteten Daten per SMS."""
        meldeweg = self.zustand["meldeweg"]
        if meldeweg != "sms" and not self.zustand["datenAus"]:
            self.sende_telegram(text)
        if meldeweg != "telegram" or self.zustand["datenAus"]:
            for nummer in self.zustand["smsEmpfänger"]:
                self.sende_sms(nummer, sms_text or text)

    def versende_wartende_sms(self):
        """Sendet die wartenden SMS der Reihe nach; liefert False, solange eine nicht gesendet werden kann."""
        while True:
            with self._sperre:
                if not self.zustand["smsAusgang"]:
                    return True
                eintrag = self.zustand["smsAusgang"][0]
            nummer, text, erstellt = eintrag
            if hindernis := self._gerät.sms_hindernis():
                if not self._sms_zurückgestellt:
                    self._sms_zurückgestellt = True
                    protokoll.warning("SMS an %s zurückgestellt: %s", nummer, hindernis)
                return False
            self._sms_zurückgestellt = False
            if self._uhr() - erstellt >= VERSPÄTUNG_FÜR_HINWEIS_SEKUNDEN:
                text = f"Verspaetet, erstellt {datetime.fromtimestamp(erstellt).strftime('%d.%m. %H:%M')}: {text}"
            try:
                self._gerät.sende_sms(nummer, text)
            except Exception:
                protokoll.exception("SMS an %s fehlgeschlagen, neuer Versuch folgt", nummer)
                return False
            with self._sperre:
                # Neues Scharfmachen kann den Ausgang während des Versands geleert haben.
                if self.zustand["smsAusgang"] and self.zustand["smsAusgang"][0] is eintrag:
                    self.zustand["smsAusgang"].pop(0)
                monat = datetime.fromtimestamp(self._uhr()).strftime("%m/%Y")
                if self.zustand["smsMonat"] != monat:
                    self.zustand.update(smsMonat=monat, smsImMonat=0)
                self.zustand["smsImMonat"] += sms_anzahl(text)
                self._speichere_zustand()

    # Befehle

    def verarbeite_befehl(self, rohtext, kanal):
        # Umschrift vor dem Vergleich, damit TÄGLICH und TAEGLICH gleichermaßen gelten.
        befehl = " ".join(rohtext.translate(UMSCHRIFT).upper().split())
        per_sms = kanal == KANAL_SMS
        if befehl == "POSITION":
            return self.sms_statusmeldung("Position") if per_sms else self.statusmeldung("Position")
        if befehl == "HILFE":
            return SMS_BEFEHLSÜBERSICHT if per_sms else self.befehlsübersicht()
        if befehl == "SETZE ANKER":
            return self._setze_anker_auf_aktuelle_position()
        if treffer := MUSTER_ANKER_KOORDINATEN.match(befehl):
            return self._setze_anker_auf_koordinaten(float(treffer.group(1)), float(treffer.group(2)))
        if befehl == "ALARM EIN":
            return self._alarm_ein_mit_zuschlag()
        if treffer := MUSTER_ALARM_RADIUS.match(befehl):
            return self._alarm_ein_mit_radius(int(treffer.group(1)))
        if befehl == "ALARM AUS":
            with self._sperre:
                self.zustand["alarmEin"] = False
                self._setze_alarmzustand_zurück()
                self._speichere_zustand()
            return "Alarm aus."
        if treffer := MUSTER_GENAUIGKEIT.match(befehl):
            return self._setze_genauigkeitsgrenze(int(treffer.group(1)))
        if treffer := MUSTER_GPS_WARNUNG.match(befehl):
            return self._setze_gps_warnung(int(treffer.group(1)))
        if treffer := MUSTER_MELDUNGEN.match(befehl):
            return self._setze_meldeweg(MELDEWEGE[treffer.group(1)])
        if treffer := MUSTER_LEBENSZEICHEN.match(befehl):
            return self._setze_lebenszeichen(LEBENSZEICHEN_HÄUFIGKEITEN[treffer.group(1)])
        if befehl == "DATEN EIN":
            self._daten_aus_fällig_um = None
            antwort = self._schalte_daten(ein=True)
            self._daten_eingeschaltet_um = self._uhr()
            return antwort
        if befehl == "DATEN AUS":
            if per_sms:
                return self._schalte_daten(ein=False)
            self._daten_aus_fällig_um = self._uhr() + DATEN_AUS_VERZÖGERUNG_SEKUNDEN
            return ("Mobile Daten und WLAN werden innerhalb einer Minute ausgeschaltet, die Bestätigung kommt per SMS. "
                    "Danach Befehle und Meldungen nur per SMS, wieder einschalten per SMS mit DATEN EIN.")
        if befehl in ("FERNZUGRIFF EIN", "FERNZUGRIFF AUS"):
            return self._schalte_fernzugriff(ein=befehl.endswith("EIN"))
        if befehl == "TRACK":
            return self.weitester_abstand_text() if per_sms else self._sende_track()
        if befehl in ("STATUS", "GERAETESTATUS"):
            return self.sms_gerätestatus() if per_sms else self.gerätestatus()
        if treffer := MUSTER_EMPFÄNGER.match(befehl):
            if (nummer := internationale_rufnummer(treffer.group(2))) is None:
                return "Nummer bitte mit Ländervorwahl angeben, zum Beispiel +491701234567."
            if treffer.group(1) == "HINZU":
                return self._füge_empfänger_hinzu(nummer)
            return self._entferne_empfänger(nummer)
        if per_sms:
            return "Falscher Befehl. HILFE liefert die Befehle."
        return "<b>Falscher Befehl.</b>\n\n" + self.befehlsübersicht()

    def _empfängerliste(self):
        return ", ".join(self.zustand["smsEmpfänger"])

    def _füge_empfänger_hinzu(self, nummer):
        with self._sperre:
            if any(gleiche_rufnummer(nummer, empfänger) for empfänger in self.zustand["smsEmpfänger"]):
                return f"{nummer} ist bereits SMS-Empfänger."
            self.zustand["smsEmpfänger"] = [*self.zustand["smsEmpfänger"], nummer]
            self._speichere_zustand()
            return f"{nummer} hinzugefügt. SMS-Empfänger: {self._empfängerliste()}."

    def _entferne_empfänger(self, nummer):
        with self._sperre:
            verbleibende = [empfänger for empfänger in self.zustand["smsEmpfänger"]
                            if not gleiche_rufnummer(nummer, empfänger)]
            if len(verbleibende) == len(self.zustand["smsEmpfänger"]):
                return f"{nummer} ist kein SMS-Empfänger. SMS-Empfänger: {self._empfängerliste()}."
            # Ohne SMS-Empfänger käme der Alarm nicht mehr per SMS und die Steuerung per SMS wäre verloren.
            if not verbleibende:
                return "Der letzte SMS-Empfänger kann nicht entfernt werden."
            self.zustand["smsEmpfänger"] = verbleibende
            self._speichere_zustand()
            return f"{nummer} entfernt. SMS-Empfänger: {self._empfängerliste()}."

    @staticmethod
    def _mobilfunknetz(telefonie):
        return ", ".join(filter(None, [telefonie.get("network_operator_name"),
                                       (telefonie.get("network_type") or "").upper(),
                                       "Roaming" if telefonie.get("network_roaming") else None]))

    def _sms_im_monat(self):
        monat = datetime.fromtimestamp(self._uhr()).strftime("%m/%Y")
        return monat, self.zustand["smsImMonat"] if self.zustand["smsMonat"] == monat else 0

    def sms_gerätestatus(self):
        """Kurzfassung des Gerätestatus für höchstens zwei SMS."""
        daten = self._gerät.gerätedaten()
        telefonie, wlan, batterie = daten["telefonie"], daten["wlan"], daten["batterie"]
        with self._sperre:
            verbindung = DATENVERBINDUNG_TEXT.get(telefonie.get("data_state"), "unbekannt")
            teile = [f"Status: laeuft {alter_kurztext(self._uhr() - self._programmstart)}",
                     f"Daten {'aus' if self.zustand['datenAus'] else 'ein'} {verbindung} "
                     f"({self._mobilfunknetz(telefonie) or 'Netz unbekannt'})"]
            if wlan.get("supplicant_state") == "COMPLETED":
                teile.append(f"WLAN {wlan.get('ssid', '').strip(chr(34))}")
            else:
                teile.append("WLAN nicht verbunden")
            teile.append(f"schaltbar {daten['schaltweg']}" if daten["schaltweg"] else "nur WLAN schaltbar")
            if self._letzter_telegram_kontakt is None:
                teile.append("Telegram kein Kontakt")
            else:
                teile.append(f"Telegram vor {alter_kurztext(self._uhr() - self._letzter_telegram_kontakt)}")
            monat, gesendet = self._sms_im_monat()
            # Ob SMS möglich sind, zeigt schon der Empfang dieser Antwort; zwei SMS lassen nur Platz für die Warteschlange.
            wartend = f" ({len(self.zustand['smsAusgang'])} wartend)" if self.zustand["smsAusgang"] else ""
            teile.append(f"SMS {gesendet} im {monat} an {' '.join(self.zustand['smsEmpfänger'])}{wartend}")
            teile.append(f"Meldungen {MELDEWEG_TEXT[self.zustand['meldeweg']]}")
            teile.append(f"Lebenszeichen {LEBENSZEICHEN_TERMIN_TEXT[self.zustand['lebenszeichenHäufigkeit']]} "
                         f"{self._konfiguration['lebenszeichenUhrzeit']}")
            if batterie:
                ladung = "laedt" if batterie.get("plugged") != "UNPLUGGED" else "ohne Ladung"
                teile.append(f"Batterie {batterie.get('percentage')}% {ladung} {batterie.get('temperature', 0):.0f}C")
            gültige = self.letzte_gültige_position
            teile.append(f"GPS {'vor ' + alter_kurztext(self._uhr() - gültige.zeitpunkt) if gültige else 'keine Position'}, "
                         f"Grenze {self.zustand['genauigkeitsgrenzeMeter']}m, "
                         f"Warnung {self.zustand['gpsWarnungNachSekunden'] // 60}min")
            teile.append(f"Alarm ein {self.zustand['radius']}m" if self.zustand["alarmEin"] else "Alarm aus")
            return ", ".join(teile)

    def gerätestatus(self):
        daten = self._gerät.gerätedaten()
        telefonie, wlan, batterie = daten["telefonie"], daten["wlan"], daten["batterie"]
        with self._sperre:
            zeilen = ["<b>Gerätestatus</b>", f"Ankerwache läuft seit: {alter_text(self._uhr() - self._programmstart)}"]
            verbindung = DATENVERBINDUNG_TEXT.get(telefonie.get("data_state"), "unbekannt")
            zeilen.append(f"Mobile Daten: {'aus' if self.zustand['datenAus'] else 'ein'}, Verbindung {verbindung}")
            netz = self._mobilfunknetz(telefonie)
            zeilen.append(f"Mobilfunknetz: {netz or 'unbekannt'}")
            if wlan.get("supplicant_state") == "COMPLETED":
                zeilen.append(f"WLAN: verbunden mit {wlan.get('ssid', 'unbekanntem Netz').strip(chr(34))}")
            else:
                zeilen.append("WLAN: nicht verbunden")
            if daten["schaltweg"]:
                zeilen.append(f"Mobile Daten schaltbar: über {daten['schaltweg']}")
            else:
                zeilen.append("Mobile Daten schaltbar: nein, DATEN EIN/AUS schaltet nur das WLAN")
            if self._letzter_telegram_kontakt is None:
                zeilen.append("Telegram: seit dem Start kein Kontakt")
            else:
                zeilen.append(f"Telegram: letzter Kontakt vor {alter_text(self._uhr() - self._letzter_telegram_kontakt)}")
            zeilen.append(f"SMS-Empfänger: {self._empfängerliste()}")
            monat, gesendet = self._sms_im_monat()
            zeilen.append(f"SMS gesendet im Monat {monat}: {gesendet}")
            hindernis = sms_hindernis(telefonie)
            versand = f"nicht möglich ({hindernis})" if hindernis else "möglich"
            if wartend := len(self.zustand["smsAusgang"]):
                versand += f", {wartend} wartend"
            zeilen.append(f"SMS: {versand}")
            zeilen.append(f"Meldungen: {MELDEWEG_TEXT[self.zustand['meldeweg']]}")
            zeilen.append(f"Lebenszeichen: {LEBENSZEICHEN_TERMIN_TEXT[self.zustand['lebenszeichenHäufigkeit']]} "
                          f"um {self._konfiguration['lebenszeichenUhrzeit']} Uhr")
            if batterie:
                ladung = "wird geladen" if batterie.get("plugged") != "UNPLUGGED" else "ohne Ladung"
                zeilen.append(f"Batterie: {batterie.get('percentage')} Prozent, {ladung}, "
                              f"{batterie.get('temperature', 0):.0f} Grad Celsius")
            else:
                zeilen.append("Batterie: unbekannt")
            gültige = self.letzte_gültige_position
            alter = f"vor {alter_text(self._uhr() - gültige.zeitpunkt)}" if gültige else "keine"
            zeilen.append(f"GPS: letzte gültige Position {alter}, "
                          f"Genauigkeitsgrenze {self.zustand['genauigkeitsgrenzeMeter']} Meter, "
                          f"Warnung nach {self.zustand['gpsWarnungNachSekunden'] // 60} Minuten")
            zeilen.append(f"Alarm: ein, Radius {self.zustand['radius']} Meter" if self.zustand["alarmEin"] else "Alarm: aus")
            return "\n".join(zeilen)

    def _setze_meldeweg(self, meldeweg):
        with self._sperre:
            self.zustand["meldeweg"] = meldeweg
            self._speichere_zustand()
        return f"Lebenszeichen, Start und Warnungen {MELDEWEG_TEXT[meldeweg]}. Der Alarm kommt immer per SMS."

    def _setze_lebenszeichen(self, häufigkeit):
        with self._sperre:
            self.zustand["lebenszeichenHäufigkeit"] = häufigkeit
            self._speichere_zustand()
        return (f"Lebenszeichen {LEBENSZEICHEN_TERMIN_TEXT[häufigkeit]} "
                f"um {self._konfiguration['lebenszeichenUhrzeit']} Uhr.")

    def _schalte_daten(self, ein):
        mobile_daten_geschaltet = self._gerät.schalte_daten(ein)
        with self._sperre:
            self.zustand["datenAus"] = not ein and mobile_daten_geschaltet
            self._speichere_zustand()
        schaltstellung = "ein" if ein else "aus"
        if mobile_daten_geschaltet:
            return f"Mobile Daten und WLAN {schaltstellung}." + ("" if ein else " Befehle und Meldungen nur per SMS.")
        return (f"WLAN {schaltstellung}. Mobile Daten konnten nicht {schaltstellung}geschaltet werden: "
                "weder Root noch ADB über localhost verfügbar.")

    def _schalte_fernzugriff(self, ein):
        with self._sperre:
            self.zustand["fernzugriffEin"] = ein
            self._speichere_zustand()
        if self._gerät.schalte_fernzugriff(ein):
            return "Fernzugriff ein: RustDesk und Tailscale laufen." if ein else "Fernzugriff aus: RustDesk und Tailscale deaktiviert, sie nutzen keine Daten."
        return (f"Fernzugriff konnte nicht {'ein' if ein else 'aus'}geschaltet werden"
                " (ADB über localhost fehlt oder RustDesk startet nicht).")

    def _setze_genauigkeitsgrenze(self, grenze):
        bereich = GENAUIGKEITSGRENZE_BEREICH_METER
        if grenze not in bereich:
            return f"Genauigkeitsgrenze muss zwischen {bereich.start} und {bereich.stop - 1} Metern liegen."
        with self._sperre:
            self.zustand["genauigkeitsgrenzeMeter"] = grenze
            self._speichere_zustand()
        return f"Genauigkeitsgrenze {grenze} Meter. Nur genauere Positionen zählen für den Alarm."

    def _setze_gps_warnung(self, minuten):
        bereich = GPS_WARNUNG_BEREICH_MINUTEN
        if minuten not in bereich:
            return f"GPS-Warnung muss zwischen {bereich.start} und {bereich.stop - 1} Minuten liegen."
        with self._sperre:
            self.zustand["gpsWarnungNachSekunden"] = minuten * 60
            self._speichere_zustand()
        return f"GPS-Warnung nach {minuten} Minuten ohne gültige Position."

    def _übernehme_anker(self, breite, länge):
        with self._sperre:
            self.zustand.update(ankerBreite=breite, ankerLänge=länge, ankerGesetztUm=self._uhr(), alarmEin=False)
            self._setze_alarmzustand_zurück()
            self._speichere_zustand()
            position = self._aktuelle_position()
            abstand = f" Aktueller Abstand {self._abstand_zum_anker(position):.0f} Meter." if position else ""
        return abstand + " Alarm ist aus, einschalten mit ALARM EIN."

    def _setze_anker_auf_koordinaten(self, breite, länge):
        if not (-90 <= breite <= 90 and -180 <= länge <= 180):
            return "Ungültige Koordinaten. Breite zwischen -90 und 90, Länge zwischen -180 und 180."
        return f"Anker gesetzt: {koordinaten_text(breite, länge)}." + self._übernehme_anker(breite, länge)

    def _setze_anker_auf_aktuelle_position(self):
        position = self.genaueste_position(MESSDAUER_SETZE_ANKER_SEKUNDEN)
        if not (position and self._ist_gültig(position)):
            position = self._aktuelle_position()
        if position is None:
            grenze = self.zustand["genauigkeitsgrenzeMeter"]
            return (f"Keine aktuelle Position mit einer Genauigkeit von {grenze} Metern oder besser. "
                    "Anker nicht gesetzt, bitte wiederholen oder Koordinaten angeben.")
        return (f"Anker gesetzt: {koordinaten_text(position.breite, position.länge)} "
                f"(Genauigkeit {position.genauigkeit:.0f} Meter)." + self._übernehme_anker(position.breite, position.länge))

    def _alarm_ein_mit_zuschlag(self):
        with self._sperre:
            if not self._anker_gesetzt():
                return "Kein Anker gesetzt. Zuerst SETZE ANKER senden."
            position = self._aktuelle_position()
            if position is None:
                return "Keine aktuelle gültige Position. Alarm bleibt aus. Alternativ ALARM EIN mit Radius senden."
            abstand = round(self._abstand_zum_anker(position))
            zuschlag = self._konfiguration["sicherheitszuschlagMeter"]
            self._schalte_alarm_ein(abstand + zuschlag)
            return f"Alarm ein. Radius {abstand + zuschlag} Meter (aktueller Abstand {abstand} Meter + {zuschlag} Meter)."

    def _alarm_ein_mit_radius(self, radius):
        with self._sperre:
            if not self._anker_gesetzt():
                return "Kein Anker gesetzt. Zuerst SETZE ANKER senden."
            self._schalte_alarm_ein(radius)
            antwort = f"Alarm ein. Radius {radius} Meter."
            position = self._aktuelle_position()
            if position is None:
                gültige = self.letzte_gültige_position
                alter = f"seit {alter_text(self._uhr() - gültige.zeitpunkt)}" if gültige else "bisher"
                antwort += f" <b>Achtung:</b> {alter} keine gültige GPS-Position, der Alarm kann so nicht auslösen."
            elif (abstand := self._abstand_zum_anker(position)) > radius:
                antwort += (f" Achtung: aktueller Abstand {abstand:.0f} Meter, Alarm folgt nach "
                            f"{AUFEINANDERFOLGENDE_POSITIONEN_FÜR_ALARM} gültigen Positionen.")
            return antwort

    def _schalte_alarm_ein(self, radius):
        self.zustand.update(radius=radius, alarmEin=True)
        self._setze_alarmzustand_zurück()
        self._speichere_zustand()

    # Track

    def _trackdatei(self, zeitpunkt):
        return self._datenordner / f"track_{utc_datum(zeitpunkt)}.csv"

    def _schreibe_trackzeile(self, position, abstand, gültig):
        datei = self._trackdatei(position.zeitpunkt)
        neu = not datei.exists()
        with datei.open("a", newline="", encoding="utf-8") as ausgabe:
            schreiber = csv.writer(ausgabe)
            if neu:
                schreiber.writerow(CSV_KOPFZEILE)
            schreiber.writerow([
                utc_zeitstempel(position.zeitpunkt), f"{position.breite:.7f}", f"{position.länge:.7f}",
                "" if position.genauigkeit is None else f"{position.genauigkeit:.1f}",
                "" if position.geschwindigkeit is None else f"{position.geschwindigkeit:.2f}",
                "" if abstand is None else f"{abstand:.1f}",
                "ja" if gültig else "nein",
            ])

    def _gültige_trackzeilen(self, beginn):
        jetzt = self._uhr()
        tageszeitpunkte = [*range(int(beginn), int(jetzt), 86400), jetzt]
        for datei in sorted({self._trackdatei(zeitpunkt) for zeitpunkt in tageszeitpunkte}):
            if not datei.exists():
                continue
            with datei.open(newline="", encoding="utf-8") as eingabe:
                for zeile in csv.DictReader(eingabe):
                    zeitpunkt = datetime.strptime(zeile["Zeitpunkt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
                    if zeile["Gültig"] == "ja" and zeitpunkt >= beginn:
                        yield zeitpunkt, zeile

    def track_gpx(self):
        punkte = [f'<trkpt lat="{zeile["Breite"]}" lon="{zeile["Länge"]}"><time>{zeile["Zeitpunkt"]}</time></trkpt>'
                  for _, zeile in self._gültige_trackzeilen(self._uhr() - TRACK_ZEITRAUM_SEKUNDEN)]
        if not punkte:
            return None
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="Ankerwache" xmlns="http://www.topografix.com/GPX/1/1">\n'
                "<trk><name>Ankerwache</name><trkseg>\n" + "\n".join(punkte) + "\n</trkseg></trk></gpx>\n").encode("utf-8")

    def _sende_track(self):
        inhalt = self.track_gpx()
        if inhalt is None:
            return "Keine gültigen Positionen in den letzten 7 Tagen."
        dateiname = f"ankerwache_{datetime.fromtimestamp(self._uhr()).strftime('%Y-%m-%d_%H%M')}.gpx"
        try:
            self._telegram.sende_datei(dateiname, inhalt, "Track der letzten 7 Tage")
        except (TelegramFehler, OSError, http.client.HTTPException, ValueError):
            protokoll.exception("Track-Versand fehlgeschlagen")
            return "Track-Versand fehlgeschlagen, bitte später erneut TRACK senden."
        return None

    def weitester_abstand_text(self):
        """Ersatz für den Track per SMS: die gültige Position mit dem größten Abstand zum aktuellen Anker."""
        with self._sperre:
            if not self._anker_gesetzt():
                return "Kein Anker gesetzt."
            anker_breite, anker_länge = self.zustand["ankerBreite"], self.zustand["ankerLänge"]
            # Positionen vor dem Setzen des Ankers, etwa bei der Anfahrt, sagen über das Schwojen nichts aus.
            beginn = max(self._uhr() - TRACK_ZEITRAUM_SEKUNDEN, self.zustand["ankerGesetztUm"] or 0)
        weiteste = max(((abstand_meter(anker_breite, anker_länge, float(zeile["Breite"]), float(zeile["Länge"])),
                         zeitpunkt, zeile) for zeitpunkt, zeile in self._gültige_trackzeilen(beginn)),
                       key=lambda eintrag: eintrag[0], default=None)
        if weiteste is None:
            return "Keine gültigen Positionen seit dem Setzen des Ankers in den letzten 7 Tagen."
        abstand, zeitpunkt, zeile = weiteste
        return (f"Weitester Abstand vom Anker (7 Tage, seit Anker gesetzt): {abstand:.0f} Meter bei Genauigkeit "
                f"{float(zeile['Genauigkeit']):.0f} Meter, {datetime.fromtimestamp(zeitpunkt).strftime('%d.%m. %H:%M')} Uhr, "
                f"Boot {koordinaten_text(float(zeile['Breite']), float(zeile['Länge']))}")

    # Telegram-Eingang

    def verarbeite_aktualisierung(self, aktualisierung):
        nachricht = aktualisierung.get("message")
        if not nachricht or nachricht["chat"]["id"] != self._telegram.gruppe:
            return None
        if "migrate_to_chat_id" in nachricht:
            self._telegram.übernehme_neue_gruppe(nachricht["migrate_to_chat_id"])
            return None
        if "text" not in nachricht:
            return None
        if self._uhr() - nachricht["date"] > HÖCHSTALTER_BEFEHL_SEKUNDEN + TELEGRAM_ABRUFABSTAND_SEKUNDEN:
            protokoll.info("Veralteten Befehl verworfen: %s", nachricht["text"])
            return None
        return self.verarbeite_befehl(nachricht["text"], KANAL_TELEGRAM)

    # SMS-Eingang

    def verarbeite_empfangene_sms(self, nachrichten):
        """Führt neue Befehle der SMS-Empfänger aus und liefert die Antworten als Paare aus Nummer und Text."""
        neue = sorted(nachrichten, key=lambda nachricht: nachricht["_id"])
        with self._sperre:
            letzte_kennung = self.zustand["letzteSmsKennung"]
            neueste_kennung = max([letzte_kennung or 0] + [nachricht["_id"] for nachricht in neue])
            if neueste_kennung != letzte_kennung:
                self.zustand["letzteSmsKennung"] = neueste_kennung
                self._speichere_zustand()
        # Beim allerersten Abruf sind alle vorhandenen SMS alt und keine Befehle an die Wache.
        if letzte_kennung is None:
            return []
        antworten = []
        for nachricht in neue:
            if nachricht["_id"] <= letzte_kennung:
                continue
            # Ältere Fassungen von Termux:API nennen den Absender nur unter "number".
            nummer = nachricht.get("address") or nachricht["number"]
            empfangen = datetime.strptime(nachricht["received"], "%Y-%m-%d %H:%M:%S").timestamp()
            if self._uhr() - empfangen > HÖCHSTALTER_BEFEHL_SEKUNDEN:
                protokoll.info("Veralteten SMS-Befehl verworfen: %s", nachricht["body"])
                continue
            if not any(gleiche_rufnummer(nummer, empfänger) for empfänger in self.zustand["smsEmpfänger"]):
                protokoll.info("SMS von fremder Nummer %s ignoriert", nummer)
                continue
            if antwort := self.verarbeite_befehl(nachricht["body"], KANAL_SMS):
                antworten.append((nummer, antwort))
        return antworten

    # Schleifen

    def _messschleife(self):
        intervall = self._konfiguration["messintervallSekunden"]
        while True:
            try:
                self.verarbeite_position(self.genaueste_position(intervall))
                self.verarbeite_batterie(self._gerät.batterie())
            except Exception:
                protokoll.exception("Fehler in der Messschleife")
                self._schlafen(intervall)
            self.letzter_messversuch = self._uhr()
            self._erste_messung_fertig.set()

    def _telegramschleife(self):
        versatz = 0
        fehlversuche = 0
        konflikt_gemeldet = False
        while True:
            beginn = self._uhr()
            try:
                aktualisierungen = self._telegram.hole_aktualisierungen(versatz)
                self._letzter_telegram_kontakt = self._uhr()
            except Exception as fehler:
                # Telegram antwortet mit 409, wenn ein zweites Gerät denselben Bot abfragt.
                if isinstance(fehler, TelegramFehler) and fehler.code == 409 and not konflikt_gemeldet:
                    self.sende_telegram("Ein zweites Gerät fragt denselben Bot ab. Befehle werden doppelt "
                                        "oder gar nicht beantwortet. Die Ankerwache auf dem anderen Gerät beenden.")
                    konflikt_gemeldet = True
                fehlversuche += 1
                self._warte_nach_fehler("Telegram-Abruf", fehlversuche, fehler)
                continue
            fehlversuche = 0
            for aktualisierung in aktualisierungen:
                versatz = aktualisierung["update_id"] + 1
                try:
                    antwort = self.verarbeite_aktualisierung(aktualisierung)
                except Exception:
                    protokoll.exception("Fehler bei der Verarbeitung einer Telegram-Nachricht")
                    continue
                if antwort:
                    self.sende_telegram(antwort)
            self._schlafen(max(0, beginn + TELEGRAM_ABRUFABSTAND_SEKUNDEN - self._uhr()))

    def versende(self, meldung):
        text, erstellt = meldung
        fehlversuche = 0
        while True:
            if self._uhr() - erstellt >= VERSPÄTUNG_FÜR_HINWEIS_SEKUNDEN:
                zeitpunkt = datetime.fromtimestamp(erstellt).strftime("%d.%m. %H:%M")
                zugestellt = f"{text}\n\n<i>Verspätet zugestellt, erstellt am {zeitpunkt} Uhr.</i>"
            else:
                zugestellt = text
            try:
                self._telegram.sende_nachricht(zugestellt)
                self._letzter_telegram_kontakt = self._uhr()
                break
            except Exception as fehler:
                if isinstance(fehler, TelegramFehler) and fehler.dauerhaft:
                    protokoll.error("Telegram lehnt die Nachricht ab, sie wird verworfen: %s\n%s", fehler, text)
                    break
                fehlversuche += 1
                self._warte_nach_fehler("Telegram-Versand", fehlversuche, fehler)
        with self._sperre:
            self.zustand["postausgang"].remove(meldung)
            self._speichere_zustand()

    def _warte_nach_fehler(self, vorgang, fehlversuche, fehler):
        wartezeit = wartezeit_nach_fehlern(fehlversuche, fehler)
        # Ohne Netz würde sonst jede Minute eine Zeile ins Protokoll geschrieben.
        if fehlversuche == 1 or wartezeit != WARTEZEIT_OHNE_NETZ_SEKUNDEN:
            protokoll.warning("%s fehlgeschlagen, neuer Versuch in %d Minuten: %r", vorgang, wartezeit // 60, fehler)
        beginn = self._uhr()
        while (verbleibend := beginn + wartezeit - self._uhr()) > 0:
            if self._daten_eingeschaltet_um is not None and self._daten_eingeschaltet_um >= beginn:
                return
            self._schlafen(min(verbleibend, PRÜFABSTAND_WÄHREND_PAUSE_SEKUNDEN))

    def _postausgangsschleife(self):
        while True:
            self.versende(self._postausgang.get())

    def _smsschleife(self):
        while True:
            try:
                for nummer, antwort in self.verarbeite_empfangene_sms(self._gerät.empfangene_sms()):
                    self.sende_sms(nummer, antwort)
            except Exception:
                protokoll.exception("Fehler beim Abruf der SMS")
            self._schlafen(SMS_ABFRAGEABSTAND_SEKUNDEN)

    def _sms_ausgangsschleife(self):
        while True:
            self._sms_wartet.wait()
            # Vor dem Versand zurücksetzen, damit eine währenddessen eingereihte SMS die Schleife erneut weckt.
            self._sms_wartet.clear()
            if not self.versende_wartende_sms():
                self._sms_wartet.set()
                self._schlafen(SMS_WIEDERHOLUNGSABSTAND_SEKUNDEN)

    def sende_startmeldung(self):
        with self._sperre:
            letzte = self.zustand["letzteStartmeldung"]
            if letzte is not None and self._uhr() - letzte < STARTMELDUNG_MINDESTABSTAND_SEKUNDEN:
                protokoll.info("Startmeldung entfällt, die letzte ist weniger als eine Stunde alt")
                return
            self.zustand["letzteStartmeldung"] = self._uhr()
            self._speichere_zustand()
        self.melde(self.statusmeldung("Ankerwache gestartet"), self.sms_statusmeldung("Ankerwache gestartet"))

    def schalte_fernzugriff_beim_start_aus(self):
        # Fernzugriff kostet ständig Daten und läuft daher nur nach FERNZUGRIFF EIN.
        if not self.zustand["fernzugriffEin"] and not self._gerät.schalte_fernzugriff(False):
            protokoll.warning("Fernzugriff ließ sich beim Start nicht ausschalten")

    def starte(self):
        # Die Startmeldung ersetzt ein am selben Tag bereits fälliges Lebenszeichen.
        if datetime.now().strftime("%H:%M") >= self._konfiguration["lebenszeichenUhrzeit"]:
            self._letztes_lebenszeichen = datetime.now().date()
        for ziel in (self._messschleife, self._telegramschleife, self._postausgangsschleife,
                     self._smsschleife, self._sms_ausgangsschleife):
            threading.Thread(target=ziel, daemon=True).start()
        self.schalte_fernzugriff_beim_start_aus()
        # Die Startmeldung soll bereits die Position enthalten.
        self._erste_messung_fertig.wait(HÄNGENDE_MESSSCHLEIFE_SEKUNDEN)
        self.sende_startmeldung()
        while True:
            self._schlafen(30)
            self.prüfe_lebenszeichen(datetime.now())
            self.prüfe_daten_aus()
            self.prüfe_gps_ausfall()
            if self._uhr() - self.letzter_messversuch > HÄNGENDE_MESSSCHLEIFE_SEKUNDEN:
                protokoll.error("Messschleife hängt, Programm wird neu gestartet")
                os._exit(1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    DATENORDNER.mkdir(exist_ok=True)
    import fcntl  # nur unter Unix vorhanden; hier statt am Dateianfang, damit die Tests auch unter Windows laufen
    # Die Sperre gilt, solange die Datei offen ist, also bis zum Prozessende; deshalb kein with-Block.
    sperrdatei = open(DATENORDNER / "sperre", "w")  # noqa: SIM115
    try:
        fcntl.flock(sperrdatei, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        protokoll.info("Ankerwache läuft bereits")
        sys.exit(3)
    konfigurationsdatei = PROGRAMMORDNER / "konfiguration.json"
    konfiguration = json.loads(konfigurationsdatei.read_text(encoding="utf-8"))

    def speichere_neue_gruppe(gruppe):
        konfiguration["telegramGruppe"] = gruppe
        schreibe_json_atomar(konfigurationsdatei, konfiguration)

    telegram = Telegram(konfiguration["telegramToken"], konfiguration["telegramGruppe"], speichere_neue_gruppe)
    Ankerwache(konfiguration, TermuxGerät(), telegram).starte()


if __name__ == "__main__":
    main()
