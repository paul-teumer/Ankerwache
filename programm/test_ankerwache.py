import io
import json
import math
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import ankerwache
from ankerwache import (
    KANAL_SMS,
    KANAL_TELEGRAM,
    Ankerwache,
    Position,
    TelegramFehler,
    abstand_meter,
    gleiche_rufnummer,
    mehrteiliger_körper,
    peilung_grad,
    sms_anzahl,
    sms_fassung,
    wartezeit_nach_fehlern,
)

ANKER_BREITE, ANKER_LÄNGE = 54.321000, 10.123000
KONFIGURATION = {
    "smsEmpfänger": ["+491700000001", "+491700000002"],
    "sicherheitszuschlagMeter": 20,
    "genauigkeitsgrenzeMeter": 15,
    "messintervallSekunden": 30,
    "zweiteMeldungNachSekunden": 1800,
    "gpsWarnungNachSekunden": 1800,
    "batteriewarnungProzent": 20,
    "meldeweg": "telegram",
    "lebenszeichenHäufigkeit": "täglich",
    "lebenszeichenUhrzeit": "09:00",
}


def versetzt(meter_nord, meter_ost=0.0):
    breite = ANKER_BREITE + math.degrees(meter_nord / 6371008.8)
    länge = ANKER_LÄNGE + math.degrees(meter_ost / (6371008.8 * math.cos(math.radians(ANKER_BREITE))))
    return breite, länge


class Uhr:
    def __init__(self):
        self.zeit = 1_790_000_000.0

    def __call__(self):
        return self.zeit

    def schlafen(self, sekunden):
        self.zeit += sekunden


class Gerät:
    def __init__(self, uhr):
        self.uhr = uhr
        self.sms = []
        self.positionen = []
        self.mobile_daten_schaltbar = True
        self.daten_ein = True
        self.sim_state = "ready"
        self.netzbetreiber = "KPN"
        # Wird der Reihe nach bei jedem Sendeversuch ausgelöst, solange Einträge vorhanden sind.
        self.sms_fehler = []

    def position(self):
        self.uhr.zeit += 1
        return self.positionen.pop(0) if self.positionen else None

    def sende_sms(self, nummer, text):
        if self.sms_fehler:
            raise self.sms_fehler.pop(0)
        self.sms.append((nummer, text))

    def sms_hindernis(self):
        return ankerwache.sms_hindernis(self.gerätedaten()["telefonie"])

    def gerätedaten(self):
        return {"telefonie": {"data_state": "connected", "network_operator_name": self.netzbetreiber,
                              "network_type": "lte", "network_roaming": True, "sim_state": self.sim_state},
                "wlan": {"supplicant_state": "COMPLETED", "ssid": '"Hafen"'},
                "batterie": {"percentage": 77, "plugged": "PLUGGED_AC", "temperature": 31.4},
                "schaltweg": "ADB"}

    def schalte_daten(self, ein):
        if self.mobile_daten_schaltbar:
            self.daten_ein = ein
        return self.mobile_daten_schaltbar

    def schalte_fernzugriff(self, ein):
        self.fernzugriff_ein = ein
        return self.mobile_daten_schaltbar


class Telegram:
    def __init__(self):
        self.gruppe = -5570195291
        self.dateien = []
        self.gesendet = []
        # Wird der Reihe nach bei jedem Sendeversuch ausgelöst, solange Einträge vorhanden sind.
        self.fehler = []

    def sende_nachricht(self, text):
        if self.fehler:
            raise self.fehler.pop(0)
        self.gesendet.append(text)

    def sende_datei(self, dateiname, inhalt, beschriftung):
        self.dateien.append((dateiname, inhalt))

    def übernehme_neue_gruppe(self, gruppe):
        self.gruppe = gruppe


class Verbindung:
    """Ersetzt http.client.HTTPSConnection; jede Anfrage liefert die nächste Antwort oder löst den nächsten Fehler aus."""
    antworten = []
    erzeugt = 0

    def __init__(self, host):
        Verbindung.erzeugt += 1
        self.sock = None
        self.timeout = None

    def request(self, methode, pfad, körper, kopfzeilen):
        self._nächste = Verbindung.antworten.pop(0)
        if isinstance(self._nächste, Exception):
            raise self._nächste

    def getresponse(self):
        return io.BytesIO(json.dumps(self._nächste).encode("utf-8"))

    def close(self):
        pass


class Testfall(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.uhr = Uhr()
        self.gerät = Gerät(self.uhr)
        self.telegram = Telegram()
        self.wache = self.neue_wache()

    def tearDown(self):
        self.ordner.cleanup()

    def neue_wache(self):
        return Ankerwache(KONFIGURATION, self.gerät, self.telegram, uhr=self.uhr,
                          schlafen=self.uhr.schlafen, datenordner=self.ordner.name)

    def position(self, meter_nord, genauigkeit=5.0, geschwindigkeit=0.3):
        breite, länge = versetzt(meter_nord)
        return Position(breite, länge, genauigkeit, geschwindigkeit, self.uhr())

    def messe(self, meter_nord, genauigkeit=5.0):
        self.uhr.zeit += 30
        self.wache.verarbeite_position(self.position(meter_nord, genauigkeit))
        self.wache.versende_wartende_sms()

    def telegramtexte(self):
        texte = []
        while not self.wache._postausgang.empty():
            texte.append(self.wache._postausgang.get()[0])
        # Gilt als zugestellt, damit eine neue Wache die Meldungen nicht erneut versendet.
        self.wache.zustand["postausgang"].clear()
        self.wache._speichere_zustand()
        return texte

    def ausgehende_sms(self):
        nachrichten = [(nummer, text) for nummer, text, _ in self.wache.zustand["smsAusgang"]]
        self.wache.zustand["smsAusgang"].clear()
        return nachrichten

    def scharf(self, radius=70):
        self.wache.verarbeite_befehl(f"setze anker {ANKER_BREITE}, {ANKER_LÄNGE}", KANAL_TELEGRAM)
        self.messe(50)
        self.assertEqual(self.wache.verarbeite_befehl(f"alarm ein {radius}", KANAL_TELEGRAM), f"Alarm ein. Radius {radius} Meter.")
        self.telegramtexte()


class Geometrie(unittest.TestCase):
    def test_abstand_eine_bogenminute_breite(self):
        self.assertAlmostEqual(abstand_meter(0, 0, 1 / 60, 0), 1853.25, delta=0.1)

    def test_sechs_nachkommastellen_unter_einem_meter(self):
        self.assertLess(abstand_meter(54, 10, 54.000001, 10.000001), 0.2)

    def test_peilung(self):
        self.assertAlmostEqual(peilung_grad(54, 10, 55, 10), 0, delta=0.01)
        self.assertAlmostEqual(peilung_grad(54, 10, 54, 11), 90, delta=0.5)
        self.assertAlmostEqual(peilung_grad(54, 10, 53, 10), 180, delta=0.01)


class Alarm(Testfall):
    def test_alarm_erst_nach_drei_gültigen_positionen_außerhalb(self):
        self.scharf()
        self.messe(80)
        self.messe(80)
        self.messe(80, genauigkeit=30)
        self.assertEqual(self.gerät.sms, [])
        self.messe(80)
        self.assertEqual(len(self.gerät.sms), 2)
        self.assertTrue(self.gerät.sms[0][1].startswith("ANKERALARM 54.321"))
        self.assertTrue(any(text.startswith("<b>ANKERALARM</b>\nBoot: 54.321") for text in self.telegramtexte()))

    def test_rückkehr_setzt_zähler_zurück(self):
        self.scharf()
        for abstand in (80, 80, 60, 80, 80, 60):
            self.messe(abstand)
        self.assertEqual(self.gerät.sms, [])

    def test_schwojen_innerhalb_des_radius(self):
        self.wache.verarbeite_befehl(f"SETZE ANKER {ANKER_BREITE} {ANKER_LÄNGE}", KANAL_TELEGRAM)
        self.messe(50)
        self.wache.verarbeite_befehl("ALARM EIN", KANAL_TELEGRAM)
        self.assertEqual(self.wache.zustand["radius"], 70)
        for winkel in range(0, 360, 10):
            self.uhr.zeit += 30
            breite, länge = versetzt(50 * math.cos(math.radians(winkel)), 50 * math.sin(math.radians(winkel)))
            self.wache.verarbeite_position(Position(breite, länge, 5.0, 0.2, self.uhr()))
        self.assertEqual(self.gerät.sms, [])

    def test_zweite_meldung_nach_30_minuten_danach_nichts(self):
        self.scharf()
        for _ in range(3):
            self.messe(90)
        self.assertEqual(len(self.gerät.sms), 2)
        for _ in range(59):
            self.messe(90)
        self.assertEqual(len(self.gerät.sms), 2)
        self.messe(90)
        self.assertEqual(len(self.gerät.sms), 4)
        self.assertTrue(self.gerät.sms[-1][1].startswith("ANKERALARM ERNEUT"))
        for _ in range(200):
            self.messe(90)
        self.assertEqual(len(self.gerät.sms), 4)

    def test_kein_alarm_wenn_aus(self):
        self.scharf()
        self.wache.verarbeite_befehl("ALARM AUS", KANAL_TELEGRAM)
        for _ in range(5):
            self.messe(200)
        self.assertEqual(self.gerät.sms, [])

    def test_sms_höchstens_160_zeichen_ohne_umlaute(self):
        self.wache.zustand.update(ankerBreite=-89.123456, ankerLänge=-179.123456, radius=9999, alarmEin=True)
        self.wache.batterie = (100, True)
        position = Position(-89.123999, -179.123999, 5.0, 10.0, self.uhr())
        for erneut in (False, True):
            text = self.wache.alarmtext(position, erneut)
            self.assertLessEqual(len(text), 160, text)
            self.assertTrue(text.isascii(), text)


class Befehle(Testfall):
    def test_falscher_befehl_liefert_übersicht(self):
        antwort = self.wache.verarbeite_befehl("anker hoch", KANAL_TELEGRAM)
        self.assertTrue(antwort.startswith("<b>Falscher Befehl.</b>"))
        self.assertIn("ALARM EIN 80", antwort)

    def test_koordinatenformate(self):
        for eingabe in ("SETZE ANKER 54.321, 10.123", "setze  anker 54.321 10.123", "SETZE ANKER 54.321;10.123"):
            self.assertTrue(self.wache.verarbeite_befehl(eingabe, KANAL_TELEGRAM).startswith("Anker gesetzt: 54.321000, 10.123000"))
        self.assertTrue(self.wache.verarbeite_befehl("SETZE ANKER -12.5, -170.25", KANAL_TELEGRAM).startswith("Anker gesetzt: -12.500000"))
        self.assertTrue(self.wache.verarbeite_befehl("SETZE ANKER 95.0, 10.0", KANAL_TELEGRAM).startswith("Ungültige Koordinaten"))

    def test_anker_setzen_schaltet_alarm_aus(self):
        self.scharf()
        self.wache.verarbeite_befehl("SETZE ANKER 54.3, 10.1", KANAL_TELEGRAM)
        self.assertFalse(self.wache.zustand["alarmEin"])

    def test_radius_kleiner_als_abstand_schaltet_mit_warnung_scharf(self):
        self.wache.verarbeite_befehl(f"SETZE ANKER {ANKER_BREITE}, {ANKER_LÄNGE}", KANAL_TELEGRAM)
        self.messe(50)
        self.assertIn("Achtung: aktueller Abstand 50 Meter", self.wache.verarbeite_befehl("ALARM EIN 40", KANAL_TELEGRAM))
        self.assertTrue(self.wache.zustand["alarmEin"])

    def test_alarm_ein_ohne_anker(self):
        self.assertIn("Kein Anker gesetzt", self.wache.verarbeite_befehl("ALARM EIN", KANAL_TELEGRAM))

    def test_anker_auf_aktuelle_position(self):
        self.gerät.positionen = [self.position(0)]
        antwort = self.wache.verarbeite_befehl("SETZE ANKER", KANAL_TELEGRAM)
        self.assertIn("Genauigkeit 5 Meter", antwort)
        self.assertLess(abstand_meter(ANKER_BREITE, ANKER_LÄNGE, self.wache.zustand["ankerBreite"],
                                      self.wache.zustand["ankerLänge"]), 0.01)

    def test_anker_nimmt_genaueste_messung(self):
        self.gerät.positionen = [self.position(0, genauigkeit=40), self.position(3, genauigkeit=6),
                                 self.position(9, genauigkeit=12)]
        self.assertIn("Genauigkeit 6 Meter", self.wache.verarbeite_befehl("SETZE ANKER", KANAL_TELEGRAM))
        self.assertAlmostEqual(abstand_meter(ANKER_BREITE, ANKER_LÄNGE, self.wache.zustand["ankerBreite"],
                                             self.wache.zustand["ankerLänge"]), 3, delta=0.1)

    def test_anker_ungenaue_messung_nimmt_letzte_gültige_position(self):
        self.messe(10)
        self.gerät.positionen = [self.position(0, genauigkeit=40)]
        self.assertTrue(self.wache.verarbeite_befehl("SETZE ANKER", KANAL_TELEGRAM).startswith("Anker gesetzt"))
        self.assertAlmostEqual(abstand_meter(ANKER_BREITE, ANKER_LÄNGE, self.wache.zustand["ankerBreite"],
                                             self.wache.zustand["ankerLänge"]), 10, delta=0.1)

    def test_anker_auf_aktuelle_position_zu_ungenau(self):
        self.gerät.positionen = [self.position(0, genauigkeit=40)]
        self.assertIn("Anker nicht gesetzt", self.wache.verarbeite_befehl("SETZE ANKER", KANAL_TELEGRAM))
        self.assertIsNone(self.wache.zustand["ankerBreite"])

    def test_zustand_übersteht_neustart(self):
        self.scharf(radius=85)
        neu = self.neue_wache()
        self.assertEqual(neu.zustand["radius"], 85)
        self.assertTrue(neu.zustand["alarmEin"])

    def test_statusmeldung(self):
        self.scharf()
        self.wache.batterie = (88, True)
        meldung = self.wache.verarbeite_befehl("POSITION", KANAL_TELEGRAM)
        self.assertIn("Alarm: <b>ein</b>, Radius 70 Meter", meldung)
        self.assertIn("\nAbstand: 50 Meter\n", meldung)
        self.assertIn("https://www.google.com/maps?q=", meldung)

    def test_genauigkeitsgrenze_per_befehl(self):
        self.messe(10, genauigkeit=20)
        self.assertIsNone(self.wache.letzte_gültige_position)
        self.assertIn("zwischen 1 und 100", self.wache.verarbeite_befehl("GENAUIGKEIT 200", KANAL_TELEGRAM))
        self.assertTrue(self.wache.verarbeite_befehl("genauigkeit 25", KANAL_TELEGRAM).startswith("Genauigkeitsgrenze 25 Meter"))
        self.messe(10, genauigkeit=20)
        self.assertIsNotNone(self.wache.letzte_gültige_position)
        self.assertIn("Genauigkeitsgrenze: 25 Meter", self.neue_wache().statusmeldung("Status"))

    def test_statusmeldung_zeigt_ungenaue_messung(self):
        self.messe(20, genauigkeit=40)
        meldung = self.wache.statusmeldung("Status")
        self.assertIn("Genauigkeit: 40 Meter (zu ungenau für den Alarm)", meldung)
        self.assertIn("Letzte gültige Position: keine", meldung)
        self.assertNotIn("noch keine Position", meldung)


class Telegrameingang(Testfall):
    def aktualisierung(self, text, gruppe=-5570195291, alter=0):
        return {"update_id": 1, "message": {"chat": {"id": gruppe}, "date": self.uhr() - alter, "text": text}}

    def test_neue_gruppennummer_aus_nachricht(self):
        umzug = {"update_id": 1, "message": {"chat": {"id": -5570195291}, "date": self.uhr(), "migrate_to_chat_id": -1004}}
        self.assertIsNone(self.wache.verarbeite_aktualisierung(umzug))
        self.assertIsNotNone(self.wache.verarbeite_aktualisierung(self.aktualisierung("POSITION", gruppe=-1004)))

    def test_nur_eigene_gruppe(self):
        self.assertIsNone(self.wache.verarbeite_aktualisierung(self.aktualisierung("POSITION", gruppe=123)))
        self.assertIsNotNone(self.wache.verarbeite_aktualisierung(self.aktualisierung("POSITION")))

    def test_veraltete_befehle_verworfen(self):
        self.assertIsNone(self.wache.verarbeite_aktualisierung(self.aktualisierung("ALARM AUS", alter=601)))

    def test_befehl_aus_dem_abrufabstand_gilt(self):
        self.assertIsNotNone(self.wache.verarbeite_aktualisierung(self.aktualisierung("POSITION", alter=590)))

    def test_abfrage_alle_fünf_minuten(self):
        class Abbruch(BaseException):
            pass
        zeitpunkte = []

        def hole_aktualisierungen(versatz):
            zeitpunkte.append(self.uhr())
            if len(zeitpunkte) == 3:
                raise Abbruch
            self.uhr.zeit += 2
            return [self.aktualisierung("POSITION")] if len(zeitpunkte) == 1 else []
        self.telegram.hole_aktualisierungen = hole_aktualisierungen
        with self.assertRaises(Abbruch):
            self.wache._telegramschleife()
        self.assertEqual([b - a for a, b in zip(zeitpunkte, zeitpunkte[1:])], [300, 300])
        self.assertEqual(len(self.telegramtexte()), 1)


class Track(Testfall):
    def test_gpx_enthält_nur_gültige_punkte(self):
        self.messe(10)
        self.messe(20, genauigkeit=50)
        self.messe(30)
        gpx = self.wache.track_gpx().decode("utf-8")
        self.assertEqual(gpx.count("<trkpt"), 2)
        self.assertIsNone(self.wache.verarbeite_befehl("TRACK", KANAL_TELEGRAM))
        self.assertEqual(len(self.telegram.dateien), 1)

    def test_gpx_umfasst_sieben_tage(self):
        self.messe(10)
        for _ in range(6):
            self.uhr.zeit += 86400
            self.messe(10)
        self.assertEqual(self.wache.track_gpx().decode("utf-8").count("<trkpt"), 7)
        self.uhr.zeit += 86400
        self.assertEqual(self.wache.track_gpx().decode("utf-8").count("<trkpt"), 6)

    def test_mehrteiliger_körper(self):
        grenze, körper = mehrteiliger_körper({"chat_id": "-1"}, "document", "track.gpx", b"<gpx/>")
        self.assertIn(b'name="document"; filename="track.gpx"', körper)
        self.assertTrue(körper.endswith(f"--{grenze}--\r\n".encode()))


class Überwachung(Testfall):
    def gps_meldungen(self):
        return [text for text in self.telegramtexte() if "GPS" in text]

    def test_gps_ausfall_und_wiederkehr(self):
        self.uhr.zeit += 1801
        self.wache.prüfe_gps_ausfall()
        self.wache.prüfe_gps_ausfall()
        self.messe(0)
        meldungen = self.gps_meldungen()
        self.assertEqual(len(meldungen), 1)
        self.assertIn("Keine gültige GPS-Position", meldungen[0])
        for _ in range(60):
            self.messe(0)
        self.assertEqual(self.gps_meldungen(), ["GPS wieder verfügbar, seit 30 Minuten stabil."])

    def test_schwankendes_gps_nur_eine_warnung(self):
        for _ in range(5):
            self.uhr.zeit += 1801
            self.wache.prüfe_gps_ausfall()
            for _ in range(10):
                self.messe(0)
        self.assertEqual(len(self.gps_meldungen()), 1)

    def test_gps_warnung_per_befehl_einstellbar(self):
        self.assertIn("zwischen 5 und 240", self.wache.verarbeite_befehl("GPS WARNUNG 2", KANAL_TELEGRAM))
        self.assertEqual(self.wache.verarbeite_befehl("gps warnung 10", KANAL_TELEGRAM), "GPS-Warnung nach 10 Minuten ohne gültige Position.")
        self.uhr.zeit += 601
        self.wache.prüfe_gps_ausfall()
        self.assertEqual(len(self.gps_meldungen()), 1)
        self.assertEqual(self.neue_wache().zustand["gpsWarnungNachSekunden"], 600)

    def test_gps_warnung_übersteht_neustart(self):
        self.messe(0)
        self.uhr.zeit += 1801
        self.wache.prüfe_gps_ausfall()
        self.telegramtexte()
        neu = self.neue_wache()
        neu.prüfe_gps_ausfall()
        self.assertTrue(neu._postausgang.empty())
        self.assertIn("Gemessen: vor 30 Minuten", neu.statusmeldung("Status"))

    def test_alarm_ein_ohne_gültige_position_warnt(self):
        self.messe(0)
        self.wache.verarbeite_befehl(f"setze anker {ANKER_BREITE}, {ANKER_LÄNGE}", KANAL_TELEGRAM)
        self.uhr.zeit += 7200
        antwort = self.wache.verarbeite_befehl("alarm ein 50", KANAL_TELEGRAM)
        self.assertEqual(antwort, "Alarm ein. Radius 50 Meter. <b>Achtung:</b> seit 2 Stunden keine gültige "
                                  "GPS-Position, der Alarm kann so nicht auslösen.")

    def test_batteriewarnung_einmal_ohne_lademeldungen(self):
        for messung in ((50, True), (19, True), (18, False), (17, False), (35, True)):
            self.wache.verarbeite_batterie(messung)
        texte = self.telegramtexte()
        self.assertEqual(texte, ["<b>Batterie niedrig:</b> 19 Prozent."])


class Telegramversand(Testfall):
    def test_wartezeit_verdoppelt_sich_bis_30_minuten(self):
        self.assertEqual([wartezeit_nach_fehlern(fehlversuch, OSError()) for fehlversuch in range(1, 5)],
                         [600, 1200, 1800, 1800])
        self.assertEqual(wartezeit_nach_fehlern(1, TelegramFehler(429, "", {"retry_after": 900})), 900)

    def test_ohne_netz_neuer_versuch_nach_einer_minute(self):
        self.assertEqual(wartezeit_nach_fehlern(5, ankerwache.socket.gaierror(7, "No address")), 60)
        self.assertEqual(wartezeit_nach_fehlern(5, OSError(ankerwache.errno.ENETUNREACH, "Network is unreachable")), 60)
        self.assertEqual(wartezeit_nach_fehlern(
            5, ConnectionAbortedError(ankerwache.errno.ECONNABORTED, "Software caused connection abort")), 60)

    def test_abgelehnte_nachricht_wird_verworfen(self):
        self.telegram.fehler = [TelegramFehler(400, "Bad Request", {})]
        beginn = self.uhr()
        self.wache.sende_telegram("Test")
        self.wache.versende(self.wache._postausgang.get())
        self.assertEqual(self.uhr(), beginn)
        self.assertEqual(self.telegram.gesendet, [])
        self.assertEqual(self.neue_wache().zustand["postausgang"], [])

    def test_netzfehler_mit_wachsender_pause(self):
        self.telegram.fehler = [OSError(), OSError(), TelegramFehler(502, "Bad Gateway", {})]
        beginn = self.uhr()
        self.wache.sende_telegram("Test")
        self.wache.versende(self.wache._postausgang.get())
        self.assertEqual(self.uhr() - beginn, 600 + 1200 + 1800)
        erstellt = datetime.fromtimestamp(beginn).strftime("%d.%m. %H:%M")
        self.assertEqual(self.telegram.gesendet, [f"Test\n\n<i>Verspätet zugestellt, erstellt am {erstellt} Uhr.</i>"])
        self.assertEqual(self.neue_wache().zustand["postausgang"], [])

    def test_daten_ein_beendet_pause_nach_fehler(self):
        def schlafen_mit_daten_ein(sekunden):
            self.uhr.schlafen(sekunden)
            self.wache.verarbeite_befehl("DATEN EIN", ankerwache.KANAL_SMS)
        self.wache._schlafen = schlafen_mit_daten_ein
        self.telegram.fehler = [OSError()]
        beginn = self.uhr()
        self.wache.sende_telegram("Test")
        self.wache.versende(self.wache._postausgang.get())
        self.assertEqual(self.uhr() - beginn, ankerwache.PRÜFABSTAND_WÄHREND_PAUSE_SEKUNDEN)
        self.assertEqual(self.telegram.gesendet, ["Test"])

    def test_rechtzeitig_zugestellt_ohne_hinweis(self):
        self.telegram.fehler = [ankerwache.socket.gaierror(7, "No address")]
        self.wache.sende_telegram("Test")
        self.wache.versende(self.wache._postausgang.get())
        self.assertEqual(self.telegram.gesendet, ["Test"])

    def test_unzugestellte_meldung_übersteht_neustart(self):
        self.wache.sende_telegram("ANKERALARM")
        neu = self.neue_wache()
        neu.versende(neu._postausgang.get())
        self.assertEqual(self.telegram.gesendet, ["ANKERALARM"])
        self.assertTrue(self.neue_wache()._postausgang.empty())

    def test_startmeldung_höchstens_einmal_pro_stunde(self):
        gesendet = []
        for abstand in (0, 1800, 1799, 1):
            self.uhr.zeit += abstand
            neu = self.neue_wache()
            neu.sende_startmeldung()
            gesendet.append(not neu._postausgang.empty())
            neu.zustand["postausgang"].clear()
            neu._speichere_zustand()
        self.assertEqual(gesendet, [True, False, False, True])


class Telegramverbindung(unittest.TestCase):
    def setUp(self):
        Verbindung.antworten, Verbindung.erzeugt = [], 0
        patch = mock.patch.object(ankerwache.http.client, "HTTPSConnection", Verbindung)
        patch.start()
        self.addCleanup(patch.stop)
        self.neue_gruppen = []
        self.telegram = ankerwache.Telegram("token", -1, self.neue_gruppen.append)

    def test_verbindung_bleibt_offen(self):
        Verbindung.antworten = [{"ok": True, "result": []}, {"ok": True, "result": []}]
        self.telegram.hole_aktualisierungen(0)
        self.telegram.hole_aktualisierungen(0)
        self.assertEqual(Verbindung.erzeugt, 1)

    def test_getrennte_verbindung_wird_sofort_neu_aufgebaut(self):
        Verbindung.antworten = [{"ok": True, "result": []}, ConnectionResetError(), {"ok": True, "result": [1]}]
        self.telegram.hole_aktualisierungen(0)
        self.assertEqual(self.telegram.hole_aktualisierungen(0), [1])
        self.assertEqual(Verbindung.erzeugt, 2)

    def test_fehler_bei_neuer_verbindung_wird_weitergegeben(self):
        Verbindung.antworten = [ConnectionResetError()]
        with self.assertRaises(ConnectionResetError):
            self.telegram.hole_aktualisierungen(0)

    def test_neue_gruppennummer_wird_übernommen(self):
        Verbindung.antworten = [{"ok": False, "error_code": 400, "description": "group chat was upgraded",
                                 "parameters": {"migrate_to_chat_id": -1004}}, {"ok": True, "result": {}}]
        self.telegram.sende_nachricht("Test")
        self.assertEqual((self.telegram.gruppe, self.neue_gruppen), (-1004, [-1004]))

    def test_ablehnung_wird_als_telegramfehler_gemeldet(self):
        Verbindung.antworten = [{"ok": False, "error_code": 403, "description": "Forbidden"}]
        with self.assertRaises(TelegramFehler) as fehler:
            self.telegram.sende_nachricht("Test")
        self.assertTrue(fehler.exception.dauerhaft)


class SmsSteuerung(Testfall):
    def sms(self, kennung, text, nummer="+491700000001", alter=0):
        empfangen = datetime.fromtimestamp(self.uhr() - alter).strftime("%Y-%m-%d %H:%M:%S")
        return {"_id": kennung, "number": nummer, "received": empfangen, "body": text}

    def test_rufnummern(self):
        self.assertTrue(gleiche_rufnummer("00491700000001", "+491700000001"))
        self.assertFalse(gleiche_rufnummer("+491700000001", "+491700000002"))
        self.assertFalse(gleiche_rufnummer("Vodafone", "+491700000001"))

    def test_rufnummern_international(self):
        deutsch = "+491700000003"
        for gleich in ("+49 170 0000003", "00491700000003", "491700000003", "01700000003", "1700000003"):
            self.assertTrue(gleiche_rufnummer(gleich, deutsch), gleich)
        # Ziffernfolgen, die zufällig auf die deutsche Nummer enden, aus den USA, den Niederlanden und national.
        for fremd in ("+1700000003", "+311700000003", "+31600000003", "0600000003", "00000003"):
            self.assertFalse(gleiche_rufnummer(fremd, deutsch), fremd)
        self.assertTrue(gleiche_rufnummer("0612345678", "+31612345678"))

    def test_vorhandene_sms_beim_ersten_abruf_sind_keine_befehle(self):
        self.assertEqual(self.wache.verarbeite_empfangene_sms([self.sms(5, "ALARM EIN 50")]), [])
        antworten = self.wache.verarbeite_empfangene_sms([self.sms(5, "ALARM EIN 50"),
                                                          self.sms(6, "hilfe")])
        self.assertEqual(antworten, [("+491700000001", ankerwache.SMS_BEFEHLSÜBERSICHT)])
        self.assertEqual(self.wache.verarbeite_empfangene_sms([self.sms(6, "hilfe")]), [])

    def test_eigene_nummer_ohne_ländervorwahl_gilt(self):
        self.wache.verarbeite_empfangene_sms([])
        antworten = self.wache.verarbeite_empfangene_sms([self.sms(1, "HILFE", nummer="01700000001"),
                                                          self.sms(2, "HILFE", nummer="01700000009")])
        self.assertEqual(antworten, [("01700000001", ankerwache.SMS_BEFEHLSÜBERSICHT)])

    def test_erste_sms_bei_leerem_posteingang_gilt(self):
        self.wache.verarbeite_empfangene_sms([])
        self.assertEqual(len(self.wache.verarbeite_empfangene_sms([self.sms(1, "POSITION")])), 1)

    def test_fremde_und_veraltete_sms_ignoriert(self):
        self.wache.verarbeite_empfangene_sms([])
        self.assertEqual(self.wache.verarbeite_empfangene_sms(
            [self.sms(1, "ALARM AUS", nummer="+491799999999"), self.sms(2, "ALARM AUS", alter=600)]), [])

    def test_gelesene_kennung_übersteht_neustart(self):
        self.wache.verarbeite_empfangene_sms([self.sms(7, "POSITION")])
        self.assertEqual(self.neue_wache().verarbeite_empfangene_sms([self.sms(7, "POSITION")]), [])

    def test_sms_antworten_in_ascii(self):
        self.scharf()
        self.wache.batterie = (88, True)
        for befehl in ("POSITION", "HILFE", "anker hoch", "LEBENSZEICHEN wöchentlich", "GENAUIGKEIT 200", "TRACK",
                       "MELDUNGEN BEIDE"):
            self.wache.sende_sms("+491700000001", self.wache.verarbeite_befehl(befehl, KANAL_SMS))
        for _, text in self.ausgehende_sms():
            self.assertTrue(text.isascii(), text)
            self.assertNotIn("<", text)

    def test_sms_status_passt_in_eine_sms(self):
        self.scharf()
        self.wache.batterie = (88, False)
        text = self.wache.verarbeite_befehl("POSITION", KANAL_SMS)
        self.assertEqual(text, "Position: Alarm ein 70m, Abstand 50m, Peilung 0 Grad, Boot 54.321450, 10.123000, "
                               "Genauigkeit 5m, 0.6kn, vor 0 Sekunden, Batterie 88% ohne Ladung")
        self.assertLessEqual(len(self.wache.sms_statusmeldung("Ankerwache gestartet")), 160)

    def test_lebenszeichen_mit_umlaut_und_umschrift(self):
        self.assertEqual(self.wache.verarbeite_befehl("Lebenszeichen Wöchentlich", KANAL_SMS),
                         "Lebenszeichen montags um 09:00 Uhr.")
        self.wache.verarbeite_befehl("LEBENSZEICHEN MONATLICH", KANAL_SMS)
        self.assertEqual(self.neue_wache().zustand["lebenszeichenHäufigkeit"], "monatlich")


class Meldewege(Testfall):
    def test_telegram_wie_bisher(self):
        self.wache.verarbeite_batterie((10, True))
        self.assertEqual(self.telegramtexte(), ["<b>Batterie niedrig:</b> 10 Prozent."])
        self.assertEqual(self.ausgehende_sms(), [])

    def test_nur_sms_an_alle_empfänger(self):
        self.wache.verarbeite_befehl("MELDUNGEN SMS", KANAL_TELEGRAM)
        self.wache.verarbeite_batterie((10, True))
        self.assertEqual(self.telegramtexte(), [])
        self.assertEqual(self.ausgehende_sms(), [("+491700000001", "Batterie niedrig: 10 Prozent."),
                                                 ("+491700000002", "Batterie niedrig: 10 Prozent.")])

    def test_beide_wege(self):
        self.wache.verarbeite_befehl("meldungen beide", KANAL_SMS)
        self.wache.sende_startmeldung()
        self.assertTrue(self.telegramtexte()[0].startswith("<b>Ankerwache gestartet</b>"))
        self.assertTrue(all(text.startswith("Ankerwache gestartet: Alarm aus") for _, text in self.ausgehende_sms()))

    def test_ohne_daten_alles_per_sms(self):
        self.scharf()
        self.assertEqual(self.wache.verarbeite_befehl("DATEN AUS", KANAL_SMS),
                         "Mobile Daten und WLAN aus. Befehle und Meldungen nur per SMS.")
        self.assertFalse(self.gerät.daten_ein)
        self.wache.verarbeite_batterie((10, True))
        for _ in range(3):
            self.messe(90)
        self.assertEqual(self.telegramtexte(), [])
        self.assertEqual([text[:10] for _, text in self.gerät.sms], ["Batterie n"] * 2 + ["ANKERALARM"] * 2)
        self.assertIn("Daten aus", self.wache.sms_statusmeldung("Status"))
        self.assertEqual(self.wache.verarbeite_befehl("DATEN EIN", KANAL_SMS), "Mobile Daten und WLAN ein.")
        self.assertTrue(self.gerät.daten_ein)
        self.assertFalse(self.wache.zustand["datenAus"])

    def test_daten_aus_per_telegram_erst_nach_der_antwort(self):
        self.assertIn("Bestätigung kommt per SMS", self.wache.verarbeite_befehl("DATEN AUS", KANAL_TELEGRAM))
        self.wache.prüfe_daten_aus()
        self.assertTrue(self.gerät.daten_ein)
        self.uhr.zeit += 20
        self.wache.prüfe_daten_aus()
        self.assertFalse(self.gerät.daten_ein)
        self.assertEqual([text for _, text in self.ausgehende_sms()],
                         ["Mobile Daten und WLAN aus. Befehle und Meldungen nur per SMS."] * 2)

    def test_mobile_daten_nicht_schaltbar(self):
        self.gerät.mobile_daten_schaltbar = False
        self.assertIn("Mobile Daten konnten nicht ausgeschaltet werden",
                      self.wache.verarbeite_befehl("DATEN AUS", KANAL_SMS))
        self.assertFalse(self.wache.zustand["datenAus"])


class Empfänger(Testfall):
    def test_hinzufügen_und_entfernen(self):
        self.assertEqual(self.wache.verarbeite_befehl("Empfänger hinzu 0031 6 12345678", KANAL_SMS),
                         "+31612345678 hinzugefügt. SMS-Empfänger: +491700000001, +491700000002, +31612345678.")
        self.assertIn("bereits", self.wache.verarbeite_befehl("EMPFAENGER HINZU +31612345678", KANAL_TELEGRAM))
        self.assertIn("Ländervorwahl", self.wache.verarbeite_befehl("EMPFÄNGER HINZU 0612345678", KANAL_TELEGRAM))
        neu = self.neue_wache()
        self.assertEqual(neu.zustand["smsEmpfänger"][-1], "+31612345678")
        neu.verarbeite_empfangene_sms([])
        sms = {"_id": 1, "number": "+31612345678", "body": "POSITION",
               "received": datetime.fromtimestamp(self.uhr()).strftime("%Y-%m-%d %H:%M:%S")}
        self.assertEqual(len(neu.verarbeite_empfangene_sms([sms])), 1)
        self.assertTrue(neu.verarbeite_befehl("EMPFÄNGER ENTFERNEN +491700000001", KANAL_SMS).startswith(
            "+491700000001 entfernt. SMS-Empfänger: +491700000002, +31612345678."))

    def test_letzter_empfänger_bleibt(self):
        self.wache.verarbeite_befehl("EMPFÄNGER ENTFERNEN +491700000001", KANAL_TELEGRAM)
        self.assertEqual(self.wache.verarbeite_befehl("EMPFÄNGER ENTFERNEN +491700000002", KANAL_TELEGRAM),
                         "Der letzte SMS-Empfänger kann nicht entfernt werden.")
        self.assertIn("ist kein SMS-Empfänger", self.wache.verarbeite_befehl("EMPFÄNGER ENTFERNEN +491769999999", KANAL_TELEGRAM))

    def test_alarm_an_neuen_empfänger(self):
        self.wache.verarbeite_befehl("EMPFÄNGER HINZU +31612345678", KANAL_TELEGRAM)
        self.scharf()
        for _ in range(3):
            self.messe(90)
        self.assertEqual([nummer for nummer, _ in self.gerät.sms], ["+491700000001", "+491700000002", "+31612345678"])


class Gerätestatus(Testfall):
    def test_gerätestatus(self):
        self.wache.sende_sms("+491700000001", "x" * 161)
        self.wache.sende_sms("+491700000001", "kurz")
        self.wache.versende_wartende_sms()
        text = self.wache.verarbeite_befehl("Gerätestatus", KANAL_TELEGRAM)
        for zeile in ("Mobile Daten: ein, Verbindung verbunden", "Mobilfunknetz: KPN, LTE, Roaming",
                      "WLAN: verbunden mit Hafen", "Mobile Daten schaltbar: über ADB", "Telegram: seit dem Start kein Kontakt",
                      "SMS-Empfänger: +491700000001, +491700000002", "SMS gesendet im Monat ", ": 3",
                      "Batterie: 77 Prozent, wird geladen, 31 Grad Celsius", "GPS: letzte gültige Position keine",
                      "SMS: möglich\n"):
            self.assertIn(zeile, text)

    def test_gerätestatus_per_sms_höchstens_zwei_sms(self):
        self.scharf()
        self.wache.batterie = (77, True)
        self.gerät.sim_state = "absent"
        self.wache.sende_sms("+491700000001", "kurz")
        text = sms_fassung(self.wache.verarbeite_befehl("status", KANAL_SMS))
        self.assertLessEqual(sms_anzahl(text), 2)
        self.assertTrue(text.isascii())
        for teil in ("Daten ein verbunden (KPN, LTE, Roaming)", "WLAN Hafen", "schaltbar ADB",
                     "an +491700000001 +491700000002 (1 wartend)", "Alarm ein 70m"):
            self.assertIn(teil, text)

    def test_sms_anzahl_zählt_erweiterungszeichen_doppelt(self):
        self.assertEqual(sms_anzahl("x" * 160), 1)
        self.assertEqual(sms_anzahl("|" + "x" * 159), 2)

    def test_sms_zähler_beginnt_jeden_monat_neu(self):
        self.wache.sende_sms("+491700000001", "kurz")
        self.wache.versende_wartende_sms()
        self.uhr.zeit += 40 * 86400
        self.wache.sende_sms("+491700000001", "kurz")
        self.wache.versende_wartende_sms()
        self.assertEqual(self.wache.zustand["smsImMonat"], 1)


class SmsAusgang(Testfall):
    def test_ohne_sim_karte_später_genau_einmal(self):
        self.gerät.sim_state = "absent"
        self.wache.sende_sms("+491700000001", "Test")
        self.assertFalse(self.wache.versende_wartende_sms())
        self.assertEqual(self.gerät.sms, [])
        self.assertEqual(self.wache.zustand["smsImMonat"], 0)
        self.gerät.sim_state = "ready"
        self.assertTrue(self.wache.versende_wartende_sms())
        self.assertTrue(self.wache.versende_wartende_sms())
        self.assertEqual(self.gerät.sms, [("+491700000001", "Test")])
        self.assertEqual(self.wache.zustand["smsImMonat"], 1)

    def test_ohne_netz_zurückgestellt(self):
        self.gerät.netzbetreiber = ""
        self.wache.sende_sms("+491700000001", "Test")
        self.assertFalse(self.wache.versende_wartende_sms())
        self.assertIn("SMS: nicht möglich (kein Mobilfunknetz), 1 wartend",
                      self.wache.verarbeite_befehl("STATUS", KANAL_TELEGRAM))
        self.gerät.netzbetreiber = "KPN"
        self.wache.versende_wartende_sms()
        self.assertEqual(len(self.gerät.sms), 1)

    def test_fehler_beim_senden_später_genau_einmal(self):
        self.gerät.sms_fehler = [TimeoutError()]
        self.wache.sende_sms("+491700000001", "Test")
        self.assertFalse(self.wache.versende_wartende_sms())
        self.assertTrue(self.wache.versende_wartende_sms())
        self.assertEqual(self.gerät.sms, [("+491700000001", "Test")])

    def test_wartende_sms_überstehen_neustart(self):
        self.gerät.sim_state = "absent"
        self.wache.sende_sms("+491700000001", "Test")
        self.wache.versende_wartende_sms()
        neu = self.neue_wache()
        self.assertTrue(neu._sms_wartet.is_set())
        self.gerät.sim_state = "ready"
        neu.versende_wartende_sms()
        self.assertEqual(self.gerät.sms, [("+491700000001", "Test")])
        self.assertEqual(self.neue_wache().zustand["smsAusgang"], [])

    def test_alarm_ohne_sim_karte_wird_nachgesendet(self):
        self.scharf()
        self.gerät.sim_state = "absent"
        for _ in range(3):
            self.messe(90)
        self.assertEqual(self.gerät.sms, [])
        self.gerät.sim_state = "ready"
        self.messe(90)
        self.messe(90)
        self.assertEqual([nummer for nummer, _ in self.gerät.sms], ["+491700000001", "+491700000002"])
        self.assertTrue(all(text.startswith("ANKERALARM 54.321") for _, text in self.gerät.sms))

    def test_reihenfolge_bleibt_erhalten(self):
        self.gerät.sim_state = "absent"
        for text in ("eins", "zwei", "drei"):
            self.wache.sende_sms("+491700000001", text)
        self.wache.versende_wartende_sms()
        self.gerät.sim_state = "ready"
        self.wache.versende_wartende_sms()
        self.assertEqual([text for _, text in self.gerät.sms], ["eins", "zwei", "drei"])

    def test_verspätete_sms_nennt_entstehungszeitpunkt(self):
        self.gerät.sim_state = "absent"
        self.wache.sende_sms("+491700000001", "frueh")
        erstellt = datetime.fromtimestamp(self.uhr()).strftime("%d.%m. %H:%M")
        self.uhr.zeit += 299
        self.wache.sende_sms("+491700000001", "spaet")
        self.uhr.zeit += 1
        self.gerät.sim_state = "ready"
        self.wache.versende_wartende_sms()
        self.assertEqual([text for _, text in self.gerät.sms], [f"Verspaetet, erstellt {erstellt}: frueh", "spaet"])

    def test_neues_scharfmachen_verwirft_wartende_sms(self):
        self.gerät.sim_state = "absent"
        for befehl in ("ALARM AUS", f"SETZE ANKER {ANKER_BREITE}, {ANKER_LÄNGE}", "ALARM EIN 70"):
            self.wache.sende_sms("+491700000001", "alt")
            self.wache.verarbeite_befehl(befehl, KANAL_TELEGRAM)
            self.assertEqual(self.wache.zustand["smsAusgang"], [], befehl)


class Lebenszeichen(Testfall):
    def gesendet_an(self, *tage):
        ergebnis = []
        for tag in tage:
            self.wache.prüfe_lebenszeichen(datetime(2026, 10, tag, 9, 0))
            ergebnis.append(bool(self.telegramtexte()))
        return ergebnis

    def test_täglich(self):
        self.assertEqual(self.gesendet_an(1, 1, 2), [True, False, True])

    def test_wöchentlich_montags(self):
        self.wache.verarbeite_befehl("LEBENSZEICHEN WOECHENTLICH", KANAL_TELEGRAM)
        self.assertEqual(self.gesendet_an(4, 5, 6, 12), [False, True, False, True])

    def test_monatlich_am_ersten(self):
        self.wache.verarbeite_befehl("LEBENSZEICHEN MONATLICH", KANAL_TELEGRAM)
        self.assertEqual(self.gesendet_an(1, 2, 5), [True, False, False])

    def test_nicht_vor_der_uhrzeit(self):
        self.wache.prüfe_lebenszeichen(datetime(2026, 10, 1, 8, 59))
        self.assertEqual(self.telegramtexte(), [])


class WeitesterAbstand(Testfall):
    def test_weitester_gültiger_abstand_seit_anker(self):
        self.messe(300)
        self.uhr.zeit += 60
        self.scharf()
        self.messe(80, genauigkeit=30)
        self.messe(65, genauigkeit=7)
        self.messe(55)
        text = self.wache.verarbeite_befehl("TRACK", KANAL_SMS)
        self.assertTrue(text.startswith("Weitester Abstand vom Anker (7 Tage, seit Anker gesetzt): "
                                        "65 Meter bei Genauigkeit 7 Meter"), text)
        self.assertEqual(self.telegram.dateien, [])

    def test_sieben_tage_über_mehrere_trackdateien(self):
        self.scharf()
        self.messe(90)
        for _ in range(6):
            self.uhr.zeit += 86400
            self.messe(60)
        self.assertIn("90 Meter", self.wache.verarbeite_befehl("TRACK", KANAL_SMS))
        self.uhr.zeit += 86400
        self.assertIn("60 Meter", self.wache.verarbeite_befehl("TRACK", KANAL_SMS))

    def test_ohne_anker(self):
        self.assertEqual(self.wache.verarbeite_befehl("TRACK", KANAL_SMS), "Kein Anker gesetzt.")


class SmsVersand(unittest.TestCase):
    """termux-sms-send kehrt vor der Übertragung zurück; gewartet wird auf den Eintrag unter "Gesendet"."""

    def setUp(self):
        self.zeit = datetime(2026, 10, 2, 12, 0, 0).timestamp()
        self.gesendet = []
        self.befehle = []
        self.gerät = ankerwache.TermuxGerät()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.gerät, "_aufruf", side_effect=self.aufruf).start()
        mock.patch.object(ankerwache.time, "time", side_effect=lambda: self.zeit).start()
        mock.patch.object(ankerwache.time, "sleep", side_effect=self.schlafe).start()

    def schlafe(self, sekunden):
        self.zeit += sekunden

    def aufruf(self, befehl, zeitlimit):
        self.befehle.append(befehl[0])
        return json.dumps(self.gesendet) if befehl[0] == "termux-sms-list" else ""

    def eintrag(self, nummer, sekunden_später):
        empfangen = datetime.fromtimestamp(self.zeit + sekunden_später).strftime("%Y-%m-%d %H:%M:%S")
        return {"number": nummer, "received": empfangen}

    def test_wartet_bis_die_sms_gesendet_ist(self):
        def schlafe(sekunden):
            self.zeit += sekunden
            self.gesendet = [self.eintrag("+491700000004", 0)]
        ankerwache.time.sleep.side_effect = schlafe
        self.gerät.sende_sms("+491700000004", "Test")
        self.assertEqual(self.befehle, ["termux-sms-send", "termux-sms-list", "termux-sms-list"])

    def test_ältere_sms_an_dieselbe_nummer_gilt_nicht_als_bestätigung(self):
        self.gesendet = [self.eintrag("+491700000004", -60)]
        with self.assertLogs(ankerwache.protokoll, "WARNING"):
            self.gerät.sende_sms("+491700000004", "Test")
        self.assertEqual(self.befehle.count("termux-sms-list"), ankerwache.SMS_SENDEBESTÄTIGUNG_HÖCHSTDAUER_SEKUNDEN)

    def test_sms_an_andere_nummer_gilt_nicht_als_bestätigung(self):
        self.gesendet = [self.eintrag("+491700000003", 0)]
        with self.assertLogs(ankerwache.protokoll, "WARNING"):
            self.gerät.sende_sms("+491700000004", "Test")


class TermuxPosition(unittest.TestCase):
    """Standort ohne aktuelle GPSLogger-Datei; Antworten von termux-location je Abfrageart."""

    def setUp(self):
        self.zeit = 1000.0
        self.antworten = {"last": "{}", "once": "{}"}
        self.abfragen = []
        self.gerät = ankerwache.TermuxGerät()
        self.gerät._gpslogger_läuft = False
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ankerwache, "GPSLOGGER_ORDNER", ankerwache.Path(ordner.name)).start()
        mock.patch.object(self.gerät, "_aufruf", side_effect=self.aufruf).start()
        mock.patch.object(ankerwache.time, "time", side_effect=lambda: self.zeit).start()
        self.thread = mock.patch.object(ankerwache.threading, "Thread").start()

    def aufruf(self, befehl, zeitlimit):
        self.abfragen.append(befehl[-1])
        if isinstance(antwort := self.antworten[befehl[-1]], Exception):
            raise antwort
        return antwort

    @staticmethod
    def fix(alter_sekunden):
        return json.dumps({"latitude": 51.5, "longitude": 4.25, "accuracy": 4.0, "speed": 0.0,
                           "elapsedMs": alter_sekunden * 1000})

    def test_junge_letzte_messung_ohne_einzelabruf(self):
        self.antworten["last"] = self.fix(3)
        position = self.gerät.position()
        self.assertEqual(position.zeitpunkt, 997)
        self.assertEqual(self.abfragen, ["last"])
        self.thread.assert_not_called()

    def test_alte_letzte_messung_führt_zum_einzelabruf(self):
        self.antworten.update(last=self.fix(400), once=self.fix(0))
        self.assertEqual(self.gerät.position().zeitpunkt, 1000)
        self.assertEqual(self.abfragen, ["last", "once"])

    def test_kein_einzelabruf_solange_gpslogger_läuft(self):
        self.gerät._gpslogger_läuft = True
        self.assertIsNone(self.gerät.position())
        self.assertEqual(self.abfragen, ["last"])

    def test_gpslogger_prüfung_setzt_zustand(self):
        with mock.patch.object(ankerwache.TermuxGerät, "_starte_gpslogger_neu", return_value=True), \
                mock.patch.object(ankerwache.TermuxGerät, "_gps_hängt", return_value=False):
            self.gerät._prüfe_gpslogger()
        self.assertTrue(self.gerät._gpslogger_läuft)

    def test_nach_fehlgeschlagenem_einzelabruf_zehn_minuten_pause(self):
        self.gerät.position()
        self.zeit += 599
        self.assertIsNone(self.gerät.position())
        self.assertEqual(self.abfragen, ["last", "once", "last"])
        self.zeit += 1
        self.gerät.position()
        self.assertEqual(self.abfragen[-1], "once")

    def test_nach_zeitüberschreitung_zehn_minuten_keine_standortabfrage(self):
        self.antworten["last"] = ankerwache.subprocess.TimeoutExpired("termux-location", 30)
        self.gerät.position()
        self.zeit += 599
        self.gerät.position()
        self.assertEqual(self.abfragen, ["last"])
        self.zeit += 1
        self.gerät.position()
        self.assertEqual(self.abfragen, ["last", "last"])

    def test_gpslogger_prüfung_höchstens_alle_zehn_minuten(self):
        for sekunden in (0, 300, 300):
            self.zeit += sekunden
            self.gerät.position()
        self.assertEqual(self.thread.call_count, 2)


class GpsloggerNeustart(unittest.TestCase):
    def test_startet_nur_nicht_laufenden_dienst_über_adb(self):
        ergebnis = mock.Mock(returncode=0, stdout="Starting service: Intent", stderr="")
        with mock.patch.object(ankerwache.subprocess, "run", return_value=ergebnis) as ausführen:
            self.assertTrue(ankerwache.TermuxGerät._starte_gpslogger_neu())
        befehl = ausführen.call_args.args[0]
        self.assertEqual(befehl[:4], ["adb", "-s", ankerwache.ADB_ZIEL, "shell"])
        self.assertIn("dumpsys activity services com.mendhak.gpslogger", befehl[4])
        self.assertIn("|| am start-foreground-service -n com.mendhak.gpslogger/.GpsLoggingService", befehl[4])

    def test_laufender_dienst_wird_nicht_als_neustart_protokolliert(self):
        ergebnis = mock.Mock(returncode=0, stdout="laeuft\n", stderr="")
        with mock.patch.object(ankerwache.subprocess, "run", return_value=ergebnis), \
                self.assertNoLogs(ankerwache.protokoll):
            self.assertTrue(ankerwache.TermuxGerät._starte_gpslogger_neu())

    def test_ohne_adb_läuft_gpslogger_nicht(self):
        with mock.patch.object(ankerwache.subprocess, "run", side_effect=OSError("adb fehlt")):
            self.assertFalse(ankerwache.TermuxGerät._starte_gpslogger_neu())


class GpsHänger(unittest.TestCase):
    """Erkennung und Behebung eines im Systemprozess hängenden GPS."""

    @staticmethod
    def stat(takte):
        return f"4288 (HwBinder:3652_5) S 3291 3291 0 0 -1 1077952576 2808 661 0 0 {takte} 0 0 1 18 -2 210\n"

    def hängt(self, takte_vorher, takte_nachher):
        self.addCleanup(mock.patch.stopall)
        ausgaben = iter([self.stat(takte_vorher), self.stat(takte_nachher)])
        mock.patch.object(ankerwache.TermuxGerät, "_adb",
                          side_effect=lambda argumente, zeitlimit: mock.Mock(stdout=next(ausgaben))).start()
        mock.patch.object(ankerwache.time, "sleep").start()
        mock.patch.object(ankerwache.time, "monotonic", side_effect=[0, 10]).start()
        return ankerwache.TermuxGerät._gps_hängt()

    def test_volllast_gilt_als_hänger(self):
        self.assertTrue(self.hängt(1000, 1900))

    def test_normalbetrieb_gilt_nicht_als_hänger(self):
        self.assertFalse(self.hängt(1000, 1010))

    def test_ohne_adb_kein_hänger(self):
        with mock.patch.object(ankerwache.subprocess, "run", side_effect=OSError("adb fehlt")):
            self.assertFalse(ankerwache.TermuxGerät._gps_hängt())

    def behebe(self, hängt_danach):
        self.addCleanup(mock.patch.stopall)
        adb = mock.patch.object(ankerwache.TermuxGerät, "_adb").start()
        mock.patch.object(ankerwache.TermuxGerät, "_gps_hängt", return_value=hängt_danach).start()
        ankerwache.TermuxGerät._behebe_gps_hänger()
        return [aufruf.args[0] for aufruf in adb.call_args_list]

    def test_gps_wird_aus_und_eingeschaltet_und_gpslogger_neu_gestartet(self):
        aufrufe = self.behebe(hängt_danach=False)
        self.assertEqual(len(aufrufe), 1)
        befehl = aufrufe[0][1]
        for teil in ("location_providers_allowed -gps", "location_providers_allowed +gps",
                     "am force-stop com.mendhak.gpslogger", "am start-foreground-service"):
            self.assertIn(teil, befehl)

    def test_hält_der_hänger_an_kein_neustart_des_handys(self):
        self.assertNotIn(["reboot"], self.behebe(hängt_danach=True))

    def test_prüfung_nur_bei_laufendem_gpslogger(self):
        gerät = ankerwache.TermuxGerät()
        with mock.patch.object(ankerwache.TermuxGerät, "_starte_gpslogger_neu", return_value=False), \
                mock.patch.object(ankerwache.TermuxGerät, "_gps_hängt") as prüfung:
            gerät._prüfe_gpslogger()
        prüfung.assert_not_called()


class Fernzugriff(Testfall):
    def test_aus_und_ein(self):
        self.assertIn("deaktiviert", self.wache.verarbeite_befehl("FERNZUGRIFF AUS", KANAL_SMS))
        self.assertFalse(self.gerät.fernzugriff_ein)
        self.assertIn("laufen", self.wache.verarbeite_befehl("FERNZUGRIFF EIN", KANAL_TELEGRAM))
        self.assertTrue(self.gerät.fernzugriff_ein)

    def test_fehlschlag_wird_gemeldet(self):
        self.gerät.mobile_daten_schaltbar = False
        self.assertIn("konnte nicht aus", self.wache.verarbeite_befehl("FERNZUGRIFF AUS", KANAL_SMS))

    def test_beim_start_aus(self):
        self.wache.schalte_fernzugriff_beim_start_aus()
        self.assertFalse(self.gerät.fernzugriff_ein)

    def test_ein_bleibt_über_neustart(self):
        self.wache.verarbeite_befehl("FERNZUGRIFF EIN", KANAL_SMS)
        del self.gerät.fernzugriff_ein
        self.neue_wache().schalte_fernzugriff_beim_start_aus()
        self.assertFalse(hasattr(self.gerät, "fernzugriff_ein"))


class FernzugriffAufDemGerät(unittest.TestCase):
    ANSICHT = ('<hierarchy><node index="0" text="" content-desc="Bildschirm freigeben&#10;Tab 3 von 4" '
               'checkable="false" bounds="[360,1168][540,1280]" /></hierarchy>')

    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.befehle = []
        self.dienst_läuft = False
        mock.patch.object(ankerwache.time, "sleep").start()
        mock.patch.object(ankerwache.TermuxGerät, "_adb", side_effect=self.adb).start()

    def adb(self, argumente, zeitlimit):
        befehl = " ".join(argumente[1:])
        self.befehle.append(befehl)
        if befehl.startswith("input tap"):
            self.dienst_läuft = True
        ausgabe = {"uiautomator": self.ANSICHT, "dumpsys": "MainService" if self.dienst_läuft else ""}
        return mock.Mock(returncode=0, stdout=ausgabe.get(befehl.split()[0], ""))

    def test_aus_deaktiviert_beide_apps(self):
        self.assertTrue(ankerwache.TermuxGerät().schalte_fernzugriff(False))
        self.assertEqual(self.befehle, [f"pm disable-user --user 0 {paket}" for paket in ankerwache.FERNZUGRIFF_PAKETE])

    def test_ein_startet_tailscale_und_tippt_rustdesk_an(self):
        self.assertTrue(ankerwache.TermuxGerät().schalte_fernzugriff(True))
        self.assertIn("pm enable com.tailscale.ipn", self.befehle)
        self.assertTrue(any(befehl.startswith("monkey -p com.tailscale.ipn") for befehl in self.befehle))
        self.assertIn("input tap 450 1224", self.befehle)
        self.assertEqual(self.befehle[-1], "input keyevent KEYCODE_HOME")

    def test_ohne_adb_fehlschlag(self):
        with mock.patch.object(ankerwache.TermuxGerät, "_adb", side_effect=OSError("adb fehlt")):
            self.assertFalse(ankerwache.TermuxGerät().schalte_fernzugriff(True))


if __name__ == "__main__":
    unittest.main()
