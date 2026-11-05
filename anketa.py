"""Ankety pre členov: hlasovanie s overením odborárskeho čísla a správa ankiet.

Ankety nie sú anonymné: pri každom hlase sa ukladá odborárske číslo
a správca vo výsledkoch vidí, kto ako hlasoval.
"""
import csv
import hashlib
import hmac
import io
import json
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
    g,
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
    "Dotazník môžete vyplniť len raz. Kým ho nepotvrdíte a neodošlete, odpovede môžete meniť.\n\n"
    "Pri otázkach označených ako anonymné nikto neuvidí, ako ste odpovedali, zobrazí sa len súhrn. "
    "Pri ostatných otázkach vedenie ZO uvidí aj vaše odpovede. Údaje slúžia iba na vyhodnotenie "
    "tohto dotazníka v rámci odborovej organizácie a nebudú poskytnuté tretím stranám."
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
    stlpce = [r["name"] for r in db.execute("PRAGMA table_info(clen)")]
    if "preukaz_hash" not in stlpce:
        db.execute("ALTER TABLE clen ADD COLUMN preukaz_hash TEXT NOT NULL DEFAULT ''")
    if "zo_slug" not in [r["name"] for r in db.execute("PRAGMA table_info(anketa)")]:
        # Anketa len pre členov jednej ZO (prázdne = pre všetkých).
        db.execute("ALTER TABLE anketa ADD COLUMN zo_slug TEXT NOT NULL DEFAULT ''")
    if "anonymna" not in [r["name"] for r in db.execute("PRAGMA table_info(otazka)")]:
        # Pri anonymnej otázke sa vo výsledkoch neukazuje, kto ako odpovedal.
        db.execute("ALTER TABLE otazka ADD COLUMN anonymna INTEGER NOT NULL DEFAULT 0")
    if "potvrdene" not in [r["name"] for r in db.execute("PRAGMA table_info(hlas)")]:
        # 0 = rozpracovaný dotazník (člen ho ešte môže meniť), 1 = potvrdený a uzavretý.
        db.execute("ALTER TABLE hlas ADD COLUMN potvrdene INTEGER NOT NULL DEFAULT 1")
    if "anonymna" not in [r["name"] for r in db.execute("PRAGMA table_info(odpoved)")]:
        # Člen si môže zvoliť, že jeho odpoveď na otázku bude anonymná.
        db.execute("ALTER TABLE odpoved ADD COLUMN anonymna INTEGER NOT NULL DEFAULT 0")
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


def normalizuj_preukaz(cislo):
    return re.sub(r"\s+", "", cislo).upper()


def hash_preukazu(db, cislo_preukazu):
    """Číslo preukazu sa neukladá čitateľne, len ako HMAC s tajným kľúčom stránky."""
    kluc = secret_key(db).encode()
    return hmac.new(kluc, normalizuj_preukaz(cislo_preukazu).encode(), hashlib.sha256).hexdigest()


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


ANONYMNA_ZNACKA = re.compile(r"\[\s*anonym\w*\s*\]", re.I)


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
            anonymna = bool(ANONYMNA_ZNACKA.search(riadok))
            riadok = ANONYMNA_ZNACKA.sub("", riadok).strip()
            otazky.append({"text": riadok, "typ": TYP_JEDNA, "moznosti": [], "anonymna": anonymna})
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
                "INSERT INTO otazka (anketa_id, poradie, text, typ, anonymna) VALUES (?, ?, ?, ?, ?)",
                (anketa_id, i, o["text"], o["typ"], int(o["anonymna"])),
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


def je_clenom_zo(db, cislo, zo_slug):
    """Člen s týmto odborárskym číslom je v evidencii danej ZO."""
    cislo = re.sub(r"\s+", "", cislo)
    for (u,) in db.execute("SELECT udaje FROM evidencia_clen WHERE zo_slug = ? AND udaje LIKE ?",
                           (zo_slug, f"%{cislo}%")):
        if re.sub(r"\s+", "", json.loads(u).get("Identifikačné číslo", "")) == cislo:
            return True
    return False


def mesto_zo(db, zo_slug):
    r = db.execute("SELECT mesto FROM zakladna_organizacia WHERE slug = ?", (zo_slug,)).fetchone() if zo_slug else None
    return r["mesto"] if r else ""


def nacitaj_anketu(db, anketa_id):
    anketa = db.execute("SELECT * FROM anketa WHERE id = ?", (anketa_id,)).fetchone()
    if anketa is None:
        abort(404)
    return anketa


# --- hlasovanie -------------------------------------------------------------


@bp.route("/<slug>")
def hlasovanie(slug):
    """Odkaz na anketu: dotazník sa vypĺňa po prihlásení vo vlastnom konte."""
    anketa = get_db().execute("SELECT id FROM anketa WHERE slug = ?", (slug,)).fetchone()
    if anketa is None:
        abort(404)
    return redirect(url_for("anketa.dotaznik", anketa_id=anketa["id"]))


# --- dotazník v konte člena -------------------------------------------------


def zo_clena(db, cislo):
    """ZO, v ktorých evidencii je člen s týmto odborárskym číslom."""
    cislo = re.sub(r"\s+", "", cislo or "")
    if not cislo:
        return set()
    return {r["zo_slug"] for r in db.execute("SELECT zo_slug, udaje FROM evidencia_clen WHERE udaje LIKE ?",
                                             (f"%{cislo}%",))
            if re.sub(r"\s+", "", json.loads(r["udaje"]).get("Identifikačné číslo", "")) == cislo}


def moze_vyplnit(db, anketa, cislo):
    return not anketa["zo_slug"] or anketa["zo_slug"] in zo_clena(db, cislo)


def dotazniky_clena(db, cislo):
    """Dotazníky pre člena: otvorené aj tie, ktoré už odoslal. [{anketa, stav, ukoncena, zo_mesto}]"""
    moje_zo = zo_clena(db, cislo)
    vysledok = []
    for a in db.execute("SELECT * FROM anketa ORDER BY id DESC"):
        if a["zo_slug"] and a["zo_slug"] not in moje_zo:
            continue
        h = db.execute("SELECT potvrdene FROM hlas WHERE anketa_id = ? AND cislo = ?", (a["id"], cislo)).fetchone()
        stav = "nevyplneny" if h is None else ("odoslany" if h["potvrdene"] else "rozpracovany")
        if je_ukoncena(a) and stav != "odoslany":
            continue
        vysledok.append({"anketa": a, "stav": stav, "ukoncena": je_ukoncena(a), "zo_mesto": mesto_zo(db, a["zo_slug"])})
    return vysledok


def moje_odpovede(db, hlas_id):
    """({otazka_id: [id možnosti alebo text]}, {otazka_id, na ktoré člen odpovedal anonymne})"""
    odp, anonymne = {}, set()
    for r in db.execute("SELECT otazka_id, moznost_id, text, anonymna FROM odpoved WHERE hlas_id = ?", (hlas_id,)):
        odp.setdefault(r["otazka_id"], []).append(r["moznost_id"] if r["moznost_id"] is not None else r["text"])
        if r["anonymna"]:
            anonymne.add(r["otazka_id"])
    return odp, anonymne


def text_odpovedi(anketa, otazky, odpovede):
    riadky = [anketa["nazov"], ""]
    for o in otazky:
        hodnoty = odpovede.get(o["id"], [])
        if o["typ"] == TYP_TEXT:
            odpoved = hodnoty[0] if hodnoty else "–"
        else:
            odpoved = ", ".join(m["text"] for m in o["moznosti"] if m["id"] in hodnoty) or "–"
        riadky += [o["text"], "  " + odpoved, ""]
    return "\n".join(riadky)


@bp.route("/dotaznik/<int:anketa_id>", methods=["GET", "POST"])
def dotaznik(anketa_id):
    clen = g.get("clen")
    if clen is None:
        return redirect(url_for("ucet.prihlasenie", dalej=request.path))
    db = get_db()
    anketa = nacitaj_anketu(db, anketa_id)
    cislo = clen["cislo"]
    if not moze_vyplnit(db, anketa, cislo):
        return render_template("anketa/sprava.html", anketa=anketa, nadpis="Dotazník nie je pre vás",
                               text=f"Tento dotazník je len pre členov ZO {mesto_zo(db, anketa['zo_slug'])}.")
    otazky = nacitaj_otazky(db, anketa_id)
    hlas = db.execute("SELECT * FROM hlas WHERE anketa_id = ? AND cislo = ?", (anketa_id, cislo)).fetchone()
    odpovede, anonymne = moje_odpovede(db, hlas["id"]) if hlas else ({}, set())
    uzavrety = bool(hlas and hlas["potvrdene"])
    ukoncena = je_ukoncena(anketa)
    chyby = []

    if request.method == "POST":
        akcia = request.form.get("akcia")
        if akcia == "email":
            email = clen.get("email", "")
            if not uzavrety:
                flash("E-mailom si môžete poslať až odoslaný dotazník.")
            elif not email:
                flash("V konte nemáte e-mail.")
            else:
                from posta import je_nastavena, posli
                if not je_nastavena(db):
                    flash("Odosielanie e-mailov zatiaľ nie je na stránke nastavené.")
                else:
                    try:
                        posli(db, email, f"Vaše odpovede: {anketa['nazov']}", text_odpovedi(anketa, otazky, odpovede))
                        flash(f"Odpovede boli poslané na {email}.")
                    except Exception:
                        flash("E-mail sa nepodarilo odoslať, skúste to neskôr.")
            return redirect(url_for("anketa.dotaznik", anketa_id=anketa_id))
        if uzavrety or ukoncena:
            flash("Dotazník je už uzavretý, odpovede sa nedajú meniť.")
            return redirect(url_for("anketa.dotaznik", anketa_id=anketa_id))
        nove = []
        anonymne = {o["id"] for o in otazky if request.form.get(f"anonymne_{o['id']}") and not o["anonymna"]}
        for o in otazky:
            pole = f"otazka_{o['id']}"
            if o["typ"] == TYP_TEXT:
                text = request.form.get(pole, "").strip()
                if text:
                    nove.append((o["id"], None, text[:2000]))
            else:
                platne = {str(m["id"]) for m in o["moznosti"]}
                vybrane = [v for v in request.form.getlist(pole) if v in platne]
                if o["typ"] == TYP_JEDNA:
                    vybrane = vybrane[:1]
                if not vybrane and akcia == "potvrdit":
                    chyby.append(f"Odpovedzte na otázku „{o['text']}“.")
                nove += [(o["id"], int(v), "") for v in vybrane]
        odpovede = {}
        for oid, mid, text in nove:
            odpovede.setdefault(oid, []).append(mid if mid is not None else text)
        if not chyby:
            cas = datetime.now(CASOVA_ZONA).strftime("%Y-%m-%d %H:%M:%S")
            potvrdit = int(akcia == "potvrdit")
            if hlas is None:
                hlas_id = db.execute("INSERT INTO hlas (anketa_id, cislo, cas, potvrdene) VALUES (?, ?, ?, ?)",
                                     (anketa_id, cislo, cas, potvrdit)).lastrowid
            else:
                hlas_id = hlas["id"]
                db.execute("UPDATE hlas SET cas = ?, potvrdene = ? WHERE id = ?", (cas, potvrdit, hlas_id))
                db.execute("DELETE FROM odpoved WHERE hlas_id = ?", (hlas_id,))
            db.executemany("INSERT INTO odpoved (hlas_id, otazka_id, moznost_id, text, anonymna) VALUES (?, ?, ?, ?, ?)",
                           [(hlas_id, *o, int(o[0] in anonymne)) for o in nove])
            db.commit()
            flash("Dotazník bol odoslaný a uzavretý. Ďakujeme." if potvrdit
                  else "Odpovede sú uložené. Dotazník môžete ešte meniť, nakoniec ho potvrďte a odošlite.")
            return redirect(url_for("anketa.dotaznik", anketa_id=anketa_id))

    return render_template("anketa/dotaznik.html", anketa=anketa, otazky=otazky, odpovede=odpovede,
                           uzavrety=uzavrety, ukoncena=ukoncena, chyby=chyby, hlas=hlas, anonymne=anonymne,
                           zo_mesto=mesto_zo(db, anketa["zo_slug"]))


# --- správca ----------------------------------------------------------------


def je_spravca():
    """Správca: prihlásený heslom správcu alebo člen s prístupom „správca stránky“."""
    clen = g.get("clen")
    return bool(session.get("spravca")) or (clen is not None and clen["rola"] == "spravca")


def spravovana_zo():
    """ZO, ktorej dotazníky spravuje prihlásený predseda alebo podpredseda ZO."""
    clen = g.get("clen")
    if clen is not None and clen["rola"] == "zo" and clen.get("zo_slug"):
        return clen["zo_slug"]
    return None


def moze_spravovat(zo_slug):
    return je_spravca() or (bool(zo_slug) and zo_slug == spravovana_zo())


def spat_na_zoznam(zo_slug):
    return url_for("anketa.spravca") if je_spravca() else url_for("anketa.dotazniky_zo", slug=zo_slug)


def len_spravca(f):
    @wraps(f)
    def obal(*args, **kwargs):
        if not je_spravca():
            return redirect(url_for("anketa.prihlasenie", dalej=request.path))
        return f(*args, **kwargs)
    return obal


@bp.route("/spravca/prihlasenie", methods=["GET", "POST"])
def prihlasenie():
    if je_spravca():
        return redirect(url_for("anketa.spravca"))
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
        {**dict(a), "hlasov": pocet_hlasov(db, a["id"]), "ukoncena": je_ukoncena(a), "zo_mesto": mesto_zo(db, a["zo_slug"])}
        for a in db.execute("SELECT * FROM anketa ORDER BY id DESC")
    ]
    pocet_clenov = db.execute("SELECT COUNT(*) FROM clen").fetchone()[0]
    return render_template("anketa/spravca.html", ankety=ankety, pocet_clenov=pocet_clenov)


@bp.route("/zo/<slug>")
def dotazniky_zo(slug):
    """Dotazníky jednej ZO pre jej predsedu a podpredsedu (aj pre správcu)."""
    if g.get("clen") is None and not je_spravca():
        return redirect(url_for("ucet.prihlasenie", dalej=request.path))
    if not moze_spravovat(slug):
        abort(403)
    db = get_db()
    mesto = mesto_zo(db, slug)
    if not mesto:
        abort(404)
    ankety = [{**dict(a), "hlasov": pocet_odoslanych(db, a["id"]), "ukoncena": je_ukoncena(a)}
              for a in db.execute("SELECT * FROM anketa WHERE zo_slug = ? ORDER BY id DESC", (slug,))]
    return render_template("anketa/zo.html", ankety=ankety, slug=slug, mesto=mesto)


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
        meno = riadok[1].strip() if len(riadok) > 1 else ""
        preukaz = normalizuj_preukaz(riadok[2]) if len(riadok) > 2 else ""
        clenovia[cislo] = (meno, preukaz)
    if not clenovia:
        flash("V súbore sa nenašlo žiadne odborárske číslo. Čísla musia byť v prvom stĺpci.")
        return redirect(url_for("anketa.spravca"))
    db = get_db()
    db.execute("DELETE FROM clen")
    db.executemany(
        "INSERT INTO clen (cislo, meno, preukaz_hash) VALUES (?, ?, ?)",
        [(c, m, hash_preukazu(db, p) if p else "") for c, (m, p) in clenovia.items()],
    )
    db.commit()
    import evidencia
    evidencia.synchronizuj_prihlasovanie(db)
    bez_preukazu = sum(1 for m, p in clenovia.values() if not p)
    sprava = f"Zoznam členov bol nahradený. Počet platných čísel: {len(clenovia)}."
    if bez_preukazu:
        sprava += f" Bez čísla preukazu (nemôžu sa prihlásiť): {bez_preukazu}."
    flash(sprava)
    return redirect(url_for("anketa.spravca"))


@bp.route("/spravca/nova", methods=["GET", "POST"])
@bp.route("/spravca/<int:anketa_id>/upravit", methods=["GET", "POST"])
def uprav_anketu(anketa_id=None):
    db = get_db()
    anketa = nacitaj_anketu(db, anketa_id) if anketa_id else None
    # Predseda / podpredseda ZO spravuje len dotazníky svojej ZO.
    pevna_zo = None if je_spravca() else spravovana_zo()
    if not je_spravca():
        if pevna_zo is None:
            if g.get("clen") is None:
                return redirect(url_for("anketa.prihlasenie", dalej=request.path))
            abort(403)
        if anketa is not None and anketa["zo_slug"] != pevna_zo:
            abort(403)
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
            zo_slug = pevna_zo or f.get("zo_slug", "")
            db.execute("UPDATE anketa SET zo_slug = ? WHERE id = ?",
                       (zo_slug if mesto_zo(db, zo_slug) else "", anketa_id))
        except ValueError as e:
            db.rollback()
            flash(str(e) if "does not match" not in str(e) else "Neplatný dátum ukončenia.")
            return render_template("anketa/uprava.html", anketa=f, ma_hlasy=ma_hlasy, nova=anketa is None,
                                   organizacie=_organizacie(db), pevna_zo=pevna_zo, spat=spat_na_zoznam(pevna_zo))
        db.commit()
        flash("Dotazník bol uložený.")
        return redirect(spat_na_zoznam(pevna_zo))
    if anketa is None:
        anketa = {"nazov": "", "uvod": PREDVOLENY_UVOD, "koniec": "", "otazky": "", "zo_slug": pevna_zo or ""}
    else:
        anketa = {**dict(anketa), "otazky": anketa["otazky_text"]}
    return render_template("anketa/uprava.html", anketa=anketa, ma_hlasy=ma_hlasy, nova=anketa_id is None,
                           organizacie=_organizacie(db), pevna_zo=pevna_zo, spat=spat_na_zoznam(pevna_zo))


def _organizacie(db):
    from evidencia import abecedne
    return sorted(db.execute("SELECT slug, mesto FROM zakladna_organizacia").fetchall(), key=lambda z: abecedne(z["mesto"]))


@bp.route("/spravca/<int:anketa_id>/zmazat", methods=["POST"])
def zmaz_anketu(anketa_id):
    db = get_db()
    anketa = _anketa_na_spravu(db, anketa_id)
    db.execute("DELETE FROM anketa WHERE id = ?", (anketa_id,))
    db.commit()
    flash("Dotazník bol zmazaný.")
    return redirect(spat_na_zoznam(anketa["zo_slug"]))


def pocet_odoslanych(db, anketa_id):
    return db.execute("SELECT COUNT(*) FROM hlas WHERE anketa_id = ? AND potvrdene = 1", (anketa_id,)).fetchone()[0]


def vysledky_ankety(db, anketa_id):
    """Súhrn len z odoslaných (potvrdených) dotazníkov; pri anonymných otázkach bez mien."""
    otazky = nacitaj_otazky(db, anketa_id)
    hlasov = pocet_odoslanych(db, anketa_id)
    for o in otazky:
        for m in o["moznosti"]:
            m["pocet"] = db.execute(
                "SELECT COUNT(*) FROM odpoved d JOIN hlas h ON h.id = d.hlas_id "
                "WHERE d.moznost_id = ? AND h.potvrdene = 1", (m["id"],)
            ).fetchone()[0]
            m["percent"] = round(100 * m["pocet"] / hlasov, 1) if hlasov else 0
        texty = [r["text"] for r in db.execute(
            "SELECT d.text FROM odpoved d JOIN hlas h ON h.id = d.hlas_id "
            "WHERE d.otazka_id = ? AND d.moznost_id IS NULL AND h.potvrdene = 1 ORDER BY d.id",
            (o["id"],),
        )]
        o["texty"] = sorted(texty, key=str.lower) if o["anonymna"] else texty
    hlasujuci = []
    for h in db.execute(
        "SELECT h.*, COALESCE(c.meno, '') AS meno FROM hlas h "
        "LEFT JOIN clen c ON c.cislo = h.cislo WHERE h.anketa_id = ? AND h.potvrdene = 1 ORDER BY h.cas",
        (anketa_id,),
    ):
        odpovede, skryte = {}, set()
        for r in db.execute(
            "SELECT o.otazka_id, COALESCE(m.text, o.text) AS text, o.anonymna FROM odpoved o "
            "LEFT JOIN moznost m ON m.id = o.moznost_id WHERE o.hlas_id = ? "
            "ORDER BY m.poradie",
            (h["id"],),
        ):
            odpovede.setdefault(r["otazka_id"], []).append(r["text"])
            if r["anonymna"]:
                skryte.add(r["otazka_id"])
        anonymne = {o["id"] for o in otazky if o["anonymna"]}
        hlasujuci.append({**dict(h), "odpovede": {k: ("anonymne" if k in skryte else ", ".join(v))
                                                  for k, v in odpovede.items() if k not in anonymne}})
    return otazky, hlasov, hlasujuci


def ucast(db, anketa):
    """(oprávnených členov, rozpracovaných dotazníkov)"""
    if anketa["zo_slug"]:
        opravnenych = db.execute("SELECT COUNT(*) FROM evidencia_clen WHERE zo_slug = ?", (anketa["zo_slug"],)).fetchone()[0]
    else:
        opravnenych = db.execute("SELECT COUNT(*) FROM clen").fetchone()[0]
    rozpracovanych = db.execute("SELECT COUNT(*) FROM hlas WHERE anketa_id = ? AND potvrdene = 0",
                                (anketa["id"],)).fetchone()[0]
    return opravnenych, rozpracovanych


def _anketa_na_spravu(db, anketa_id):
    anketa = nacitaj_anketu(db, anketa_id)
    if not moze_spravovat(anketa["zo_slug"]):
        abort(403)
    return anketa


@bp.route("/spravca/<int:anketa_id>/vysledky")
def vysledky(anketa_id):
    db = get_db()
    anketa = _anketa_na_spravu(db, anketa_id)
    otazky, hlasov, hlasujuci = vysledky_ankety(db, anketa_id)
    opravnenych, rozpracovanych = ucast(db, anketa)
    return render_template("anketa/vysledky.html", anketa=anketa, otazky=otazky, hlasov=hlasov,
                           hlasujuci=hlasujuci, ukoncena=je_ukoncena(anketa), opravnenych=opravnenych,
                           rozpracovanych=rozpracovanych, zo_mesto=mesto_zo(db, anketa["zo_slug"]),
                           spat=spat_na_zoznam(anketa["zo_slug"]))


@bp.route("/spravca/<int:anketa_id>/export.csv")
def export(anketa_id):
    db = get_db()
    anketa = _anketa_na_spravu(db, anketa_id)
    otazky, hlasov, hlasujuci = vysledky_ankety(db, anketa_id)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow([anketa["nazov"]])
    w.writerow(["Počet odoslaných dotazníkov", hlasov])
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
    menovite = [o for o in otazky if not o["anonymna"]]
    w.writerow(["Odborárske číslo", "Meno", "Čas odoslania"] + [o["text"] for o in menovite])
    for h in hlasujuci:
        w.writerow([h["cislo"], h["meno"], h["cas"]] + [h["odpovede"].get(o["id"], "") for o in menovite])
    nazov_suboru = f"vysledky-{anketa['slug']}.csv"
    # BOM na začiatku, aby Excel správne zobrazil diakritiku.
    return Response(
        "﻿" + out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nazov_suboru}"'},
    )
