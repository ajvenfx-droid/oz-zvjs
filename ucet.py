"""Kontá členov: prihlásenie odborárskym číslom a heslom, Moje konto.

Prihlasovacie meno je odborárske číslo zo zoznamu platných členov (tabuľka clen).
Kým si člen nenastaví vlastné heslo, heslom je číslo jeho zamestnaneckého
alebo služobného preukazu zo zoznamu členov.
"""
import hashlib
import hmac
import re
import secrets
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

import posta
from anketa import CASOVA_ZONA, hash_preukazu, len_spravca, nastav, nastavenie
from db import get_db

bp = Blueprint("ucet", __name__)

ROLA_CLEN = "clen"  # ďalšie roly (napr. predseda ZO) pribudnú neskôr

MIN_DLZKA_HESLA = 8
MAX_POKUSOV = 5  # po toľkých nesprávnych heslách sa konto dočasne zamkne
ZAMKNUTIE = timedelta(minutes=15)
PLATNOST_ODKAZU = timedelta(hours=1)  # odkaz na obnovu hesla z e-mailu
ODSTUP_EMAILOV = timedelta(minutes=5)  # najviac jeden e-mail s obnovou za 5 minút

ROLE = {
    "clen": "bez prístupu k evidencii",
    "zo": "predseda / podpredseda ZO – mení členov svojej ZO",
    "vedenie": "predsedníctvo – prezerá všetky ZO, nemení",
}

EMAIL_VZOR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
        CREATE TABLE IF NOT EXISTS obnova_hesla (
            token_hash TEXT PRIMARY KEY,
            cislo TEXT NOT NULL,
            platne_do TEXT NOT NULL
        );
        """
    )
    stlpce = [r["name"] for r in db.execute("PRAGMA table_info(ucet)")]
    if "email" not in stlpce:
        db.execute("ALTER TABLE ucet ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "zo_slug" not in stlpce:
        db.execute("ALTER TABLE ucet ADD COLUMN zo_slug TEXT NOT NULL DEFAULT ''")
    db.commit()


def teraz(posun=timedelta()):
    return (datetime.now(CASOVA_ZONA) + posun).strftime("%Y-%m-%d %H:%M:%S")


def hash_tokenu(token):
    return hashlib.sha256(token.encode()).hexdigest()


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
        "COALESCE(u.heslo_hash, '') != '' AS vlastne_heslo, COALESCE(u.email, '') AS email, "
        "COALESCE(u.zo_slug, '') AS zo_slug "
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
        f = request.form
        if f.get("akcia") == "email":
            email = f.get("email", "").strip()
            if email and not EMAIL_VZOR.match(email):
                flash("Zadajte platnú e-mailovú adresu.")
            else:
                db.execute(
                    "INSERT INTO ucet (cislo, email) VALUES (?, ?) "
                    "ON CONFLICT(cislo) DO UPDATE SET email = excluded.email",
                    (cislo, email),
                )
                db.commit()
                flash("E-mail bol uložený." if email else "E-mail bol odstránený.")
            return redirect(url_for("ucet.moje_konto"))
        clen = db.execute("SELECT * FROM clen WHERE cislo = ?", (cislo,)).fetchone()
        ucet = db.execute("SELECT * FROM ucet WHERE cislo = ?", (cislo,)).fetchone()
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


@bp.route("/zabudnute-heslo", methods=["GET", "POST"])
def zabudnute_heslo():
    db = get_db()
    if request.method == "POST":
        cislo = request.form.get("cislo", "").strip()
        ucet = db.execute(
            "SELECT u.* FROM ucet u JOIN clen c ON c.cislo = u.cislo "
            "WHERE u.cislo = ? AND u.email != ''",
            (cislo,),
        ).fetchone()
        if ucet is not None and posta.je_nastavena(db):
            nedavny = db.execute(
                "SELECT 1 FROM obnova_hesla WHERE cislo = ? AND platne_do > ?",
                (cislo, teraz(PLATNOST_ODKAZU - ODSTUP_EMAILOV)),
            ).fetchone()
            if nedavny is None:
                token = secrets.token_urlsafe(32)
                db.execute("DELETE FROM obnova_hesla WHERE cislo = ? OR platne_do < ?", (cislo, teraz()))
                db.execute(
                    "INSERT INTO obnova_hesla (token_hash, cislo, platne_do) VALUES (?, ?, ?)",
                    (hash_tokenu(token), cislo, teraz(PLATNOST_ODKAZU)),
                )
                db.commit()
                odkaz = url_for("ucet.nove_heslo", token=token, _external=True)
                try:
                    posta.posli(
                        db,
                        ucet["email"],
                        "Obnovenie hesla – Odborový zväz ZVJS",
                        "Dobrý deň,\n\n"
                        "dostali sme žiadosť o obnovenie hesla k vášmu kontu "
                        f"(odborárske číslo {cislo}).\n\n"
                        f"Nové heslo si nastavíte na tomto odkaze, ktorý platí 1 hodinu:\n{odkaz}\n\n"
                        "Ak ste o obnovenie nežiadali, tento e-mail ignorujte. Vaše heslo sa nezmenilo.\n\n"
                        "Odborový zväz Zboru väzenskej a justičnej stráže",
                    )
                except Exception:
                    db.execute("DELETE FROM obnova_hesla WHERE cislo = ?", (cislo,))
                    db.commit()
                    flash("E-mail sa nepodarilo odoslať. Skúste to neskôr alebo kontaktujte predsedu ZO.")
                    return redirect(url_for("ucet.zabudnute_heslo"))
        # Rovnaká správa vždy, aby sa nedalo zistiť, ktoré čísla majú zadaný e-mail.
        flash("Ak má konto zadaný e-mail, poslali sme naň odkaz na nastavenie nového hesla. "
              "Skontrolujte aj priečinok so spamom.")
        return redirect(url_for("ucet.prihlasenie"))
    return render_template("ucet/zabudnute_heslo.html")


@bp.route("/nove-heslo/<token>", methods=["GET", "POST"])
def nove_heslo(token):
    db = get_db()
    zaznam = db.execute(
        "SELECT * FROM obnova_hesla WHERE token_hash = ? AND platne_do > ?",
        (hash_tokenu(token), teraz()),
    ).fetchone()
    if zaznam is None:
        flash("Odkaz na obnovenie hesla je neplatný alebo vypršal. Požiadajte o nový.")
        return redirect(url_for("ucet.zabudnute_heslo"))
    if request.method == "POST":
        chyba = kontrola_hesla(request.form.get("heslo", ""), request.form.get("heslo2", ""))
        if chyba:
            flash(chyba)
        else:
            db.execute(
                "UPDATE ucet SET heslo_hash = ?, zlyhane_pokusy = 0, zamknute_do = '' WHERE cislo = ?",
                (generate_password_hash(request.form["heslo"]), zaznam["cislo"]),
            )
            db.execute("DELETE FROM obnova_hesla WHERE cislo = ?", (zaznam["cislo"],))
            db.commit()
            session["clen"] = zaznam["cislo"]
            session.permanent = True
            flash("Nové heslo je nastavené a ste prihlásený.")
            return redirect(url_for("ucet.moje_konto"))
    return render_template("ucet/nove_heslo.html")


# --- správca ----------------------------------------------------------------


@bp.route("/spravca/konta")
@len_spravca
def spravca_konta():
    db = get_db()
    konta = db.execute(
        "SELECT c.cislo, c.meno, c.preukaz_hash != '' AS ma_preukaz, "
        "COALESCE(u.heslo_hash, '') != '' AS vlastne_heslo, COALESCE(u.email, '') AS email "
        "FROM clen c LEFT JOIN ucet u ON u.cislo = c.cislo ORDER BY c.cislo"
    ).fetchall()
    return render_template(
        "ucet/spravca_konta.html",
        konta=konta,
        vlastnych=sum(1 for k in konta if k["vlastne_heslo"]),
        bez_preukazu=sum(1 for k in konta if not k["ma_preukaz"] and not k["vlastne_heslo"]),
        s_emailom=sum(1 for k in konta if k["email"]),
        smtp_email=nastavenie(db, "smtp_email") or "",
        email_nastaveny=posta.je_nastavena(db),
        role=ROLE,
        pristupy=db.execute(
            "SELECT u.cislo, c.meno, u.rola, u.zo_slug, z.mesto FROM ucet u "
            "JOIN clen c ON c.cislo = u.cislo "
            "LEFT JOIN zakladna_organizacia z ON z.slug = u.zo_slug "
            "WHERE u.rola IN ('zo', 'vedenie') ORDER BY u.rola, z.poradie, c.meno"
        ).fetchall(),
        organizacie=db.execute(
            "SELECT slug, mesto FROM zakladna_organizacia ORDER BY poradie, mesto").fetchall(),
    )


@bp.route("/spravca/konta/rola", methods=["POST"])
@len_spravca
def nastav_rolu():
    """Prístup člena k evidencii členov: bez prístupu / vedenie ZO (svoja ZO) / predsedníctvo (prezeranie)."""
    db = get_db()
    cislo = request.form.get("cislo", "").strip()
    rola = request.form.get("rola", "clen")
    zo_slug = request.form.get("zo_slug", "").strip() if rola == "zo" else ""
    if rola not in ROLE:
        rola = "clen"
    clen = db.execute("SELECT meno FROM clen WHERE cislo = ?", (cislo,)).fetchone()
    if clen is None:
        flash(f"Odborárske číslo {cislo} nie je v zozname členov.")
    elif rola == "zo" and db.execute(
            "SELECT 1 FROM zakladna_organizacia WHERE slug = ?", (zo_slug,)).fetchone() is None:
        flash("Vyberte základnú organizáciu.")
    else:
        db.execute(
            "INSERT INTO ucet (cislo, rola, zo_slug) VALUES (?, ?, ?) "
            "ON CONFLICT(cislo) DO UPDATE SET rola = excluded.rola, zo_slug = excluded.zo_slug",
            (cislo, rola, zo_slug),
        )
        db.commit()
        flash(f"{clen['meno'] or cislo}: {ROLE[rola]}.")
    return redirect(url_for("ucet.spravca_konta") + "#pristupy")


@bp.route("/spravca/email", methods=["POST"])
@len_spravca
def nastav_email():
    db = get_db()
    f = request.form
    if f.get("akcia") == "test":
        try:
            posta.posli(db, nastavenie(db, "smtp_email"), "Skúšobný e-mail – stránka OZ ZVJS",
                        "Odosielanie e-mailov zo stránky funguje.")
            flash(f"Skúšobný e-mail bol odoslaný na {nastavenie(db, 'smtp_email')}.")
        except Exception as e:
            flash(f"Skúšobný e-mail sa nepodarilo odoslať: {e}")
        return redirect(url_for("ucet.spravca_konta"))
    email = f.get("smtp_email", "").strip()
    heslo = f.get("smtp_heslo", "").replace(" ", "")
    if not EMAIL_VZOR.match(email):
        flash("Zadajte platnú adresu Gmail schránky.")
    else:
        nastav(db, "smtp_email", email)
        if heslo:
            nastav(db, "smtp_heslo", heslo)
        db.commit()
        flash("Nastavenie odosielania e-mailov bolo uložené. Pošlite si skúšobný e-mail.")
    return redirect(url_for("ucet.spravca_konta"))


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
