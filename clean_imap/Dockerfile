FROM python:3.12-slim

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY imap_cleaner.py /imap_cleaner.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
