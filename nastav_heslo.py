"""Nastaví (alebo zmení) heslo správcu ankiet.

Spustenie v Bash konzole:  cd ~/oz-zvjs && python3 nastav_heslo.py
"""
import getpass

from werkzeug.security import generate_password_hash

import anketa
from db import connect

db = connect()
anketa.init_db(db)
while True:
    heslo = getpass.getpass("Nové heslo správcu (aspoň 8 znakov): ")
    if len(heslo) < 8:
        print("Heslo je príliš krátke.")
    elif heslo != getpass.getpass("Zopakujte heslo: "):
        print("Heslá sa nezhodujú, skúste znova.")
    else:
        break
anketa.nastav(db, "heslo_spravcu", generate_password_hash(heslo))
db.commit()
db.close()
print("Heslo správcu je nastavené. Na karte Web kliknite na Reload.")
