import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
import time
import json
import sys

print("IMAP Cleaner: Python script started", flush=True)

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

print("IMAP Cleaner: Options geladen:", opts, flush=True)

# -------------------------------------------------------
# 2. HTML opschonen
# -------------------------------------------------------
def html_to_text(html):
    """Converteert HTML naar platte tekst."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")

    clean = "\n".join(
        line.strip() for line in text.splitlines() if line.strip()
    )
    return clean


# -------------------------------------------------------
# 3. Header (subject, from, etc.) veilig decoderen
# -------------------------------------------------------
def decode_header_value(value):
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""

    for text, enc in parts:
        try:
            if isinstance(text, bytes):
                decoded += text.decode(enc or "utf-8", errors="replace")
            else:
                decoded += text
        except:
            decoded += str(text)

    return decoded


# -------------------------------------------------------
# 4. Beste leesbare e-mailtekst ophalen (plain > html)
# -------------------------------------------------------
def extract_body(msg):
    text_plain = None
    text_html = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"

            if ctype == "text/plain" and text_plain is None:
                try:
                    text_plain = payload.decode(charset, errors="replace")
                except:
                    pass

            elif ctype == "text/html" and text_html is None:
                try:
                    text_html = payload.decode(charset, errors="replace")
                except:
                    pass

    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"

        try:
            decoded = payload.decode(charset, errors="replace")
        except:
            decoded = None

        ctype = msg.get_content_type()

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
# 5. MQTT client (API v2 – future-proof)
# -------------------------------------------------------
mqtt_client = mqtt.Client(
    client_id="imap_cleaner",
    protocol=mqtt.MQTTv311,
    transport="tcp",
    callback_api_version=2
)

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

print("IMAP Cleaner: MQTT client gestart", flush=True)


# -------------------------------------------------------
# 6. MQTT publish functie (ontbrak!)
# -------------------------------------------------------
def mqtt_send(data):
    try:
        print("IMAP Cleaner: MQTT publish:", data, flush=True)
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.publish(MQTT_TOPIC, json.dumps(data))
        mqtt_client.disconnect()
    except Exception as e:
        print("MQTT fout:", e, flush=True)


# -------------------------------------------------------
# 7. Main loop – IMAP uitlezen
# -------------------------------------------------------
def run_imap_loop():

    print("IMAP Cleaner: IMAP loop gestart", flush=True)

    while True:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(IMAP_USER, IMAP_PASS)
            mail.select("INBOX")

            status, ids = mail.search(None, "UNSEEN")
            print("IMAP Cleaner: UNSEEN:", status, ids, flush=True)

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
            print("IMAP fout:", e, flush=True)
            time.sleep(10)


# -------------------------------------------------------
# 8. Start script
# -------------------------------------------------------
if __name__ == "__main__":
    run_imap_loop()
