"""Kontá členov: aktivácia kódom, prihlásenie odborárskym číslom a heslom, Moje konto.

Prihlasovacie meno je odborárske číslo zo zoznamu platných členov (tabuľka clen).
Pri prvom prihlásení člen zadá aktivačný kód od správcu a nastaví si heslo.
"""
import csv
import io
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from anketa import CASOVA_ZONA, len_spravca
from db import get_db

bp = Blueprint("ucet", __name__)

ROLA_CLEN = "clen"  # ďalšie roly (napr. predseda ZO) pribudnú neskôr

MIN_DLZKA_HESLA = 8
MAX_POKUSOV = 5  # po toľkých nesprávnych heslách sa konto dočasne zamkne
ZAMKNUTIE = timedelta(minutes=15)

# Bez znakov, ktoré sa dajú pomýliť (0/O, 1/I/L).
ZNAKY_KODU = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def init_db(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ucet (
            cislo TEXT PRIMARY KEY,
            heslo_hash TEXT NOT NULL DEFAULT '',
            aktivacny_kod TEXT NOT NULL DEFAULT '',
            rola TEXT NOT NULL DEFAULT 'clen',
            zlyhane_pokusy INTEGER NOT NULL DEFAULT 0,
            zamknute_do TEXT NOT NULL DEFAULT ''
        );
        """
    )
    db.commit()


def teraz():
    return datetime.now(CASOVA_ZONA).strftime("%Y-%m-%d %H:%M:%S")


def novy_kod():
    return "".join(secrets.choice(ZNAKY_KODU) for _ in range(8))


def normalizuj_kod(kod):
    return kod.strip().upper().replace(" ", "").replace("-", "")


def je_clen(db, cislo):
    return db.execute("SELECT 1 FROM clen WHERE cislo = ?", (cislo,)).fetchone() is not None


def kontrola_hesla(heslo, heslo2):
    if len(heslo) < MIN_DLZKA_HESLA:
        return f"Heslo musí mať aspoň {MIN_DLZKA_HESLA} znakov."
    if heslo != heslo2:
        return "Heslá sa nezhodujú."
    return None


@bp.before_app_request
def nacitaj_prihlaseneho():
    """Do g.clen uloží prihláseného člena; ak medzitým vypadol zo zoznamu, odhlási ho."""
    g.clen = None
    cislo = session.get("clen")
    if not cislo:
        return
    row = get_db().execute(
        "SELECT c.cislo, c.meno, u.rola FROM clen c JOIN ucet u ON u.cislo = c.cislo "
        "WHERE c.cislo = ? AND u.heslo_hash != ''",
        (cislo,),
    ).fetchone()
    if row is None:
        session.pop("clen", None)
    else:
        g.clen = row


@bp.app_context_processor
def inject_clen():
    return {"prihlaseny_clen": g.get("clen")}


def len_prihlaseny(f):
    @wraps(f)
    def obal(*args, **kwargs):
        if g.get("clen") is None:
            return redirect(url_for("ucet.prihlasenie", dalej=request.path))
        return f(*args, **kwargs)
    return obal


def bezpecny_ciel(dalej):
    return dalej if dalej.startswith("/") and not dalej.startswith("//") else url_for("index")


# --- člen -------------------------------------------------------------------


@bp.route("/prihlasenie", methods=["GET", "POST"])
def prihlasenie():
    if g.clen is not None:
        return redirect(url_for("ucet.moje_konto"))
    if request.method == "POST":
        db = get_db()
        cislo = request.form.get("cislo", "").strip()
        heslo = request.form.get("heslo", "")
        ucet = db.execute("SELECT * FROM ucet WHERE cislo = ?", (cislo,)).fetchone()
        if ucet is not None and ucet["zamknute_do"] and teraz() < ucet["zamknute_do"]:
            flash("Konto je pre veľa nesprávnych pokusov dočasne zamknuté. Skúste to o 15 minút.")
        elif ucet is not None and ucet["heslo_hash"] and je_clen(db, cislo) \
                and check_password_hash(ucet["heslo_hash"], heslo):
            db.execute("UPDATE ucet SET zlyhane_pokusy = 0, zamknute_do = '' WHERE cislo = ?", (cislo,))
            db.commit()
            session["clen"] = cislo
            session.permanent = True
            return redirect(bezpecny_ciel(request.args.get("dalej", "")))
        else:
            if ucet is not None:
                pokusy = ucet["zlyhane_pokusy"] + 1
                zamknute = ""
                if pokusy >= MAX_POKUSOV:
                    pokusy = 0
                    zamknute = (datetime.now(CASOVA_ZONA) + ZAMKNUTIE).strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "UPDATE ucet SET zlyhane_pokusy = ?, zamknute_do = ? WHERE cislo = ?",
                    (pokusy, zamknute, cislo),
                )
                db.commit()
            if ucet is not None and not ucet["heslo_hash"]:
                flash("Konto ešte nie je aktivované. Použite prvé prihlásenie s aktivačným kódom.")
            else:
                flash("Nesprávne odborárske číslo alebo heslo.")
    return render_template("ucet/prihlasenie.html")


@bp.route("/aktivacia", methods=["GET", "POST"])
def aktivacia():
    if request.method == "POST":
        db = get_db()
        f = request.form
        cislo = f.get("cislo", "").strip()
        kod = normalizuj_kod(f.get("kod", ""))
        ucet = db.execute("SELECT * FROM ucet WHERE cislo = ?", (cislo,)).fetchone()
        if ucet is not None and ucet["zamknute_do"] and teraz() < ucet["zamknute_do"]:
            flash("Pre veľa nesprávnych pokusov je aktivácia dočasne zamknutá. Skúste to o 15 minút.")
        elif ucet is None or not ucet["aktivacny_kod"] or not je_clen(db, cislo) \
                or not secrets.compare_digest(ucet["aktivacny_kod"], kod):
            if ucet is not None:
                pokusy = ucet["zlyhane_pokusy"] + 1
                zamknute = ""
                if pokusy >= MAX_POKUSOV:
                    pokusy = 0
                    zamknute = (datetime.now(CASOVA_ZONA) + ZAMKNUTIE).strftime("%Y-%m-%d %H:%M:%S")
                db.execute("UPDATE ucet SET zlyhane_pokusy = ?, zamknute_do = ? WHERE cislo = ?",
                           (pokusy, zamknute, cislo))
                db.commit()
            flash("Odborárske číslo alebo aktivačný kód nie je správny. Kód vám vydá predseda ZO.")
        else:
            chyba = kontrola_hesla(f.get("heslo", ""), f.get("heslo2", ""))
            if chyba:
                flash(chyba)
            else:
                db.execute(
                    "UPDATE ucet SET heslo_hash = ?, aktivacny_kod = '', zlyhane_pokusy = 0, "
                    "zamknute_do = '' WHERE cislo = ?",
                    (generate_password_hash(f["heslo"]), cislo),
                )
                db.commit()
                session["clen"] = cislo
                session.permanent = True
                flash("Konto je aktivované a ste prihlásený.")
                return redirect(url_for("ucet.moje_konto"))
    return render_template("ucet/aktivacia.html")


@bp.route("/odhlasenie", methods=["POST"])
def odhlasenie():
    session.pop("clen", None)
    flash("Boli ste odhlásený.")
    return redirect(url_for("index"))


@bp.route("/moje-konto", methods=["GET", "POST"])
@len_prihlaseny
def moje_konto():
    if request.method == "POST":
        db = get_db()
        ucet = db.execute("SELECT * FROM ucet WHERE cislo = ?", (g.clen["cislo"],)).fetchone()
        f = request.form
        if not check_password_hash(ucet["heslo_hash"], f.get("stare", "")):
            flash("Súčasné heslo nie je správne.")
        else:
            chyba = kontrola_hesla(f.get("heslo", ""), f.get("heslo2", ""))
            if chyba:
                flash(chyba)
            else:
                db.execute("UPDATE ucet SET heslo_hash = ? WHERE cislo = ?",
                           (generate_password_hash(f["heslo"]), g.clen["cislo"]))
                db.commit()
                flash("Heslo bolo zmenené.")
        return redirect(url_for("ucet.moje_konto"))
    return render_template("ucet/moje_konto.html")


# --- správca ----------------------------------------------------------------


def prehlad_kont(db):
    return db.execute(
        "SELECT c.cislo, c.meno, COALESCE(u.heslo_hash != '', 0) AS aktivne, "
        "COALESCE(u.aktivacny_kod, '') AS kod FROM clen c "
        "LEFT JOIN ucet u ON u.cislo = c.cislo ORDER BY c.cislo"
    ).fetchall()


@bp.route("/spravca/konta")
@len_spravca
def spravca_konta():
    konta = prehlad_kont(get_db())
    return render_template(
        "ucet/spravca_konta.html",
        konta=konta,
        aktivnych=sum(1 for k in konta if k["aktivne"]),
        s_kodom=sum(1 for k in konta if k["kod"]),
    )


@bp.route("/spravca/konta/kody", methods=["POST"])
@len_spravca
def vygeneruj_kody():
    """Vytvorí aktivačný kód každému členovi, ktorý ešte nemá aktívne konto ani kód."""
    db = get_db()
    nove = 0
    for k in prehlad_kont(db):
        if not k["aktivne"] and not k["kod"]:
            db.execute(
                "INSERT INTO ucet (cislo, aktivacny_kod) VALUES (?, ?) "
                "ON CONFLICT(cislo) DO UPDATE SET aktivacny_kod = excluded.aktivacny_kod",
                (k["cislo"], novy_kod()),
            )
            nove += 1
    db.commit()
    flash(f"Vytvorené nové aktivačné kódy: {nove}.")
    return redirect(url_for("ucet.spravca_konta"))


@bp.route("/spravca/konta/obnovit", methods=["POST"])
@len_spravca
def obnov_pristup():
    """Zabudnuté heslo: zruší heslo člena a vydá mu nový aktivačný kód."""
    db = get_db()
    cislo = request.form.get("cislo", "").strip()
    if not je_clen(db, cislo):
        flash(f"Odborárske číslo {cislo} nie je v zozname členov.")
    else:
        kod = novy_kod()
        db.execute(
            "INSERT INTO ucet (cislo, aktivacny_kod) VALUES (?, ?) "
            "ON CONFLICT(cislo) DO UPDATE SET heslo_hash = '', aktivacny_kod = excluded.aktivacny_kod, "
            "zlyhane_pokusy = 0, zamknute_do = ''",
            (cislo, kod),
        )
        db.commit()
        flash(f"Nový aktivačný kód pre číslo {cislo}: {kod}. Doterajšie heslo už neplatí.")
    return redirect(url_for("ucet.spravca_konta"))


@bp.route("/spravca/konta/kody.csv")
@len_spravca
def export_kodov():
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Odborárske číslo", "Meno", "Aktivačný kód"])
    for k in prehlad_kont(get_db()):
        if k["kod"]:
            w.writerow([k["cislo"], k["meno"], k["kod"]])
    return Response(
        "﻿" + out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="aktivacne-kody.csv"'},
    )
