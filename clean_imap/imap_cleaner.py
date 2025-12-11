#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import imaplib
import email
import json
import os
import sys
import time
import logging
import re

import paho.mqtt.client as mqtt

# -----------------------------------------------------------
# Logging configuratie
# -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="IMAP Cleaner: %(message)s",
    stream=sys.stdout,
)

LOG = logging.getLogger("imap_cleaner")


# -----------------------------------------------------------
# Config laden + basisvalidatie
# -----------------------------------------------------------
def load_config():
    options_path = "/data/options.json"
    if not os.path.exists(options_path):
        LOG.error("Configuratiebestand %s niet gevonden.", options_path)
        sys.exit(1)

    try:
        with open(options_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        LOG.error("Kon configuratie niet lezen (%s): %s", options_path, e)
        sys.exit(1)

    return cfg


def validate_hostname(name, value):
    if not isinstance(value, str) or not value.strip():
        LOG.error("Ongeldige hostnaam voor %s: '%s'", name, value)
        sys.exit(1)


def validate_port(name, value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        LOG.error("Poort voor %s is geen geldig getal: '%s'", name, value)
        sys.exit(1)

    if port < 1 or port > 65535:
        LOG.error("Poort voor %s moet tussen 1 en 65535 liggen (nu: %s).", name, port)
        sys.exit(1)

    return port


# -----------------------------------------------------------
# E-mail hulpfuncties
# -----------------------------------------------------------
def decode_header_value(raw):
    """Decodeer MIME header (subject, from, …) naar nette unicode."""
    from email.header import decode_header

    if not raw:
        return ""

    parts = decode_header(raw)
    decoded = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                decoded += text.decode(enc or "utf-8", errors="replace")
            except Exception:
                decoded += text.decode("utf-8", errors="replace")
        else:
            decoded += text

    return decoded


def extract_text_from_message(msg):
    """
    Haal de tekstuele inhoud uit de e-mail (prefereer text/plain).
    Geeft een unicode string terug.
    """
    if msg.is_multipart():
        # Eerst zoeken naar text/plain
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", "")).lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")

        # Fallback: eerste text/html naar platte tekst strippen
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", "")).lower()
            if ctype == "text/html" and "attachment" not in disp:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    html = part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    html = part.get_payload(decode=True).decode("utf-8", errors="replace")

                # heel simpele HTML-strip
                text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text)
                return text.strip()
    else:
        # Enkelvoudige e-mail
        ctype = msg.get_content_type()
        try:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload is None:
                return ""
            body = payload.decode(charset, errors="replace")
        except Exception:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

        if ctype == "text/html":
            body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body)
        return body.strip()

    return ""


# -----------------------------------------------------------
# MQTT callbacks (Callback API v2)
# -----------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        LOG.info("Verbonden met MQTT broker.")
    else:
        LOG.error("Verbinding met MQTT broker mislukt (code %s).", rc)


def on_disconnect(client, userdata, rc, properties=None):
    # rc kan 0 (normaal) of anders zijn
    LOG.info("Verbinding met MQTT broker verbroken (code %s).", rc)


# -----------------------------------------------------------
# Hoofdlogica
# -----------------------------------------------------------
def main():
    cfg = load_config()

    # Config uit options.json
    imap_host = cfg.get("imap_server") or cfg.get("imap_host") or ""
    imap_port = cfg.get("imap_port", 993)
    imap_user = cfg.get("imap_user", "")
    imap_pass = cfg.get("imap_password", "")
    imap_mailbox = cfg.get("imap_mailbox", "INBOX")

    mqtt_host = cfg.get("mqtt_host", "")
    mqtt_port = cfg.get("mqtt_port", 1883)
    mqtt_user = cfg.get("mqtt_username", "")
    mqtt_pass = cfg.get("mqtt_password", "")
    mqtt_topic = cfg.get("mqtt_topic", "mail/decoded")

    poll_interval = int(cfg.get("poll_interval", 60))

    # Validatie host/poort
    validate_hostname("IMAP", imap_host)
    imap_port = validate_port("IMAP", imap_port)

    validate_hostname("MQTT", mqtt_host)
    mqtt_port = validate_port("MQTT", mqtt_port)

    # Wachtwoord-checks
    if not imap_pass:
        LOG.error(
            "Er is geen IMAP-wachtwoord opgegeven in de configuratie. "
            "Controleer de add-on instellingen."
        )
        sys.exit(1)

    if mqtt_user and not mqtt_pass:
        LOG.warning(
            "MQTT-gebruikersnaam is ingevuld, maar er ontbreekt een MQTT-wachtwoord "
            "in de configuratie."
        )

    # IMAP verbinden
    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port)
        imap.login(imap_user, imap_pass)
    except Exception as e:
        LOG.error("Kon niet verbinden met IMAP (%s:%s): %s", imap_host, imap_port, e)
        sys.exit(1)

    # MQTT client (Callback API v2)
    try:
        client = mqtt.Client(
            client_id="imap_cleaner",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
    except TypeError:
        # Fallback voor heel oude paho-mqtt (zou je eigenlijk niet meer moeten hebben)
        client = mqtt.Client(client_id="imap_cleaner", protocol=mqtt.MQTTv311)

    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_pass or None)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    try:
        client.connect(mqtt_host, mqtt_port, keepalive=60)
    except Exception as e:
        LOG.error("Kon niet verbinden met MQTT broker (%s:%s): %s", mqtt_host, mqtt_port, e)
        sys.exit(1)

    client.loop_start()

    LOG.info(
        "gestart. Poll-interval: %s seconden. IMAP server: %s:%s, MQTT broker: %s:%s, topic: %s",
        poll_interval,
        imap_host,
        imap_port,
        mqtt_host,
        mqtt_port,
        mqtt_topic,
    )

    last_uid = None

    while True:
        try:
            # Selecteer mailbox
            status, _ = imap.select(imap_mailbox)
            if status != "OK":
                LOG.error("Kan mailbox '%s' niet selecteren.", imap_mailbox)
                time.sleep(poll_interval)
                continue

            # Zoek ongelezen mails
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                LOG.error("Zoekopdracht naar ongelezen e-mails mislukt.")
                time.sleep(poll_interval)
                continue

            ids = data[0].split()
            if ids:
                LOG.info("%s nieuwe e-mail(s) gevonden.", len(ids))

            for num in ids:
                # Haal volledige mail op
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK":
                    LOG.error("Kon e-mail (UID %s) niet ophalen.", num)
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                from_raw = msg.get("From", "")
                subject_raw = msg.get("Subject", "")
                date_raw = msg.get("Date", "")

                afzender = decode_header_value(from_raw)
                onderwerp = decode_header_value(subject_raw)
                tekst = extract_text_from_message(msg)

                payload = {
                    "uid": num.decode() if isinstance(num, bytes) else str(num),
                    "afzender": afzender,
                    "onderwerp": onderwerp,
                    "tekst": tekst,
                    "datum": date_raw,
                }

                # Publiceer naar MQTT
                client.publish(mqtt_topic, json.dumps(payload), qos=0, retain=False)
                LOG.info(
                    "E-mail (UID %s) gepubliceerd op MQTT-topic '%s'.",
                    payload["uid"],
                    mqtt_topic,
                )

                last_uid = payload["uid"]

        except imaplib.IMAP4.error as e:
            LOG.error("IMAP-fout: %s. Poging tot herverbinden over %s seconden.", e, poll_interval)
            try:
                imap.logout()
            except Exception:
                pass
            time.sleep(poll_interval)
            try:
                imap = imaplib.IMAP4_SSL(imap_host, imap_port)
                imap.login(imap_user, imap_pass)
            except Exception as e2:
                LOG.error("Herverbinden met IMAP mislukt: %s", e2)

        except Exception as e:
            LOG.error("Onverwachte fout: %s", e)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
