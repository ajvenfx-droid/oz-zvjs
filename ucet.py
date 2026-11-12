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
    Response,
    abort,
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
from evidencia import abecedne
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
    "spravca": "správca stránky – môže všetko",
}
# Voľby v správe kont: predsedu a podpredsedu ZO prideľuje správca samostatne.
VOLBY_PRISTUPU = {
    "clen": ROLE["clen"],
    "predseda": "predseda ZO – mení členov svojej ZO",
    "podpredseda": "podpredseda ZO – mení členov svojej ZO",
    "vedenie": ROLE["vedenie"],
    "spravca": ROLE["spravca"],
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
    if "funkcia" not in stlpce:
        db.execute("ALTER TABLE ucet ADD COLUMN funkcia TEXT NOT NULL DEFAULT ''")
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
    if not heslo.strip():
        return False
    ulozene = [x for x in (clen["preukaz_hash"], clen["sluzobne_hash"]) if x]
    h = re.sub(r"\s+", "", heslo)
    varianty = {h, h.lstrip("0"), "0" + h}  # Excel často zje úvodnú nulu
    return any(hmac.compare_digest(u, hash_preukazu(db, v)) for u in ulozene for v in varianty if v)


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
        "COALESCE(u.heslo_hash, '') != '' AS vlastne_heslo, "
        "CASE WHEN COALESCE(u.email, '') != '' THEN u.email ELSE COALESCE(c.email, '') END AS email, "
        "COALESCE(u.zo_slug, '') AS zo_slug, COALESCE(u.funkcia, '') AS funkcia "
        "FROM clen c LEFT JOIN ucet u ON u.cislo = c.cislo WHERE c.cislo = ?",
        (cislo,),
    ).fetchone()
    if row is None:
        session.pop("clen", None)
        return
    clen = dict(row)
    clen["skutocna_rola"] = clen["rola"]
    # Predseda ZO, predsedníctvo či správca si môže prepnúť zobrazenie „ako člen“.
    if clen["rola"] == "clen" and clen["funkcia"]:
        clen["rola"] = "zo"
        clen["skutocna_rola"] = "zo"
    clen["skutocna_funkcia"] = clen["funkcia"]
    zo = get_db().execute("SELECT mesto FROM zakladna_organizacia WHERE slug = ?", (clen["zo_slug"],)).fetchone() \
        if clen["zo_slug"] else None
    clen["zo_mesto"] = zo["mesto"] if zo else ""
    clen["ako_clen"] = False
    clen["simulovany_organ"] = None
    clen["zobrazenie"] = "clen" if session.get("ako_clen") else ""
    # Správca (konto so správcom alebo prihlásenie heslom správcu) si môže pozrieť stránku očami inej funkcie.
    clen["moze_simulovat"] = clen["skutocna_rola"] == "spravca" or bool(session.get("spravca"))
    zobrazenie = session.get("zobrazenie_spravcu") if clen["moze_simulovat"] else None
    if zobrazenie in ZOBRAZENIA_SPRAVCU:
        # Správca si môže pozrieť stránku tak, ako ju vidí predseda, podpredseda, revízna komisia či výbor ZO.
        slug, mesto = clen["zo_slug"], clen["zo_mesto"]
        if not slug:
            z = next(iter(moje_udaje(get_db(), cislo)), None)
            slug, mesto = (z["zo_slug"], z["mesto"]) if z else ("", "")
        if slug:
            clen["zobrazenie"] = zobrazenie
            clen["zo_slug"], clen["zo_mesto"] = slug, mesto
            if zobrazenie in ("predseda", "podpredseda"):
                clen["rola"], clen["funkcia"] = "zo", zobrazenie
            else:
                clen["rola"], clen["funkcia"] = "clen", ""
                clen["simulovany_organ"] = (slug, "rk_clen1" if zobrazenie == "rk" else "vybor")
            g.clen = clen
            return
    if clen["rola"] != "clen" and session.get("ako_clen"):
        clen["ako_clen"] = True
        clen["rola"] = "clen"
        clen["funkcia"] = ""
    g.clen = clen


ZOBRAZENIA_SPRAVCU = {"predseda": "predseda ZO", "podpredseda": "podpredseda ZO",
                      "rk": "člen revíznej komisie ZO", "vybor": "člen výboru ZO"}


@bp.route("/moje-konto/zobrazenie", methods=["POST"])
def prepni_zobrazenie():
    if g.get("clen") is None:
        return redirect(url_for("ucet.prihlasenie"))
    zobrazenie = request.form.get("zobrazenie")
    session.pop("zobrazenie_spravcu", None)
    if zobrazenie == "clen":
        session["ako_clen"] = True
    else:
        session.pop("ako_clen", None)
        if zobrazenie in ZOBRAZENIA_SPRAVCU and g.clen["moze_simulovat"]:
            session["zobrazenie_spravcu"] = zobrazenie
    dalej = request.form.get("dalej", "")
    if not dalej.startswith("/") or dalej.startswith("//") or (zobrazenie != "spravca" and dalej.startswith(("/spravca", "/anketa/spravca"))):
        dalej = url_for("ucet.moje_konto")  # v zobrazení „ako člen“ nie na stránky správy
    return redirect(dalej)


@bp.app_context_processor
def inject_clen():
    return {"prihlaseny_clen": g.get("clen"), "zobrazenia_spravcu": ZOBRAZENIA_SPRAVCU, "clenom": clenom}


def clenom():
    """Dĺžka členstva prihláseného člena pre hornú lištu (v relácii, obnovuje sa raz za 10 minút)."""
    clen = g.get("clen")
    if clen is None:
        return ""
    import time
    ulozene = session.get("clenom")
    if ulozene and ulozene.get("cislo") == clen["cislo"] and time.time() - ulozene.get("cas", 0) < 600:
        return ulozene.get("dlzka", "")
    dlzka = next((z["dlzka"] for z in moje_udaje(get_db(), clen["cislo"]) if z["dlzka"]), "")
    session["clenom"] = {"cislo": clen["cislo"], "cas": time.time(), "dlzka": dlzka}
    return dlzka


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


def najdi_cisla(db, meno):
    """Prihlasovacie meno je e-mail (z evidencie alebo z Moje konto); funguje aj odborárske číslo."""
    if "@" not in meno:
        return [meno] if meno else []
    meno = meno.lower()
    return [r["cislo"] for r in db.execute(
        "SELECT c.cislo FROM clen c LEFT JOIN ucet u ON u.cislo = c.cislo "
        "WHERE lower(c.email) = ? OR lower(COALESCE(u.email, '')) = ? ORDER BY c.cislo", (meno, meno))]


@bp.route("/prihlasenie", methods=["GET", "POST"])
def prihlasenie():
    if g.clen is not None:
        return redirect(url_for("ucet.moje_konto"))
    if request.method == "POST":
        db = get_db()
        meno = re.sub(r"\s+", "", request.form.get("cislo", ""))
        heslo = request.form.get("heslo", "")
        kandidati = najdi_cisla(db, meno)
        # Ak má viac členov rovnaký e-mail, prihlási sa ten, ktorému sedí heslo.
        cislo = next((c for c in kandidati if over_heslo(
            db, db.execute("SELECT * FROM clen WHERE cislo = ?", (c,)).fetchone(),
            db.execute("SELECT * FROM ucet WHERE cislo = ?", (c,)).fetchone(), heslo)),
            kandidati[0] if kandidati else "")
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
            flash("Nesprávny e-mail alebo heslo.")
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
        if "zmazat_foto" in f.getlist("akcia"):
            zmaz_moju_fotku(db, g.clen, f.get("zaznam", ""))
            return redirect(url_for("ucet.moje_konto", _anchor="udaje"))
        if f.get("akcia") == "foto":
            uloz_moju_fotku(db, g.clen, f.get("zaznam", ""), request.files.get("foto"))
            return redirect(url_for("ucet.moje_konto", _anchor="udaje"))
        if f.get("akcia") == "udaje":
            uloz_moje_udaje(db, g.clen, f)
            return redirect(url_for("ucet.moje_konto", _anchor="udaje"))
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
    from anketa import dotazniky_clena
    from evidencia import zo_organov_clena
    from obsah import benefity_clena
    db = get_db()
    slugy = set() if g.clen.get("ako_clen") else set(zo_organov_clena(db, g.clen["cislo"]))
    if g.clen.get("zo_slug") and not g.clen.get("ako_clen") and (g.clen.get("funkcia") or g.clen.get("rola") == "zo"):
        slugy.add(g.clen["zo_slug"])
    listiny = [dict(r) for r in db.execute(
        f"SELECT slug, mesto FROM zakladna_organizacia WHERE slug IN ({','.join('?' * len(slugy))}) ORDER BY mesto",
        tuple(slugy))] if slugy else []
    return render_template("ucet/moje_konto.html", evidencia=moje_udaje(db, g.clen["cislo"]),
                           dotazniky=dotazniky_clena(db, g.clen["cislo"]), listiny=listiny,
                           benefity=benefity_clena())


def moje_udaje(db, cislo):
    """Záznamy prihláseného člena v evidencii: [{id, zo_slug, mesto, polia: [(stĺpec, hodnota, menitelne)]}]."""
    import json
    from evidencia import STANDARDNE_STLPCE, menitelne_clenom
    vysledok = []
    for r in db.execute(
            "SELECT e.id, e.zo_slug, e.udaje, z.mesto, s.stlpce FROM evidencia_clen e "
            "JOIN zakladna_organizacia z ON z.slug = e.zo_slug "
            "LEFT JOIN evidencia_stlpce s ON s.zo_slug = e.zo_slug"):
        udaje = json.loads(r["udaje"])
        if re.sub(r"\s+", "", udaje.get("Identifikačné číslo", "")) != cislo:
            continue
        poradie = STANDARDNE_STLPCE + [s for s in (json.loads(r["stlpce"]) if r["stlpce"] else [])
                                       if s not in STANDARDNE_STLPCE]
        stlpce = poradie + [s for s in udaje if s not in poradie]
        menitelne = menitelne_clenom(stlpce)
        for prvy, druhy in (("Titul", "Titul za menom"), ("Dátum narodenia", "Dátum vstupu do poistenia")):
            if prvy in stlpce and druhy in stlpce:  # dvojica v jednom riadku
                stlpce = [x for x in stlpce if x != druhy]
                stlpce.insert(stlpce.index(prvy) + 1, druhy)
        stlpce = [x for x in stlpce if x not in ("Preukaz", "Fotka")] + [x for x in ("Preukaz", "Fotka") if x in stlpce]
        polia = [(s, udaje.get(s, ""), menitelne.index(s) if s in menitelne else None) for s in stlpce
                 if s.lower() != "číslo" and s not in ("Predchádzajúce mená", "Identifikačné číslo") and (udaje.get(s) or s in menitelne)]
        from evidencia import dlzka_clenstva, obdobia_clenstva
        obdobia = obdobia_clenstva(db, udaje)
        from evidencia import kluc_clena, nacitaj_foto
        f = nacitaj_foto(db, kluc_clena(udaje))
        vysledok.append({"id": r["id"], "zo_slug": r["zo_slug"], "mesto": r["mesto"], "polia": polia,
                         "menitelne": menitelne, "obdobia": obdobia, "dlzka": dlzka_clenstva(obdobia),
                         "foto": f["verzia"] if f else None})
    return vysledok


def emaily_predsedov(db, zo_slug):
    """E-maily predsedu a podpredsedu ZO (prístup v evidencii), inak e-mail z popisu ZO."""
    emaily = [r["email"] for r in db.execute(
        "SELECT CASE WHEN COALESCE(u.email, '') != '' THEN u.email ELSE c.email END AS email "
        "FROM ucet u JOIN clen c ON c.cislo = u.cislo WHERE u.zo_slug = ? AND (u.rola = 'zo' OR u.funkcia != '')",
        (zo_slug,))
        if r["email"]]
    if not emaily:
        zo = db.execute("SELECT popis FROM zakladna_organizacia WHERE slug = ?", (zo_slug,)).fetchone()
        najdene = re.search(r"[^@\s]+@[^@\s]+\.[^@\s,;]+", zo["popis"] if zo else "")
        if najdene:
            emaily = [najdene.group(0)]
    return emaily


def email_predsedu_zo(db, zo_slug):
    """E-mail predsedu ZO; ak nie je určený, e-maily vedenia ZO (predseda/podpredseda)."""
    emaily = [r["email"] for r in db.execute(
        "SELECT CASE WHEN COALESCE(u.email, '') != '' THEN u.email ELSE c.email END AS email "
        "FROM ucet u JOIN clen c ON c.cislo = u.cislo WHERE u.zo_slug = ? "
        "AND (u.funkcia = 'predseda' OR (u.rola = 'zo' AND u.funkcia = ''))", (zo_slug,)) if r["email"]]
    return emaily or emaily_predsedov(db, zo_slug)


def ma_preukaz(udaje):
    hodnota = (udaje.get("Preukaz") or "").strip().lower()
    return hodnota not in ("", "nie", "-", "–", "0", "x")


def zmaz_moju_fotku(db, clen, zaznam_id):
    """Člen si odstráni svoju fotku z evidencie."""
    import json
    from evidencia import kluc_clena
    zaznam = next((z for z in moje_udaje(db, clen["cislo"]) if str(z["id"]) == str(zaznam_id)), None)
    if zaznam is None:
        abort(404)
    udaje = json.loads(db.execute("SELECT udaje FROM evidencia_clen WHERE id = ?", (zaznam["id"],)).fetchone()["udaje"])
    db.execute("DELETE FROM evidencia_foto WHERE kluc = ?", (kluc_clena(udaje),))
    if (udaje.get("Fotka") or "").strip().lower() == "áno":
        udaje["Fotka"] = ""
        db.execute("UPDATE evidencia_clen SET udaje = ? WHERE id = ?", (json.dumps(udaje, ensure_ascii=False), zaznam["id"]))
    db.commit()
    flash("Fotka bola odstránená.")


def uloz_moju_fotku(db, clen, zaznam_id, subor):
    """Člen si nahrá fotku do evidencie; ak nemá preukaz, dostane predseda OZ a ZO e-mail."""
    import json
    from evidencia import kluc_clena, uprav_fotku
    zaznam = next((z for z in moje_udaje(db, clen["cislo"]) if str(z["id"]) == str(zaznam_id)), None)
    if zaznam is None:
        abort(404)
    data = subor.read() if subor else b""
    if not data:
        flash("Vyberte fotku.")
        return
    if len(data) > 15 * 1024 * 1024:
        flash("Fotka je príliš veľká (najviac 15 MB).")
        return
    typ = subor.mimetype or ""
    try:
        data, novy_typ = uprav_fotku(data)
        typ = novy_typ or typ
    except Exception:
        flash("Súbor sa nepodarilo otvoriť ako obrázok. Použite fotku vo formáte JPG alebo PNG.")
        return
    if not typ.startswith("image/"):
        flash("Súbor nie je obrázok.")
        return
    udaje = json.loads(db.execute("SELECT udaje FROM evidencia_clen WHERE id = ?", (zaznam["id"],)).fetchone()["udaje"])
    kluc = kluc_clena(udaje)
    db.execute("INSERT INTO evidencia_foto (kluc, typ, data, verzia) VALUES (?, ?, ?, ?) "
               "ON CONFLICT(kluc) DO UPDATE SET typ = excluded.typ, data = excluded.data, verzia = excluded.verzia",
               (kluc, typ, data, datetime.now().strftime("%Y%m%d%H%M%S")))
    if not udaje.get("Fotka"):
        udaje["Fotka"] = "áno"
        db.execute("UPDATE evidencia_clen SET udaje = ? WHERE id = ?", (json.dumps(udaje, ensure_ascii=False), zaznam["id"]))
    db.commit()
    flash("Fotka bola uložená.")
    if ma_preukaz(udaje):
        return
    meno = " ".join(x for x in (udaje.get("Titul", ""), udaje.get("Meno poisteného", ""),
                                udaje.get("Priezvisko / Názov poisteného", ""), udaje.get("Titul za menom", "")) if x).strip()
    meno = meno or clen["meno"] or clen["cislo"]
    emaily = list(dict.fromkeys(([nastavenie(db, "email_predseda_oz")] if nastavenie(db, "email_predseda_oz") else [])
                                + email_predsedu_zo(db, zaznam["zo_slug"])))
    if not emaily or not posta.je_nastavena(db):
        return
    text = (f"Člen {meno} (odborárske číslo {clen['cislo']}, ZO {zaznam['mesto']}) si na stránke nahral fotku.\n\n"
            "Podľa evidencie zatiaľ nemá odborársky preukaz, preto je potrebné mu ho vyhotoviť.\n"
            "Fotka je uložená v evidencii členov pri jeho údajoch.\n")
    for komu in emaily:
        try:
            posta.posli(db, komu, f"Odborársky preukaz – {meno}", text)
        except Exception:
            pass


def uloz_moje_udaje(db, clen, f):
    import json
    from evidencia import kluc_clena, synchronizuj_prihlasovanie, zapamataj_mena
    zaznamy = moje_udaje(db, clen["cislo"])
    zaznam = next((z for z in zaznamy if str(z["id"]) == f.get("zaznam")), None)
    if zaznam is None:
        abort(404)
    r = db.execute("SELECT udaje FROM evidencia_clen WHERE id = ?", (zaznam["id"],)).fetchone()
    udaje = json.loads(r["udaje"])
    povodne_udaje = dict(udaje)
    zmeny = []
    for i, st in enumerate(zaznam["menitelne"]):
        nove = f.get(f"st_{i}", "").strip()
        if nove != udaje.get(st, ""):
            if st == "E-mail" and nove and not EMAIL_VZOR.match(nove):
                flash("Zadajte platnú e-mailovú adresu.")
                return
            if st == "E-mail" and not nove:
                flash("E-mail nemôže byť prázdny, je to vaše prihlasovacie meno.")
                return
            zmeny.append((st, udaje.get(st, ""), nove))
            udaje[st] = nove
    if not zmeny:
        flash("Nič sa nezmenilo.")
        return
    udaje = zapamataj_mena(povodne_udaje, {k: v for k, v in udaje.items() if v})
    db.execute("UPDATE evidencia_clen SET udaje = ?, kluc = ? WHERE id = ?",
               (json.dumps(udaje, ensure_ascii=False), kluc_clena(udaje), zaznam["id"]))
    if any(st == "E-mail" for st, _, _ in zmeny):
        db.execute("UPDATE ucet SET email = '' WHERE cislo = ?", (clen["cislo"],))
    meno = clen["meno"] or clen["cislo"]
    cas = datetime.now(CASOVA_ZONA).strftime("%d.%m.%Y %H:%M")
    db.execute("INSERT INTO evidencia_zmena (zo_slug, cislo, meno, cas, zmeny) VALUES (?, ?, ?, ?, ?)",
               (zaznam["zo_slug"], clen["cislo"], meno, cas, json.dumps(zmeny, ensure_ascii=False)))
    db.commit()
    synchronizuj_prihlasovanie(db)
    flash("Údaje boli uložené. Predseda vašej ZO dostane informáciu o zmene.")
    emaily = emaily_predsedov(db, zaznam["zo_slug"])
    if emaily and posta.je_nastavena(db):
        text = (f"Člen {meno} (odborárske číslo {clen['cislo']}) si na stránke zmenil údaje:\n\n"
                + "\n".join(f"{st}: {stare or '–'} → {nove or '–'}" for st, stare, nove in zmeny)
                + "\n\nZmena je už uložená v evidencii členov ZO " + zaznam["mesto"] + ".\n")
        for komu in emaily:
            try:
                posta.posli(db, komu, f"Zmena údajov člena – {meno}", text)
            except Exception:
                pass


@bp.route("/zabudnute-heslo", methods=["GET", "POST"])
def zabudnute_heslo():
    db = get_db()
    if request.method == "POST":
        kandidati = najdi_cisla(db, re.sub(r"\s+", "", request.form.get("cislo", "")))
        cislo = kandidati[0] if len(kandidati) == 1 else ""
        ucet = db.execute(
            "SELECT c.cislo, CASE WHEN COALESCE(u.email, '') != '' THEN u.email ELSE c.email END AS email "
            "FROM clen c LEFT JOIN ucet u ON u.cislo = c.cislo WHERE c.cislo = ?",
            (cislo,),
        ).fetchone()
        if ucet is not None and not ucet["email"]:
            ucet = None
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
                "INSERT INTO ucet (cislo, heslo_hash) VALUES (?, ?) ON CONFLICT(cislo) DO UPDATE SET "
                "heslo_hash = excluded.heslo_hash, zlyhane_pokusy = 0, zamknute_do = ''",
                (zaznam["cislo"], generate_password_hash(request.form["heslo"])),
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
        "SELECT c.cislo, c.meno, (c.preukaz_hash != '' OR c.sluzobne_hash != '') AS ma_preukaz, "
        "COALESCE(u.heslo_hash, '') != '' AS vlastne_heslo, "
        "CASE WHEN COALESCE(u.email, '') != '' THEN u.email ELSE c.email END AS email "
        "FROM clen c LEFT JOIN ucet u ON u.cislo = c.cislo ORDER BY c.cislo"
    ).fetchall()
    import json
    zo_clena = {}
    for r in db.execute("SELECT e.id, e.zo_slug, e.poradie, e.udaje, z.mesto FROM evidencia_clen e "
                        "JOIN zakladna_organizacia z ON z.slug = e.zo_slug"):
        c = re.sub(r"\s+", "", json.loads(r["udaje"]).get("Identifikačné číslo", ""))
        if c:
            zo_clena.setdefault(c, (r["mesto"], r["zo_slug"], r["id"], r["poradie"]))
    skupiny = {}
    for k in konta:
        mesto, slug, eid, poradie = zo_clena.get(k["cislo"], ("Bez ZO v evidencii", None, None, 10 ** 9))
        skupiny.setdefault(mesto, []).append(dict(k) | {"zo_slug": slug, "evidencia_id": eid, "poradie": poradie})
    skupiny = sorted(skupiny.items(), key=lambda x: (x[0] == "Bez ZO v evidencii", abecedne(x[0])))
    for _, clenovia in skupiny:
        clenovia.sort(key=lambda k: (len(k["cislo"]), k["cislo"]))  # podľa odborárskeho čísla
    return render_template(
        "ucet/spravca_konta.html",
        konta=konta,
        skupiny=skupiny,
        vlastnych=sum(1 for k in konta if k["vlastne_heslo"]),
        bez_preukazu=sum(1 for k in konta if not k["email"] or (not k["ma_preukaz"] and not k["vlastne_heslo"])),
        s_emailom=sum(1 for k in konta if k["email"]),
        smtp_email=nastavenie(db, "smtp_email") or "",
        email_predseda_oz=nastavenie(db, "email_predseda_oz") or "",
        email_nastaveny=posta.je_nastavena(db),
        role=VOLBY_PRISTUPU,
        pristupy=db.execute(
            "SELECT u.cislo, c.meno, u.rola, u.zo_slug, u.funkcia, z.mesto FROM ucet u "
            "JOIN clen c ON c.cislo = u.cislo "
            "LEFT JOIN zakladna_organizacia z ON z.slug = u.zo_slug "
            "WHERE u.rola IN ('zo', 'vedenie', 'spravca') OR u.funkcia != '' ORDER BY u.rola, z.poradie, c.meno"
        ).fetchall(),
        organizacie=sorted(db.execute("SELECT slug, mesto FROM zakladna_organizacia").fetchall(),
                           key=lambda z: abecedne(z["mesto"])),
    )


@bp.route("/spravca/konta/rola", methods=["POST"])
@len_spravca
def nastav_rolu():
    """Prístup člena k evidencii členov: bez prístupu / vedenie ZO (svoja ZO) / predsedníctvo (prezeranie)."""
    db = get_db()
    zadane = re.sub(r"\s+", "", request.form.get("cislo", ""))
    kandidati = najdi_cisla(db, zadane)
    cislo = kandidati[0] if kandidati else zadane
    rola = request.form.get("rola", "clen")
    funkcia = rola if rola in ("predseda", "podpredseda") else ""
    if funkcia:
        rola = "zo"
    zo_slug = request.form.get("zo_slug", "").strip() if rola == "zo" else ""
    if rola not in ROLE:
        rola = "clen"
    clen = db.execute("SELECT meno FROM clen WHERE cislo = ?", (cislo,)).fetchone()
    if clen is None:
        flash(f"Odborárske číslo {cislo} nie je v zozname členov.")
    elif rola == "zo" and db.execute(
            "SELECT 1 FROM zakladna_organizacia WHERE slug = ?", (zo_slug,)).fetchone() is None:
        flash("Vyberte základnú organizáciu.")
    elif funkcia:
        # Doterajší predseda / podpredseda tej ZO funkciu stratí.
        db.execute("UPDATE ucet SET funkcia = '', zo_slug = '', "
                   "rola = CASE WHEN rola = 'zo' THEN 'clen' ELSE rola END "
                   "WHERE zo_slug = ? AND funkcia = ? AND cislo != ?", (zo_slug, funkcia, cislo))
        db.execute(
            "INSERT INTO ucet (cislo, rola, zo_slug, funkcia) VALUES (?, 'zo', ?, ?) "
            "ON CONFLICT(cislo) DO UPDATE SET zo_slug = excluded.zo_slug, funkcia = excluded.funkcia, "
            "rola = CASE WHEN ucet.rola IN ('spravca', 'vedenie') THEN ucet.rola ELSE 'zo' END",
            (cislo, zo_slug, funkcia))
        db.commit()
        mesto = db.execute("SELECT mesto FROM zakladna_organizacia WHERE slug = ?", (zo_slug,)).fetchone()["mesto"]
        flash(f"{clen['meno'] or cislo}: {funkcia} ZO {mesto}.")
    else:
        db.execute(
            "INSERT INTO ucet (cislo, rola, zo_slug) VALUES (?, ?, ?) "
            "ON CONFLICT(cislo) DO UPDATE SET rola = excluded.rola, zo_slug = excluded.zo_slug, "
            "funkcia = CASE WHEN excluded.rola = 'zo' AND excluded.zo_slug = ucet.zo_slug THEN ucet.funkcia ELSE '' END",
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
    if f.get("akcia") == "predseda_oz":
        email = f.get("email_predseda_oz", "").strip()
        if email and not EMAIL_VZOR.match(email):
            flash("Zadajte platnú e-mailovú adresu predsedu OZ.")
        else:
            nastav(db, "email_predseda_oz", email)
            db.commit()
            flash("E-mail predsedu OZ bol uložený.")
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


def stav_prihlasenia(db, zadane, kandidati):
    """Pre správcu: prečo sa člen nevie prihlásiť (bez zobrazenia hesiel)."""
    kandidati = [c for c in kandidati if db.execute("SELECT 1 FROM clen WHERE cislo = ?", (c,)).fetchone()]
    if not kandidati:
        if "@" in zadane:
            return (f"E-mail {zadane} nemá v evidencii žiadny člen. Skontrolujte stĺpec E-mail v evidencii "
                    "(napr. nahrajte súbor s e-mailami cez „Spojiť s doterajšími údajmi“).")
        return f"{zadane} nie je v zozname členov."
    vysledky = []
    for c in kandidati:
        k = db.execute("SELECT * FROM clen WHERE cislo = ?", (c,)).fetchone()
        u = db.execute("SELECT * FROM ucet WHERE cislo = ?", (c,)).fetchone()
        casti = [f"Odborárske číslo {c}"]
        casti.append("e-mail v evidencii: " + ("áno" if k["email"] else "nie"))
        casti.append("služobné číslo v evidencii: " + ("áno" if k["sluzobne_hash"] else "nie"))
        if not k["z_evidencie"]:
            casti.append("je aj v nahratom zozname členov (CSV)")
        if u is not None and u["heslo_hash"]:
            casti.append("má nastavené VLASTNÉ heslo – služobné číslo neplatí, kým ho neobnovíte")
        if u is not None and u["zamknute_do"] and teraz() < u["zamknute_do"]:
            casti.append("konto je dočasne ZAMKNUTÉ po zlých pokusoch")
        vysledky.append(", ".join(casti) + ".")
    return " ".join(vysledky)


@bp.route("/spravca/konta/obnovit", methods=["POST"])
@len_spravca
def obnov_heslo():
    """Zabudnuté heslo: zruší vlastné heslo člena, heslom je znova číslo preukazu."""
    db = get_db()
    zadane = re.sub(r"\s+", "", request.form.get("cislo", ""))
    kandidati = najdi_cisla(db, zadane)
    cislo = kandidati[0] if kandidati else zadane
    if request.form.get("akcia") == "kontrola":
        flash(stav_prihlasenia(db, zadane, kandidati))
        return redirect(url_for("ucet.spravca_konta"))
    if db.execute("SELECT 1 FROM clen WHERE cislo = ?", (cislo,)).fetchone() is None:
        flash(f"{zadane} nie je v zozname členov.")
    else:
        db.execute(
            "UPDATE ucet SET heslo_hash = '', zlyhane_pokusy = 0, zamknute_do = '' WHERE cislo = ?",
            (cislo,),
        )
        db.commit()
        flash(f"Heslo člena {cislo} je obnovené. Prihlási sa svojím služobným alebo zamestnaneckým číslom.")
    return redirect(url_for("ucet.spravca_konta"))


@bp.route("/moje-konto/foto/<int:zaznam_id>")
@len_prihlaseny
def moja_foto(zaznam_id):
    from evidencia import kluc_clena, nacitaj_foto
    db = get_db()
    zaznam = next((z for z in moje_udaje(db, g.clen["cislo"]) if z["id"] == zaznam_id), None)
    if zaznam is None:
        abort(404)
    import json
    udaje = json.loads(db.execute("SELECT udaje FROM evidencia_clen WHERE id = ?", (zaznam_id,)).fetchone()["udaje"])
    f = nacitaj_foto(db, kluc_clena(udaje))
    if f is None:
        abort(404)
    return Response(f["data"], mimetype=f["typ"], headers={"Cache-Control": "private, max-age=86400"})
