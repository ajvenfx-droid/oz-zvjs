"""Záloha databázy oz_zvjs.db (všetky údaje stránky vrátane fotiek a dokumentov).

- Každý deň sa automaticky uloží kópia do priečinka zalohy/ (pri prvej návšteve stránky v daný deň
  a tiež pri spustení aktualizuj.sh). Ponechá sa posledných 14 denných záloh.
- Správca si v Správe stránky môže stiahnuť aktuálnu zálohu alebo niektorú z uložených.
- Ručne v Bash konzole:  python3 ~/oz-zvjs/zaloha.py
Priečinok zalohy/ nejde na GitHub (.gitignore), lebo obsahuje osobné údaje členov.
"""
import gzip
import os
import shutil
import re
import sqlite3
import tempfile
import threading
from datetime import date, datetime

DB_PATH = os.environ.get("OZ_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "oz_zvjs.db"))
ADRESAR_ZALOH = os.environ.get("OZ_ZALOHY", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "zalohy"))
PONECHAT = 14
NAZOV = re.compile(r"^oz_zvjs-\d{4}-\d{2}-\d{2}\.db\.gz$")

_zamok = threading.Lock()
_posledny_den = None


def _kopia(ciel):
    """Konzistentná kópia databázy aj počas behu stránky (SQLite backup API)."""
    zdroj = sqlite3.connect(DB_PATH)
    try:
        dest = sqlite3.connect(ciel)
        with dest:
            zdroj.backup(dest)
        dest.close()
    finally:
        zdroj.close()


def zoznam_zaloh():
    if not os.path.isdir(ADRESAR_ZALOH):
        return []
    subory = sorted((f for f in os.listdir(ADRESAR_ZALOH) if NAZOV.match(f)), reverse=True)
    return [{"nazov": f, "velkost": os.path.getsize(os.path.join(ADRESAR_ZALOH, f)),
             "cas": datetime.fromtimestamp(os.path.getmtime(os.path.join(ADRESAR_ZALOH, f)))} for f in subory]


def denna_zaloha(vynutit=False):
    """Vytvorí dnešnú zálohu, ak ešte neexistuje, a zmaže staršie ako posledných PONECHAT. Vráti cestu."""
    global _posledny_den
    dnes = date.today().isoformat()
    if _posledny_den == dnes and not vynutit:
        return None
    with _zamok:
        os.makedirs(ADRESAR_ZALOH, exist_ok=True)
        cesta = os.path.join(ADRESAR_ZALOH, f"oz_zvjs-{dnes}.db.gz")
        if vynutit or not os.path.exists(cesta):
            docasna = os.path.join(ADRESAR_ZALOH, ".kopia.db")
            _kopia(docasna)
            with open(docasna, "rb") as zdroj, gzip.open(cesta + ".tmp", "wb") as ciel:
                shutil.copyfileobj(zdroj, ciel)
            os.remove(docasna)
            os.replace(cesta + ".tmp", cesta)
        for z in zoznam_zaloh()[PONECHAT:]:
            try:
                os.remove(os.path.join(ADRESAR_ZALOH, z["nazov"]))
            except OSError:
                pass
        _posledny_den = dnes
        return cesta


try:  # webová časť; samotná záloha (python3 zaloha.py) Flask nepotrebuje
    from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
except ImportError:
    Blueprint = None

if Blueprint:
    bp = Blueprint("zaloha", __name__)

    @bp.before_app_request
    def _automaticka_zaloha():
        if _posledny_den == date.today().isoformat():
            return
        try:
            denna_zaloha()
        except Exception:
            pass  # záloha nesmie zablokovať stránku


    @bp.route("/spravca/zaloha", methods=["GET", "POST"])
    def spravca():
        from anketa import je_spravca
        if not je_spravca():
            return redirect(url_for("anketa.prihlasenie", dalej=request.path))
        if request.method == "POST":
            denna_zaloha(vynutit=True)
            flash("Záloha bola vytvorená.")
            return redirect(url_for("zaloha.spravca"))
        return render_template("zaloha.html", zalohy=zoznam_zaloh(), ponechat=PONECHAT)


    @bp.route("/spravca/zaloha/stiahnut")
    @bp.route("/spravca/zaloha/stiahnut/<nazov>")
    def stiahnut(nazov=None):
        from anketa import je_spravca
        if not je_spravca():
            abort(403)
        if nazov:
            if not NAZOV.match(nazov) or not os.path.exists(os.path.join(ADRESAR_ZALOH, nazov)):
                abort(404)
            return send_file(os.path.join(ADRESAR_ZALOH, nazov), as_attachment=True, download_name=nazov,
                             mimetype="application/octet-stream")
        fd, docasna = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        _kopia(docasna)
        with open(docasna, "rb") as f:
            data = f.read()
        os.remove(docasna)
        import io
        return send_file(io.BytesIO(data), as_attachment=True,
                         download_name=f"oz_zvjs-{datetime.now():%Y-%m-%d-%H%M}.db", mimetype="application/octet-stream")


if __name__ == "__main__":
    print("Záloha uložená:", denna_zaloha(vynutit=True))
