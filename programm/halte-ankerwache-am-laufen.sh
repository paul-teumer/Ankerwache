#!/data/data/com.termux/files/usr/bin/sh
# Android startet dieses Skript alle 15 Minuten. Läuft die Ankerwache bereits, beendet sich der neue Start sofort.
# Stellt Wakelock und Termux:API wieder her, falls Android die Termux-Apps beendet hat.
termux-wake-lock
termux-api-start >/dev/null 2>&1
command -v sshd >/dev/null && ! pgrep -x sshd >/dev/null && sshd
# Wartet das Startskript gerade nach einem Absturz, darf kein zweites die Pause umgehen.
pgrep -f start-ankerwache.sh >/dev/null || nohup sh "$HOME/.termux/boot/start-ankerwache.sh" >/dev/null 2>&1 &
