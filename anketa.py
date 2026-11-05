"""Ankety pre členov: hlasovanie s overením odborárskeho čísla a správa ankiet.

Ankety nie sú anonymné: pri každom hlase sa ukladá odborárske číslo
a správca vo výsledkoch vidí, kto ako hlasoval.
"""
import csv
import io
import re
import secrets
import unicodedata
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from db import get_db

bp = Blueprint("anketa", __name__, url_prefix="/anketa")

CASOVA_ZONA = ZoneInfo("Europe/Bratislava")

TYP_JEDNA = "jedna"  # výber jednej možnosti
TYP_VIAC = "viac"  # výber viacerých možností
TYP_TEXT = "text"  # voľná textová odpoveď

# Anketa vložená pri prvom spustení (len raz, aj keby ju správca neskôr zmazal).
PRVA_ANKETA = {
    "nazov": "Nákup hodiniek na plaváreň",
    "otazky": "Nákup hodiniek na plaváreň\n- 1 ks\n- 2 ks\n- 3 ks\n- 4 ks\n- 5 ks",
}

PREDVOLENY_UVOD = (
    "Na hlasovanie zadajte svoje odborárske číslo. Každý člen môže hlasovať iba raz.\n\n"
    "Hlasovanie nie je anonymné: vaše odborárske číslo sa uloží spolu s vašou odpoveďou "
    "a správca ankety uvidí, ako ste hlasovali. Údaje slúžia iba na vyhodnotenie tejto "
    "ankety v rámci odborovej organizácie a nebudú poskytnuté tretím stranám."
)


# --- databáza ---------------------------------------------------------------


def init_db(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS nastavenie (
            kluc TEXT PRIMARY KEY,
            hodnota TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clen (
            cislo TEXT PRIMARY KEY,
            meno TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS anketa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            nazov TEXT NOT NULL,
            uvod TEXT NOT NULL DEFAULT '',
            koniec TEXT NOT NULL DEFAULT '',
            otazky_text TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS otazka (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anketa_id INTEGER NOT NULL REFERENCES anketa(id) ON DELETE CASCADE,
            poradie INTEGER NOT NULL,
            text TEXT NOT NULL,
            typ TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS moznost (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            otazka_id INTEGER NOT NULL REFERENCES otazka(id) ON DELETE CASCADE,
            poradie INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hlas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anketa_id INTEGER NOT NULL REFERENCES anketa(id) ON DELETE CASCADE,
            cislo TEXT NOT NULL,
            cas TEXT NOT NULL,
            UNIQUE (anketa_id, cislo)
        );
        CREATE TABLE IF NOT EXISTS odpoved (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hlas_id INTEGER NOT NULL REFERENCES hlas(id) ON DELETE CASCADE,
            otazka_id INTEGER NOT NULL REFERENCES otazka(id) ON DELETE CASCADE,
            moznost_id INTEGER REFERENCES moznost(id) ON DELETE CASCADE,
            text TEXT NOT NULL DEFAULT ''
        );
        """
    )
    if nastavenie(db, "prva_anketa_vlozena") is None:
        if db.execute("SELECT COUNT(*) FROM anketa").fetchone()[0] == 0:
            uloz_anketu(db, None, PRVA_ANKETA["nazov"], PREDVOLENY_UVOD, "", PRVA_ANKETA["otazky"])
        nastav(db, "prva_anketa_vlozena", "1")
    db.commit()


def nastavenie(db, kluc):
    row = db.execute("SELECT hodnota FROM nastavenie WHERE kluc = ?", (kluc,)).fetchone()
    return row["hodnota"] if row else None


def nastav(db, kluc, hodnota):
    db.execute(
        "INSERT INTO nastavenie (kluc, hodnota) VALUES (?, ?) "
        "ON CONFLICT(kluc) DO UPDATE SET hodnota = excluded.hodnota",
        (kluc, hodnota),
    )


def secret_key(db):
    """Tajný kľúč pre prihlásenie správcu; vytvorí sa raz a uloží do databázy."""
    kluc = nastavenie(db, "secret_key")
    if kluc is None:
        kluc = secrets.token_hex(32)
        nastav(db, "secret_key", kluc)
        db.commit()
    return kluc


# --- pomocné funkcie --------------------------------------------------------


def teraz():
    return datetime.now(CASOVA_ZONA).strftime("%Y-%m-%dT%H:%M")


def je_ukoncena(anketa):
    return bool(anketa["koniec"]) and teraz() >= anketa["koniec"]


def format_koniec(hodnota):
    if not hodnota:
        return ""
    return datetime.strptime(hodnota, "%Y-%m-%dT%H:%M").strftime("%d.%m.%Y o %H:%M")


bp.add_app_template_filter(format_koniec, "koniec")


def vytvor_slug(db, nazov, anketa_id=None):
    zaklad = unicodedata.normalize("NFKD", nazov).encode("ascii", "ignore").decode()
    zaklad = re.sub(r"[^a-z0-9]+", "-", zaklad.lower()).strip("-")[:60] or "anketa"
    slug, i = zaklad, 2
    while True:
        row = db.execute("SELECT id FROM anketa WHERE slug = ?", (slug,)).fetchone()
        if row is None or row["id"] == anketa_id:
            return slug
        slug, i = f"{zaklad}-{i}", i + 1


def rozober_otazky(text):
    """Prevedie text otázok na zoznam (text, typ, možnosti).

    Riadok bez pomlčky je otázka. Riadky „- …“ sú možnosti s výberom jednej,
    riadky „+ …“ možnosti s výberom viacerých. Otázka bez možností je textová.
    """
    otazky = []
    for riadok in text.splitlines():
        riadok = riadok.strip()
        if not riadok:
            continue
        if riadok[0] in "-+" and otazky:
            otazky[-1]["moznosti"].append(riadok[1:].strip())
            if riadok[0] == "+":
                otazky[-1]["typ"] = TYP_VIAC
        elif riadok[0] in "-+":
            raise ValueError(f"Možnosť „{riadok}“ nepatrí k žiadnej otázke.")
        else:
            otazky.append({"text": riadok, "typ": TYP_JEDNA, "moznosti": []})
    for o in otazky:
        o["moznosti"] = [m for m in o["moznosti"] if m]
        if not o["moznosti"]:
            o["typ"] = TYP_TEXT
        elif len(o["moznosti"]) < 2:
            raise ValueError(f"Otázka „{o['text']}“ musí mať aspoň dve možnosti.")
    if not otazky:
        raise ValueError("Anketa musí mať aspoň jednu otázku.")
    return otazky


def uloz_anketu(db, anketa_id, nazov, uvod, koniec, otazky_text, menit_otazky=True):
    if anketa_id is None:
        cur = db.execute(
            "INSERT INTO anketa (slug, nazov, uvod, koniec, otazky_text) VALUES (?, ?, ?, ?, ?)",
            (vytvor_slug(db, nazov), nazov, uvod, koniec, otazky_text),
        )
        anketa_id = cur.lastrowid
    else:
        db.execute(
            "UPDATE anketa SET nazov = ?, uvod = ?, koniec = ? WHERE id = ?",
            (nazov, uvod, koniec, anketa_id),
        )
    if menit_otazky:
        otazky = rozober_otazky(otazky_text)
        db.execute("DELETE FROM otazka WHERE anketa_id = ?", (anketa_id,))
        db.execute("UPDATE anketa SET otazky_text = ? WHERE id = ?", (otazky_text, anketa_id))
        for i, o in enumerate(otazky):
            cur = db.execute(
                "INSERT INTO otazka (anketa_id, poradie, text, typ) VALUES (?, ?, ?, ?)",
                (anketa_id, i, o["text"], o["typ"]),
            )
            db.executemany(
                "INSERT INTO moznost (otazka_id, poradie, text) VALUES (?, ?, ?)",
                [(cur.lastrowid, j, m) for j, m in enumerate(o["moznosti"])],
            )
    return anketa_id


def nacitaj_otazky(db, anketa_id):
    otazky = []
    for o in db.execute(
        "SELECT * FROM otazka WHERE anketa_id = ? ORDER BY poradie", (anketa_id,)
    ):
        moznosti = db.execute(
            "SELECT * FROM moznost WHERE otazka_id = ? ORDER BY poradie", (o["id"],)
        ).fetchall()
        otazky.append({**dict(o), "moznosti": [dict(m) for m in moznosti]})
    return otazky


def pocet_hlasov(db, anketa_id):
    return db.execute("SELECT COUNT(*) FROM hlas WHERE anketa_id = ?", (anketa_id,)).fetchone()[0]


def aktivne_ankety(db):
    return [a for a in db.execute("SELECT * FROM anketa ORDER BY id DESC") if not je_ukoncena(a)]


def nacitaj_anketu(db, anketa_id):
    anketa = db.execute("SELECT * FROM anketa WHERE id = ?", (anketa_id,)).fetchone()
    if anketa is None:
        abort(404)
    return anketa


# --- hlasovanie -------------------------------------------------------------


@bp.route("/<slug>", methods=["GET", "POST"])
def hlasovanie(slug):
    db = get_db()
    anketa = db.execute("SELECT * FROM anketa WHERE slug = ?", (slug,)).fetchone()
    if anketa is None:
        abort(404)
    otazky = nacitaj_otazky(db, anketa["id"])
    if je_ukoncena(anketa):
        return render_template("anketa/sprava.html", anketa=anketa, nadpis="Anketa je ukončená",
                               text="Hlasovanie v tejto ankete sa už skončilo.")

    chyby, vyplnene = [], request.form
    if request.method == "POST":
        cislo = request.form.get("cislo", "").strip()
        clen = db.execute("SELECT * FROM clen WHERE cislo = ?", (cislo,)).fetchone()
        if not cislo:
            chyby.append("Zadajte svoje odborárske číslo.")
        elif clen is None:
            chyby.append("Toto odborárske číslo nie je v zozname platných členov.")
        elif db.execute(
            "SELECT 1 FROM hlas WHERE anketa_id = ? AND cislo = ?", (anketa["id"], cislo)
        ).fetchone():
            return render_template("anketa/sprava.html", anketa=anketa, nadpis="Už ste hlasovali",
                                   text=f"S odborárskym číslom {cislo} už bolo v tejto ankete hlasované. "
                                        "Hlasovať je možné iba raz.")

        odpovede = []
        for o in otazky:
            pole = f"otazka_{o['id']}"
            platne = {str(m["id"]) for m in o["moznosti"]}
            if o["typ"] == TYP_TEXT:
                text = request.form.get(pole, "").strip()
                if text:
                    odpovede.append((o["id"], None, text[:2000]))
            else:
                vybrane = [v for v in request.form.getlist(pole) if v in platne]
                if o["typ"] == TYP_JEDNA:
                    vybrane = vybrane[:1]
                if not vybrane:
                    chyby.append(f"Odpovedzte na otázku „{o['text']}“.")
                odpovede += [(o["id"], int(v), "") for v in vybrane]

        if not chyby:
            try:
                cur = db.execute(
                    "INSERT INTO hlas (anketa_id, cislo, cas) VALUES (?, ?, ?)",
                    (anketa["id"], cislo, datetime.now(CASOVA_ZONA).strftime("%Y-%m-%d %H:%M:%S")),
                )
            except Exception:
                db.rollback()
                return render_template("anketa/sprava.html", anketa=anketa, nadpis="Už ste hlasovali",
                                       text="S týmto odborárskym číslom už bolo hlasované.")
            db.executemany(
                "INSERT INTO odpoved (hlas_id, otazka_id, moznost_id, text) VALUES (?, ?, ?, ?)",
                [(cur.lastrowid, *o) for o in odpovede],
            )
            db.commit()
            return render_template("anketa/sprava.html", anketa=anketa, nadpis="Ďakujeme za hlas",
                                   text="Váš hlas bol zaznamenaný.")

    return render_template("anketa/hlasovanie.html", anketa=anketa, otazky=otazky,
                           chyby=chyby, vyplnene=vyplnene)


# --- správca ----------------------------------------------------------------


def len_spravca(f):
    @wraps(f)
    def obal(*args, **kwargs):
        if not session.get("spravca"):
            return redirect(url_for("anketa.prihlasenie", dalej=request.path))
        return f(*args, **kwargs)
    return obal


@bp.route("/spravca/prihlasenie", methods=["GET", "POST"])
def prihlasenie():
    db = get_db()
    heslo_hash = nastavenie(db, "heslo_spravcu")
    if request.method == "POST" and heslo_hash:
        if check_password_hash(heslo_hash, request.form.get("heslo", "")):
            session["spravca"] = True
            dalej = request.args.get("dalej", "")
            return redirect(dalej if dalej.startswith(("/anketa/spravca", "/spravca")) else url_for("anketa.spravca"))
        flash("Nesprávne heslo.")
    return render_template("anketa/prihlasenie.html", heslo_nastavene=bool(heslo_hash))


@bp.route("/spravca/odhlasenie", methods=["POST"])
def odhlasenie():
    session.pop("spravca", None)
    return redirect(url_for("index"))


@bp.route("/spravca")
@len_spravca
def spravca():
    db = get_db()
    ankety = [
        {**dict(a), "hlasov": pocet_hlasov(db, a["id"]), "ukoncena": je_ukoncena(a)}
        for a in db.execute("SELECT * FROM anketa ORDER BY id DESC")
    ]
    pocet_clenov = db.execute("SELECT COUNT(*) FROM clen").fetchone()[0]
    return render_template("anketa/spravca.html", ankety=ankety, pocet_clenov=pocet_clenov)


@bp.route("/spravca/clenovia", methods=["POST"])
@len_spravca
def nahraj_clenov():
    subor = request.files.get("subor")
    if not subor or not subor.filename:
        flash("Vyberte súbor so zoznamom členov.")
        return redirect(url_for("anketa.spravca"))
    data = subor.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1250", errors="replace")
    oddelovac = ";" if text.count(";") >= text.count(",") else ","
    clenovia = {}
    for riadok in csv.reader(io.StringIO(text), delimiter=oddelovac):
        if not riadok:
            continue
        cislo = riadok[0].strip()
        if not cislo or not any(z.isdigit() for z in cislo):
            continue  # prázdny riadok alebo hlavička
        meno = " ".join(s.strip() for s in riadok[1:] if s.strip())
        clenovia[cislo] = meno
    if not clenovia:
        flash("V súbore sa nenašlo žiadne odborárske číslo. Čísla musia byť v prvom stĺpci.")
        return redirect(url_for("anketa.spravca"))
    db = get_db()
    db.execute("DELETE FROM clen")
    db.executemany("INSERT INTO clen (cislo, meno) VALUES (?, ?)", clenovia.items())
    db.commit()
    flash(f"Zoznam členov bol nahradený. Počet platných čísel: {len(clenovia)}.")
    return redirect(url_for("anketa.spravca"))


@bp.route("/spravca/nova", methods=["GET", "POST"])
@bp.route("/spravca/<int:anketa_id>/upravit", methods=["GET", "POST"])
@len_spravca
def uprav_anketu(anketa_id=None):
    db = get_db()
    anketa = nacitaj_anketu(db, anketa_id) if anketa_id else None
    ma_hlasy = bool(anketa_id) and pocet_hlasov(db, anketa_id) > 0
    if request.method == "POST":
        f = request.form
        nazov = f.get("nazov", "").strip()
        try:
            if not nazov:
                raise ValueError("Zadajte názov ankety.")
            koniec = f.get("koniec", "").strip()
            if koniec:
                datetime.strptime(koniec, "%Y-%m-%dT%H:%M")
            anketa_id = uloz_anketu(db, anketa_id, nazov, f.get("uvod", "").strip(), koniec,
                                    f.get("otazky", ""), menit_otazky=not ma_hlasy)
        except ValueError as e:
            db.rollback()
            flash(str(e) if "does not match" not in str(e) else "Neplatný dátum ukončenia.")
            return render_template("anketa/uprava.html", anketa=f, ma_hlasy=ma_hlasy, nova=anketa is None)
        db.commit()
        flash("Anketa bola uložená.")
        return redirect(url_for("anketa.spravca"))
    if anketa is None:
        anketa = {"nazov": "", "uvod": PREDVOLENY_UVOD, "koniec": "", "otazky": ""}
    else:
        anketa = {**dict(anketa), "otazky": anketa["otazky_text"]}
    return render_template("anketa/uprava.html", anketa=anketa, ma_hlasy=ma_hlasy, nova=anketa_id is None)


@bp.route("/spravca/<int:anketa_id>/zmazat", methods=["POST"])
@len_spravca
def zmaz_anketu(anketa_id):
    db = get_db()
    nacitaj_anketu(db, anketa_id)
    db.execute("DELETE FROM anketa WHERE id = ?", (anketa_id,))
    db.commit()
    flash("Anketa bola zmazaná.")
    return redirect(url_for("anketa.spravca"))


def vysledky_ankety(db, anketa_id):
    otazky = nacitaj_otazky(db, anketa_id)
    hlasov = pocet_hlasov(db, anketa_id)
    for o in otazky:
        for m in o["moznosti"]:
            m["pocet"] = db.execute(
                "SELECT COUNT(*) FROM odpoved WHERE moznost_id = ?", (m["id"],)
            ).fetchone()[0]
            m["percent"] = round(100 * m["pocet"] / hlasov, 1) if hlasov else 0
        o["texty"] = [r["text"] for r in db.execute(
            "SELECT text FROM odpoved WHERE otazka_id = ? AND moznost_id IS NULL ORDER BY id",
            (o["id"],),
        )]
    hlasujuci = []
    for h in db.execute(
        "SELECT h.*, COALESCE(c.meno, '') AS meno FROM hlas h "
        "LEFT JOIN clen c ON c.cislo = h.cislo WHERE h.anketa_id = ? ORDER BY h.cas",
        (anketa_id,),
    ):
        odpovede = {}
        for r in db.execute(
            "SELECT o.otazka_id, COALESCE(m.text, o.text) AS text FROM odpoved o "
            "LEFT JOIN moznost m ON m.id = o.moznost_id WHERE o.hlas_id = ? "
            "ORDER BY m.poradie",
            (h["id"],),
        ):
            odpovede.setdefault(r["otazka_id"], []).append(r["text"])
        hlasujuci.append({**dict(h), "odpovede": {k: ", ".join(v) for k, v in odpovede.items()}})
    return otazky, hlasov, hlasujuci


@bp.route("/spravca/<int:anketa_id>/vysledky")
@len_spravca
def vysledky(anketa_id):
    db = get_db()
    anketa = nacitaj_anketu(db, anketa_id)
    otazky, hlasov, hlasujuci = vysledky_ankety(db, anketa_id)
    return render_template("anketa/vysledky.html", anketa=anketa, otazky=otazky, hlasov=hlasov,
                           hlasujuci=hlasujuci, ukoncena=je_ukoncena(anketa))


@bp.route("/spravca/<int:anketa_id>/export.csv")
@len_spravca
def export(anketa_id):
    db = get_db()
    anketa = nacitaj_anketu(db, anketa_id)
    otazky, hlasov, hlasujuci = vysledky_ankety(db, anketa_id)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow([anketa["nazov"]])
    w.writerow(["Počet hlasujúcich", hlasov])
    w.writerow([])
    w.writerow(["Súhrn"])
    for o in otazky:
        w.writerow([o["text"]])
        if o["typ"] == TYP_TEXT:
            for t in o["texty"]:
                w.writerow(["", t])
        else:
            w.writerow(["Možnosť", "Počet", "Percent"])
            for m in o["moznosti"]:
                w.writerow([m["text"], m["pocet"], str(m["percent"]).replace(".", ",")])
        w.writerow([])
    w.writerow(["Hlasy jednotlivých členov"])
    w.writerow(["Odborárske číslo", "Meno", "Čas hlasovania"] + [o["text"] for o in otazky])
    for h in hlasujuci:
        w.writerow([h["cislo"], h["meno"], h["cas"]] + [h["odpovede"].get(o["id"], "") for o in otazky])
    nazov_suboru = f"vysledky-{anketa['slug']}.csv"
    # BOM na začiatku, aby Excel správne zobrazil diakritiku.
    return Response(
        "﻿" + out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nazov_suboru}"'},
    )
