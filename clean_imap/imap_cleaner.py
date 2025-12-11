#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import imaplib
import email
import json
import time
import socket
import sys
import os
import re

from email.header import decode_header
from email.message import Message

import paho.mqtt.client as mqtt

OPTIONS_PATH = "/data/options.json"
LAST_UID_PATH = "/data/last_uid.txt"


# -----------------------------
# Helper: opties inladen
# -----------------------------
def load_options():
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"IMAP Cleaner FATAAL: Kan opties niet lezen uit {OPTIONS_PATH}: {e}", flush=True)
        sys.exit(1)


# -----------------------------
# Helper: poort-validatie
# -----------------------------
def validate_port(port, name):
    if not isinstance(port, int):
        try:
            port = int(str(port))
        except Exception:
            print(f"IMAP Cleaner FATAAL: {name} poort is geen geldig getal: {port}", flush=True)
            sys.exit(1)

    if port < 1 or port > 65535:
        print(f"IMAP Cleaner FATAAL: Ongeldige {name} poort: {port} (moet tussen 1 en 65535 zijn)", flush=True)
        sys.exit(1)

    return port


# -----------------------------
# Helper: hostname-validatie
# -----------------------------
def validate_hostname(host, name):
    if not host:
        print(f"IMAP Cleaner FATAAL: {name} hostnaam is leeg of niet ingesteld.", flush=True)
        sys.exit(1)

    try:
        socket.gethostbyname(host)
    except Exception as e:
        print(f"IMAP Cleaner FATAAL: {name} host '{host}' kan niet worden geresolvd: {e}", flush=True)
        sys.exit(1)


# -----------------------------
# Helper: laatste verwerkte UID
# -----------------------------
def load_last_uid():
    if not os.path.exists(LAST_UID_PATH):
        return 0
    try:
        with open(LAST_UID_PATH, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def save_last_uid(uid):
    try:
        with open(LAST_UID_PATH, "w", encoding="utf-8") as f:
            f.write(str(uid))
    except Exception as e:
        print(f"IMAP Cleaner WAARSCHUWING: Kon laatste UID niet opslaan: {e}", flush=True)


# -----------------------------
# Helper: header decoderen
# -----------------------------
def decode_mime_header(value):
    if not value:
        return ""
    parts = decode_header(value)
    decoded_parts = []
    for text, enc in parts:
        try:
            if isinstance(text, bytes):
                decoded_parts.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                decoded_parts.append(text)
        except Exception:
            if isinstance(text, bytes):
                decoded_parts.append(text.decode("utf-8", errors="replace"))
            else:
                decoded_parts.append(str(text))
    return "".join(decoded_parts)


# -----------------------------
# Helper: HTML naar tekst
# -----------------------------
TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html):
    if not html:
        return ""
    # Tags strippen
    text = TAG_RE.sub("", html)
    # HTML entities simpel vervangen
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    # Meerdere spaties/regels normaliseren
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# -----------------------------
# Helper: body uit e-mail halen
# -----------------------------
def extract_text_from_message(msg: Message) -> str:
    # 1. Probeer eerst text/plain
    if msg.is_multipart():
        plain_candidates = []
        html_candidates = []
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "").lower()

            # Bijlagen overslaan
            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None

            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                plain_candidates.append(text)
            elif content_type == "text/html":
                html_candidates.append(text)

        if plain_candidates:
            return "\n\n".join(plain_candidates).strip()
        if html_candidates:
            combined_html = "\n\n".join(html_candidates)
            return html_to_text(combined_html)

    # Geen multipart
    try:
        payload = msg.get_payload(decode=True)
    except Exception:
        payload = None

    if payload:
        charset = msg.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except Exception:
            text = payload.decode("utf-8", errors="replace")

        if msg.get_content_type() == "text/html":
            return html_to_text(text)
        return text.strip()

    return ""


# -----------------------------
# MQTT client
# -----------------------------
def build_mqtt_client(mqtt_host, mqtt_port, mqtt_user, mqtt_pass):
    client = mqtt.Client()

    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_pass or "")

    def on_connect(cl, userdata, flags, rc):
        if rc == 0:
            print("IMAP Cleaner: Verbonden met MQTT broker.", flush=True)
        else:
            print(f"IMAP Cleaner FOUT: Verbinding met MQTT broker mislukt (rc={rc})", flush=True)

    client.on_connect = on_connect

    try:
        client.connect(mqtt_host, mqtt_port, 60)
    except Exception as e:
        print(f"IMAP Cleaner FATAAL: Kan geen verbinding maken met MQTT broker {mqtt_host}:{mqtt_port} - {e}", flush=True)
        sys.exit(1)

    client.loop_start()
    return client


# -----------------------------
# 1 cyclus: mails ophalen & publiceren
# -----------------------------
def process_imap_cycle(
    imap_host,
    imap_port,
    imap_user,
    imap_pass,
    mqtt_client,
    mqtt_topic,
    mark_as_read,
):
    last_uid = load_last_uid()

    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port)
    except Exception as e:
        print(f"IMAP Cleaner FOUT: Kan geen SSL IMAP-verbinding maken met {imap_host}:{imap_port} - {e}", flush=True)
        return

    try:
        imap.login(imap_user, imap_pass)
    except Exception as e:
        print(f"IMAP Cleaner FOUT: IMAP login mislukt voor gebruiker '{imap_user}': {e}", flush=True)
        try:
            imap.logout()
        except Exception:
            pass
        return

    try:
        status, _ = imap.select("INBOX")
        if status != "OK":
            print("IMAP Cleaner FOUT: Kan INBOX niet selecteren.", flush=True)
            imap.logout()
            return

        # Zoek mails met UID groter dan last_uid
        if last_uid > 0:
            criteria = f"(UID {last_uid + 1}:*)"
        else:
            # Eerste keer: alleen UNSEEN
            criteria = "(UNSEEN)"

        status, data = imap.uid("SEARCH", None, criteria)
        if status != "OK":
            print(f"IMAP Cleaner FOUT: IMAP zoekopdracht mislukt ({criteria}).", flush=True)
            imap.logout()
            return

        uids = data[0].split()
        if not uids or uids == [b""]:
            # Geen nieuwe mails
            imap.logout()
            return

        print(f"IMAP Cleaner: {len(uids)} nieuwe e-mail(s) gevonden.", flush=True)

        max_uid = last_uid

        for raw_uid in uids:
            try:
                uid_int = int(raw_uid)
            except ValueError:
                continue

            status, msg_data = imap.uid("FETCH", raw_uid, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                print(f"IMAP Cleaner WAARSCHUWING: Kon bericht met UID {raw_uid} niet ophalen.", flush=True)
                continue

            raw_email = msg_data[0][1]
            try:
                msg = email.message_from_bytes(raw_email)
            except Exception as e:
                print(f"IMAP Cleaner WAARSCHUWING: Kon e-mail niet parsen (UID {uid_int}): {e}", flush=True)
                continue

            subject = decode_mime_header(msg.get("Subject", ""))
            from_raw = msg.get("From", "")
            from_decoded = decode_mime_header(from_raw)

            text_body = extract_text_from_message(msg)

            payload = {
                "onderwerp": subject,
                "afzender": from_decoded,
                "tekst": text_body,
            }

            try:
                mqtt_client.publish(mqtt_topic, json.dumps(payload), qos=0, retain=False)
                print(f"IMAP Cleaner: E-mail (UID {uid_int}) gepubliceerd op MQTT-topic '{mqtt_topic}'.", flush=True)
            except Exception as e:
                print(f"IMAP Cleaner FOUT: Publiceren naar MQTT mislukt voor UID {uid_int}: {e}", flush=True)

            if mark_as_read:
                try:
                    imap.uid("STORE", raw_uid, "+FLAGS", "(\\Seen)")
                except Exception as e:
                    print(f"IMAP Cleaner WAARSCHUWING: Kon e-mail (UID {uid_int}) niet als gelezen markeren: {e}", flush=True)

            if uid_int > max_uid:
                max_uid = uid_int

        if max_uid > last_uid:
            save_last_uid(max_uid)

    except Exception as e:
        print(f"IMAP Cleaner FOUT: Onverwachte fout tijdens IMAP-verwerking: {e}", flush=True)
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass


# -----------------------------
# main()
# -----------------------------
def main():
    print("IMAP Cleaner: Start...", flush=True)

    options = load_options()

    imap_host = options.get("imap_host", "").strip()
    imap_port = options.get("imap_port", 993)
    imap_user = options.get("imap_username", "").strip()
    imap_pass = options.get("imap_password", "")

    mqtt_host = options.get("mqtt_host", "core-mosquitto").strip()
    mqtt_port = options.get("mqtt_port", 1883)
    mqtt_user = options.get("mqtt_username", "").strip()
    mqtt_pass = options.get("mqtt_password", "")

    mqtt_topic = options.get("mqtt_topic", "mail/decoded").strip()
    mark_as_read = bool(options.get("mark_as_read", True))
    poll_interval = int(options.get("poll_interval", 60))

    # Validaties
    validate_hostname(imap_host, "IMAP")
    validate_hostname(mqtt_host, "MQTT")

    imap_port = validate_port(imap_port, "IMAP")
    mqtt_port = validate_port(mqtt_port, "MQTT")

    if not imap_user:
        print("IMAP Cleaner FATAAL: IMAP gebruikersnaam is niet ingesteld.", flush=True)
        sys.exit(1)

    if not mqtt_topic:
        print("IMAP Cleaner FATAAL: MQTT topic is niet ingesteld.", flush=True)
        sys.exit(1)

    # Wachtwoord-waarschuwingen
    if not imap_pass:
        print("IMAP Cleaner WAARSCHUWING: IMAP wachtwoord ontbreekt!", flush=True)

    if mqtt_user and not mqtt_pass:
        print(
            "IMAP Cleaner WAARSCHUWING: MQTT wachtwoord ontbreekt terwijl er wel een gebruikersnaam is ingesteld!",
            flush=True,
        )

    mqtt_client = build_mqtt_client(mqtt_host, mqtt_port, mqtt_user, mqtt_pass)

    print(
        f"IMAP Cleaner: gestart. Poll-interval: {poll_interval} seconden. "
        f"IMAP server: {imap_host}:{imap_port}, MQTT broker: {mqtt_host}:{mqtt_port}, topic: {mqtt_topic}",
        flush=True,
    )

    while True:
        process_imap_cycle(
            imap_host=imap_host,
            imap_port=imap_port,
            imap_user=imap_user,
            imap_pass=imap_pass,
            mqtt_client=mqtt_client,
            mqtt_topic=mqtt_topic,
            mark_as_read=mark_as_read,
        )

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
