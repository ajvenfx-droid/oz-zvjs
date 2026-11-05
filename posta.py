"""Posielanie e-mailov cez Gmail (jediný SMTP server povolený aj pre bezplatný PythonAnywhere).

Adresa schránky a heslo pre aplikácie sa nastavujú v správe (Kontá členov)
a ukladajú sa v databáze na serveri.
"""
import smtplib
from email.message import EmailMessage

from anketa import nastavenie

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


def je_nastavena(db):
    return bool(nastavenie(db, "smtp_email")) and bool(nastavenie(db, "smtp_heslo"))


def posli(db, komu, predmet, text):
    """Pošle e-mail; pri chybe vyhodí výnimku (smtplib.SMTPException alebo OSError)."""
    odosielatel = nastavenie(db, "smtp_email")
    sprava = EmailMessage()
    sprava["From"] = f"Odborový zväz ZVJS <{odosielatel}>"
    sprava["To"] = komu
    sprava["Subject"] = predmet
    sprava.set_content(text)
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(odosielatel, nastavenie(db, "smtp_heslo"))
        smtp.send_message(sprava)
