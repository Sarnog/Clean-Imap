import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
import time
import json

# -------------------------------------------------------
# 1. Lees Home Assistant add-on opties
# -------------------------------------------------------
with open("/data/options.json", "r") as f:
    opts = json.load(f)

IMAP_HOST = opts.get("imap_host")
IMAP_PORT = int(opts.get("imap_port"))
IMAP_USER = opts.get("imap_username")
IMAP_PASS = opts.get("imap_password")

MQTT_HOST = opts.get("mqtt_host")
MQTT_PORT = int(opts.get("mqtt_port"))
MQTT_USER = opts.get("mqtt_username")
MQTT_PASS = opts.get("mqtt_password")
MQTT_TOPIC = opts.get("mqtt_topic")

# -------------------------------------------------------
# 2. Helper: HTML opschonen
# -------------------------------------------------------
def html_to_text(html):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    # verwijder lege regels
    clean = "\n".join(
        line.strip() for line in text.splitlines() if line.strip()
    )
    return clean


# -------------------------------------------------------
# 3. Header decoderen (onderwerp, afzender, etc.)
# -------------------------------------------------------
def decode_header_value(value):
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            decoded += text.decode(enc or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


# -------------------------------------------------------
# 4. Beste leesbare tekst zoeken in MIME e-mail
# -------------------------------------------------------
def extract_body(msg):
    text_plain = None
    text_html = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()

            if ctype == "text/plain" and text_plain is None:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset()
                try:
                    text_plain = payload.decode(charset or "utf-8", errors="replace")
                except:
                    pass

            elif ctype == "text/html" and text_html is None:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset()
                try:
                    text_html = payload.decode(charset or "utf-8", errors="replace")
                except:
                    pass
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True)

        if payload:
            charset = msg.get_content_charset()
            try:
                decoded = payload.decode(charset or "utf-8", errors="replace")
            except:
                decoded = None

            if ctype == "text/plain":
                text_plain = decoded
            elif ctype == "text/html":
                text_html = decoded

    if text_plain:
        return text_plain
    if text_html:
        return html_to_text(text_html)

    return "(Geen leesbare inhoud gevonden)"


# -------------------------------------------------------
# 5. Maak MQTT client – Future-proof (API v2)
# -------------------------------------------------------

mqtt_client = mqtt.Client(
    client_id="imap_cleaner",
    protocol=mqtt.MQTTv311,
    transport="tcp",
    callback_api_version=2
)

# MQTT authenticatie (indien aanwezig)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)


# -------------------------------------------------------
# 6. Main loop: IMAP uitlezen
# -------------------------------------------------------
def run_imap_loop():

    while True:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(IMAP_USER, IMAP_PASS)
            mail.select("INBOX")

            status, ids = mail.search(None, "UNSEEN")

            if status == "OK":
                for msg_id in ids[0].split():
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    onderwerp = decode_header_value(msg.get("subject"))
                    afzender = decode_header_value(msg.get("from"))
                    tekst = extract_body(msg)

                    payload = {
                        "onderwerp": onderwerp,
                        "afzender": afzender,
                        "tekst": tekst
                    }

                    mqtt_send(payload)

            mail.logout()
            time.sleep(5)

        except Exception as e:
            print("IMAP fout:", e)
            time.sleep(10)


if __name__ == "__main__":
    run_imap_loop()
