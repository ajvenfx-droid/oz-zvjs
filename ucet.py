"""Kontá členov: prihlásenie odborárskym číslom a heslom, Moje konto.

Prihlasovacie meno je odborárske číslo zo zoznamu platných členov (tabuľka clen).
Kým si člen nenastaví vlastné heslo, heslom je číslo jeho zamestnaneckého
alebo služobného preukazu zo zoznamu členov.
"""
import hmac
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from anketa import CASOVA_ZONA, hash_preukazu, len_spravca
from db import get_db

bp = Blueprint("ucet", __name__)

ROLA_CLEN = "clen"  # ďalšie roly (napr. predseda ZO) pribudnú neskôr

MIN_DLZKA_HESLA = 8
MAX_POKUSOV = 5  # po toľkých nesprávnych heslách sa konto dočasne zamkne
ZAMKNUTIE = timedelta(minutes=15)


def init_db(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ucet (
            cislo TEXT PRIMARY KEY,
            heslo_hash TEXT NOT NULL DEFAULT '',
            rola TEXT NOT NULL DEFAULT 'clen',
            zlyhane_pokusy INTEGER NOT NULL DEFAULT 0,
            zamknute_do TEXT NOT NULL DEFAULT ''
        );
        """
    )
    db.commit()


def teraz():
    return datetime.now(CASOVA_ZONA).strftime("%Y-%m-%d %H:%M:%S")


def kontrola_hesla(heslo, heslo2):
    if len(heslo) < MIN_DLZKA_HESLA:
        return f"Heslo musí mať aspoň {MIN_DLZKA_HESLA} znakov."
    if heslo != heslo2:
        return "Heslá sa nezhodujú."
    return None


def over_heslo(db, clen, ucet, heslo):
    """Vlastné heslo, ak si ho člen nastavil; inak číslo preukazu zo zoznamu členov."""
    if ucet is not None and ucet["heslo_hash"]:
        return check_password_hash(ucet["heslo_hash"], heslo)
    if not clen["preukaz_hash"] or not heslo.strip():
        return False
    return hmac.compare_digest(clen["preukaz_hash"], hash_preukazu(db, heslo))


def zaznamenaj_zlyhanie(db, cislo, ucet):
    pokusy = (ucet["zlyhane_pokusy"] if ucet else 0) + 1
    zamknute = ""
    if pokusy >= MAX_POKUSOV:
        pokusy = 0
        zamknute = (datetime.now(CASOVA_ZONA) + ZAMKNUTIE).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO ucet (cislo, zlyhane_pokusy, zamknute_do) VALUES (?, ?, ?) "
        "ON CONFLICT(cislo) DO UPDATE SET zlyhane_pokusy = excluded.zlyhane_pokusy, "
        "zamknute_do = excluded.zamknute_do",
        (cislo, pokusy, zamknute),
    )
    db.commit()


@bp.before_app_request
def nacitaj_prihlaseneho():
    """Do g.clen uloží prihláseného člena; ak medzitým vypadol zo zoznamu, odhlási ho."""
    g.clen = None
    cislo = session.get("clen")
    if not cislo:
        return
    row = get_db().execute(
        "SELECT c.cislo, c.meno, COALESCE(u.rola, 'clen') AS rola, "
        "COALESCE(u.heslo_hash, '') != '' AS vlastne_heslo "
        "FROM clen c LEFT JOIN ucet u ON u.cislo = c.cislo WHERE c.cislo = ?",
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
        clen = db.execute("SELECT * FROM clen WHERE cislo = ?", (cislo,)).fetchone()
        ucet = db.execute("SELECT * FROM ucet WHERE cislo = ?", (cislo,)).fetchone()
        if ucet is not None and ucet["zamknute_do"] and teraz() < ucet["zamknute_do"]:
            flash("Konto je pre veľa nesprávnych pokusov dočasne zamknuté. Skúste to o 15 minút.")
        elif clen is not None and over_heslo(db, clen, ucet, heslo):
            db.execute(
                "INSERT INTO ucet (cislo) VALUES (?) ON CONFLICT(cislo) DO UPDATE SET "
                "zlyhane_pokusy = 0, zamknute_do = ''",
                (cislo,),
            )
            db.commit()
            session["clen"] = cislo
            session.permanent = True
            return redirect(bezpecny_ciel(request.args.get("dalej", "")))
        else:
            if clen is not None:
                zaznamenaj_zlyhanie(db, cislo, ucet)
            flash("Nesprávne odborárske číslo alebo heslo.")
    return render_template("ucet/prihlasenie.html")


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
        cislo = g.clen["cislo"]
        clen = db.execute("SELECT * FROM clen WHERE cislo = ?", (cislo,)).fetchone()
        ucet = db.execute("SELECT * FROM ucet WHERE cislo = ?", (cislo,)).fetchone()
        f = request.form
        if not over_heslo(db, clen, ucet, f.get("stare", "")):
            flash("Súčasné heslo nie je správne.")
        else:
            chyba = kontrola_hesla(f.get("heslo", ""), f.get("heslo2", ""))
            if chyba:
                flash(chyba)
            else:
                db.execute(
                    "INSERT INTO ucet (cislo, heslo_hash) VALUES (?, ?) "
                    "ON CONFLICT(cislo) DO UPDATE SET heslo_hash = excluded.heslo_hash",
                    (cislo, generate_password_hash(f["heslo"])),
                )
                db.commit()
                flash("Heslo bolo zmenené.")
        return redirect(url_for("ucet.moje_konto"))
    return render_template("ucet/moje_konto.html")


# --- správca ----------------------------------------------------------------


@bp.route("/spravca/konta")
@len_spravca
def spravca_konta():
    konta = get_db().execute(
        "SELECT c.cislo, c.meno, c.preukaz_hash != '' AS ma_preukaz, "
        "COALESCE(u.heslo_hash, '') != '' AS vlastne_heslo FROM clen c "
        "LEFT JOIN ucet u ON u.cislo = c.cislo ORDER BY c.cislo"
    ).fetchall()
    return render_template(
        "ucet/spravca_konta.html",
        konta=konta,
        vlastnych=sum(1 for k in konta if k["vlastne_heslo"]),
        bez_preukazu=sum(1 for k in konta if not k["ma_preukaz"] and not k["vlastne_heslo"]),
    )


@bp.route("/spravca/konta/obnovit", methods=["POST"])
@len_spravca
def obnov_heslo():
    """Zabudnuté heslo: zruší vlastné heslo člena, heslom je znova číslo preukazu."""
    db = get_db()
    cislo = request.form.get("cislo", "").strip()
    if db.execute("SELECT 1 FROM clen WHERE cislo = ?", (cislo,)).fetchone() is None:
        flash(f"Odborárske číslo {cislo} nie je v zozname členov.")
    else:
        db.execute(
            "UPDATE ucet SET heslo_hash = '', zlyhane_pokusy = 0, zamknute_do = '' WHERE cislo = ?",
            (cislo,),
        )
        db.commit()
        flash(f"Heslo člena {cislo} je obnovené. Prihlási sa číslom svojho preukazu.")
    return redirect(url_for("ucet.spravca_konta"))
