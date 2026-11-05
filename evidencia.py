"""Evidencia členov podľa základných organizácií.

Prístup: správca všetko; predseda a podpredseda ZO (rola „zo“) prezerá a mení len svoju ZO;
predsedníctvo (rola „vedenie“) prezerá všetky ZO, ale nič nemení.

Údaje sa nahrávajú zo zoznamov v Exceli (.xls alebo .xlsx) v tvare „Zoznam poistených“:
každá ZO má vlastný hárok, ktorého názov začína číslom zoznamu (napr. „10.BBVV“).
Ukladajú sa všetky stĺpce tak, ako sú v súbore, aj s farbou riadku.
"""
import csv
import io
import json
import re
from datetime import datetime, timedelta

from functools import wraps

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for

from anketa import je_spravca
from db import get_db

bp = Blueprint("evidencia", __name__, url_prefix="/spravca/evidencia")

# Číslo zoznamu v súbore -> ZO na stránke.
CISLO_ZOZNAMU = {
    1: "ruzomberok", 2: "kosice", 3: "hrnciarovce-nad-parnou", 4: "dubnica-nad-vahom", 5: "sucany",
    6: "zilina", 7: "bratislava", 8: "trencin", 9: "nitra", 10: "banska-bystrica", 11: "ilava",
    12: "zeliezovce", 13: "nitra-chrenova", 14: "banska-bystrica-kralova", 15: "levoca",
    16: "generalne-riaditelstvo", 17: "presov-sabinov", 18: "leopoldov", 19: "kosice-saca",
}

FARBY = {"zelena": "zelený", "cervena": "červený"}

# Údaje, ktoré si člen v Moje konto zmeniť nemôže (mení ich len predseda ZO alebo správca).
NEMENITELNE_CLENOM = {
    "číslo", "Identifikačné číslo", "Dátum narodenia", "Dátum vstupu do poistenia", "Dátum výstupu z poistenia",
    "Preukaz", "Fotka", "MEDAILA, PLAKETA", "Podporný fond", "Predchádzajúce mená",
}


def menitelne_clenom(stlpce):
    return [s for s in stlpce if s not in NEMENITELNE_CLENOM and s.lower() != "číslo"]

# Stĺpce, ktoré má každá ZO, aj keď ich v nahratom súbore nie je (dajú sa doplniť ručne).
STANDARDNE_STLPCE = [
    "Identifikačné číslo", "Titul", "Meno poisteného", "Priezvisko / Názov poisteného", "Titul za menom",
    "Dátum narodenia", "Adresa trvalého pobytu – ulica, číslo domu", "Adresa trvalého pobytu – mesto",
    "Adresa trvalého pobytu – PSČ", "Dátum vstupu do poistenia", "Dátum výstupu z poistenia",
    "E-mail", "Preukaz", "Fotka", "MEDAILA, PLAKETA", "Kontakt", "Služobné/ zam. číslo", "Podporný fond",
    "Predchádzajúce mená",
]
PREDCHADZAJUCE = "Predchádzajúce mená"


def zjednot_stlpec(nazov):
    """Rovnaký stĺpec môže mať v súboroch trochu iný názov; zjednotíme ho na štandardný."""
    jednoduchy = re.sub(r"[^a-záäčďéíĺľňóôŕšťúýž0-9]", "", nazov.lower())
    for st in STANDARDNE_STLPCE:
        if re.sub(r"[^a-záäčďéíĺľňóôŕšťúýž0-9]", "", st.lower()) == jednoduchy:
            return st
    if jednoduchy in ("ulicačíslodomu",):
        return "Adresa trvalého pobytu – ulica, číslo domu"
    if jednoduchy == "mesto":
        return "Adresa trvalého pobytu – mesto"
    if jednoduchy == "psč":
        return "Adresa trvalého pobytu – PSČ"
    return nazov


def init_db(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS evidencia_clen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            poradie INTEGER NOT NULL,
            kluc TEXT NOT NULL,
            farba TEXT NOT NULL DEFAULT '',
            udaje TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS evidencia_stlpce (
            zo_slug TEXT PRIMARY KEY,
            stlpce TEXT NOT NULL,
            nahrate TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS evidencia_archiv (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            kluc TEXT NOT NULL,
            farba TEXT NOT NULL DEFAULT '',
            udaje TEXT NOT NULL,
            archivovane TEXT NOT NULL,
            dovod TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS evidencia_zmena (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            cislo TEXT NOT NULL,
            meno TEXT NOT NULL,
            cas TEXT NOT NULL,
            zmeny TEXT NOT NULL
        )
        """
    )
    stlpce_clen = [r["name"] for r in db.execute("PRAGMA table_info(clen)")]
    if "z_evidencie" not in stlpce_clen:
        db.execute("ALTER TABLE clen ADD COLUMN z_evidencie INTEGER NOT NULL DEFAULT 0")
    if "email" not in stlpce_clen:
        db.execute("ALTER TABLE clen ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "sluzobne_hash" not in stlpce_clen:
        db.execute("ALTER TABLE clen ADD COLUMN sluzobne_hash TEXT NOT NULL DEFAULT ''")
    db.commit()
    synchronizuj_prihlasovanie(db)


def synchronizuj_prihlasovanie(db):
    """Členovia z evidencie sa môžu prihlásiť: meno = e-mail z evidencie (alebo identifikačné číslo),
    heslo = služobné / zamestnanecké číslo. Ručne nahratý zoznam členov má prednosť."""
    from anketa import hash_preukazu

    z_evidencie = {}
    for (u,) in db.execute("SELECT udaje FROM evidencia_clen"):
        u = json.loads(u)
        cislo = re.sub(r"\s+", "", u.get("Identifikačné číslo", ""))
        if not cislo:
            continue
        meno = " ".join(x for x in (u.get("Meno poisteného", ""), u.get("Priezvisko / Názov poisteného", "")) if x)
        sluzobne = u.get("Služobné/ zam. číslo", "")
        email = re.sub(r"\s+", "", u.get("E-mail", "")).lower()
        z_evidencie[cislo] = (meno, hash_preukazu(db, sluzobne) if sluzobne.strip() else "", email)
    existujuce = {r["cislo"]: r for r in db.execute("SELECT cislo, meno, preukaz_hash, z_evidencie FROM clen")}
    for cislo, (meno, preukaz, email) in z_evidencie.items():
        r = existujuce.get(cislo)
        if r is None:
            db.execute("INSERT INTO clen (cislo, meno, preukaz_hash, z_evidencie, email, sluzobne_hash) "
                       "VALUES (?, ?, ?, 1, ?, ?)", (cislo, meno, preukaz, email, preukaz))
            continue
        db.execute("UPDATE clen SET email = ?, sluzobne_hash = ? WHERE cislo = ?", (email, preukaz, cislo))
        if r["z_evidencie"]:
            db.execute("UPDATE clen SET meno = ?, preukaz_hash = ? WHERE cislo = ?", (meno, preukaz, cislo))
        else:
            db.execute("UPDATE clen SET meno = CASE WHEN meno = '' THEN ? ELSE meno END, "
                       "preukaz_hash = CASE WHEN preukaz_hash = '' THEN ? ELSE preukaz_hash END WHERE cislo = ?",
                       (meno, preukaz, cislo))
    for cislo, r in existujuce.items():
        if cislo in z_evidencie:
            continue
        if r["z_evidencie"]:
            db.execute("DELETE FROM clen WHERE cislo = ?", (cislo,))
        else:
            db.execute("UPDATE clen SET email = '', sluzobne_hash = '' WHERE cislo = ?", (cislo,))
    db.commit()


# --- čítanie Excelu -----------------------------------------------------------


def _farba(rgb):
    if not rgb:
        return ""
    r, g, b = rgb[:3]
    if g > 150 and r < 200 and b < 100:
        return "zelena"
    if r > 200 and g < 100 and b < 100:
        return "cervena"
    return ""


def _hodnota(v, stlpec, datemode):
    """Excel uloží dátumy a PSČ ako čísla; vrátime ich ako čitateľný text."""
    if isinstance(v, float):
        if "dátum" in stlpec.lower() and 1000 < v < 80000:
            d = datetime(1899, 12, 30) + timedelta(days=v if datemode == 0 else v + 1462)
            return d.strftime("%d.%m.%Y")
        if v == int(v):
            # PSČ uložené ako číslo stratí úvodnú nulu (napr. 04001 -> 4001).
            return str(int(v)).zfill(5) if "psč" in stlpec.lower() else str(int(v))
        return str(v)
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y")
    return str(v).strip() if v is not None else ""


def _harky_xls(data):
    import xlrd

    wb = xlrd.open_workbook(file_contents=data, formatting_info=True)
    for sh in wb.sheets():
        riadky, farby = [], []
        for r in range(sh.nrows):
            riadky.append(sh.row_values(r))
            rgb = None
            if sh.ncols > 4:
                xf = wb.xf_list[sh.cell_xf_index(r, 4)]
                if xf.background.fill_pattern:
                    rgb = wb.colour_map.get(xf.background.pattern_colour_index)
            farby.append(_farba(rgb))
        yield sh.name, riadky, farby, wb.datemode


def _harky_xlsx(data):
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    for sh in wb.worksheets:
        riadky, farby = [], []
        for row in sh.iter_rows():
            riadky.append([c.value for c in row])
            rgb = None
            if len(row) > 4 and row[4].fill and row[4].fill.fill_type == "solid":
                farba = row[4].fill.fgColor
                if farba is not None and farba.type == "rgb" and isinstance(farba.rgb, str):
                    h = farba.rgb[-6:]
                    rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            farby.append(_farba(rgb))
        yield sh.title, riadky, farby, 0


def precitaj_subor(nazov, data):
    """Vráti {zo_slug: (stĺpce, [(farba, {stĺpec: hodnota})])} pre hárky, ktoré patria nejakej ZO."""
    harky = _harky_xlsx(data) if nazov.lower().endswith(".xlsx") else _harky_xls(data)
    vysledok = {}
    for meno_harku, riadky, farby, datemode in harky:
        m = re.match(r"\s*(\d+)\s*\.", meno_harku)
        if not m or int(m.group(1)) not in CISLO_ZOZNAMU:
            continue
        slug = CISLO_ZOZNAMU[int(m.group(1))]
        # Hlavička je riadok, ktorý v prvom stĺpci obsahuje „číslo“; pod ním býva podhlavička.
        h = next((i for i, r in enumerate(riadky[:15]) if r and str(r[0] or "").strip().lower() == "číslo"), None)
        if h is None:
            continue
        hlavicka = riadky[h]
        pod = riadky[h + 1] if h + 1 < len(riadky) else []
        stlpce = []
        for i in range(len(hlavicka)):
            hore = str(hlavicka[i] or "").strip()
            dole = str(pod[i] or "").strip() if i < len(pod) else ""
            nazov_st = dole or hore
            if not nazov_st:
                # Napr. „identifikačné číslo“ býva v niektorých hárkoch o riadok vyššie nad hlavičkou.
                for j in range(h - 1, max(h - 4, -1), -1):
                    nad = riadky[j]
                    if i < len(nad) and str(nad[i] or "").strip():
                        nazov_st = str(nad[i]).strip()
                        break
            if nazov_st:
                stlpce.append((i, zjednot_stlpec(nazov_st)))
        clenovia = []
        for r in range(h + 2, len(riadky)):
            riadok = riadky[r]
            udaje = {}
            for i, st in stlpce:
                if i < len(riadok):
                    hodnota = _hodnota(riadok[i], st, datemode)
                    if hodnota:
                        udaje[st] = hodnota
            # Riadok bez mena a priezviska je prázdny (len poradové číslo).
            mena = [v for k, v in udaje.items() if "meno" in k.lower() or "priezvisko" in k.lower()]
            if not mena:
                continue
            clenovia.append((farby[r] if r < len(farby) else "", udaje))
        vysledok[slug] = ([st for _, st in stlpce], clenovia)
    return vysledok


def kluc_clena(udaje):
    for k, v in udaje.items():
        if "identifikačné" in k.lower() and v:
            return "id:" + v
    casti = [v for k, v in udaje.items() if any(s in k.lower() for s in ("meno", "priezvisko", "narodenia"))]
    return "meno:" + "|".join(casti).lower()


def _norm(text):
    import unicodedata
    text = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"\s+", "", text)


def cele_meno(udaje):
    return " ".join(x for x in (udaje.get("Meno poisteného", ""), udaje.get("Priezvisko / Názov poisteného", "")) if x)


def osoby(udaje):
    """Ten istý človek aj s novým číslom: (meno priezvisko + dátum narodenia) pre súčasné aj predchádzajúce mená."""
    narodenie = udaje.get("Dátum narodenia", "")
    mena = [cele_meno(udaje)] + [m for m in udaje.get(PREDCHADZAJUCE, "").split(";")]
    return {_norm(m) + "|" + _norm(narodenie) for m in mena if m.strip()}


def zapamataj_mena(stare, nove):
    """Ak sa zmenilo meno alebo priezvisko, pôvodné sa pridá do „Predchádzajúce mená“ (nič sa nestratí)."""
    zoznam = [m.strip() for m in (stare.get(PREDCHADZAJUCE, "") + ";" + nove.get(PREDCHADZAJUCE, "")).split(";")
              if m.strip()]
    staremeno = cele_meno(stare)
    if staremeno and _norm(staremeno) != _norm(cele_meno(nove)):
        zoznam.append(staremeno)
    vysledok = []
    for m in zoznam:
        if _norm(m) != _norm(cele_meno(nove)) and _norm(m) not in [_norm(x) for x in vysledok]:
            vysledok.append(m)
    if vysledok:
        nove[PREDCHADZAJUCE] = "; ".join(vysledok)
    else:
        nove.pop(PREDCHADZAJUCE, None)
    return nove


def kluce_osoby(udaje):
    """Kľúče, podľa ktorých sa ten istý človek spozná aj medzi rôznymi verziami súboru."""
    return {kluc_clena(udaje)} | {"osoba:" + o for o in osoby(udaje)}


def archivuj(db, zo_slug, kluc, farba, udaje_json, dovod):
    db.execute("INSERT INTO evidencia_archiv (zo_slug, kluc, farba, udaje, archivovane, dovod) "
               "VALUES (?, ?, ?, ?, ?, ?)",
               (zo_slug, kluc, farba, udaje_json, datetime.now().strftime("%d.%m.%Y"), dovod))


def _den(text):
    from datetime import date
    m = re.match(r"\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def obdobia_clenstva(db, udaje):
    """Všetky obdobia členstva tohto človeka: z archívu (aj pod starým číslom) + súčasné. [(od, do, popis)]"""
    from datetime import date
    ja = osoby(udaje)
    obdobia = []
    if ja:
        for r in db.execute("SELECT a.udaje, a.archivovane, z.mesto FROM evidencia_archiv a "
                            "JOIN zakladna_organizacia z ON z.slug = a.zo_slug").fetchall():
            u = json.loads(r["udaje"])
            if not (osoby(u) & ja):
                continue
            od = _den(u.get("Dátum vstupu do poistenia"))
            do = _den(u.get("Dátum výstupu z poistenia")) or _den(r["archivovane"])
            if od and do:
                obdobia.append((od, do, f"ZO {r['mesto']}, číslo {u.get('Identifikačné číslo', '–')}"))
    od = _den(udaje.get("Dátum vstupu do poistenia"))
    if od:
        obdobia.append((od, _den(udaje.get("Dátum výstupu z poistenia")) or date.today(), "súčasné členstvo"))
    return sorted(obdobia)


def dlzka_clenstva(obdobia):
    """Spolu bez prekrývania, ako text „X rokov Y mesiacov“."""
    dni, koniec = 0, None
    for od, do, _ in obdobia:
        if koniec and od <= koniec:
            if do > koniec:
                dni += (do - koniec).days
                koniec = do
        else:
            dni += max((do - od).days, 0)
            koniec = do
    roky, mesiace = dni // 365, (dni % 365) // 30
    if not obdobia:
        return ""
    def tvar(n, a, b, c):
        return f"{n} {a if n == 1 else b if 2 <= n <= 4 else c}"
    return f"{tvar(roky, 'rok', 'roky', 'rokov')} {tvar(mesiace, 'mesiac', 'mesiace', 'mesiacov')}"


def abecedne(text):
    """Kľúč na zoradenie podľa slovenskej abecedy (diakritika až za základným písmenom)."""
    import unicodedata
    zaklad = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))
    return (zaklad, text.lower())


# --- prístup ------------------------------------------------------------------


def pristup(slug=None):
    """Vráti 'menit', 'citat' alebo None podľa toho, kto je prihlásený."""
    if je_spravca():
        return "menit"
    clen = g.get("clen")
    if clen is None:
        return None
    if slug is not None and clen["zo_slug"] == slug and (clen["rola"] == "zo" or clen["funkcia"]):
        return "menit"
    if clen["rola"] == "vedenie":
        return "citat"
    return None


def vyzaduj(menit=False):
    def dekorator(f):
        @wraps(f)
        def obal(*args, **kwargs):
            p = pristup(kwargs.get("slug"))
            if p is None or (menit and p != "menit"):
                if not je_spravca() and g.get("clen") is None:
                    return redirect(url_for("ucet.prihlasenie", dalej=request.path))
                abort(403)
            g.moze_menit = p == "menit"
            return f(*args, **kwargs)
        return obal
    return dekorator


@bp.app_context_processor
def inject_pristup():
    return {"moze_menit": g.get("moze_menit", False), "je_spravca": bool(je_spravca())}


# --- stránky -----------------------------------------------------------------


@bp.route("/", methods=["GET", "POST"])
def prehlad():
    db = get_db()
    clen = g.get("clen")
    if not je_spravca() and clen is not None and clen["rola"] == "zo" and clen["zo_slug"]:
        return redirect(url_for("evidencia.zo", slug=clen["zo_slug"]))
    if request.method == "POST" and not je_spravca():
        abort(403)
    if pristup() is None:
        if g.get("clen") is None:
            return redirect(url_for("ucet.prihlasenie", dalej=request.path))
        abort(403)
    if request.method == "POST":
        subor = request.files.get("subor")
        if not subor or not subor.filename:
            flash("Vyberte súbor Excel so zoznamom členov.")
            return redirect(url_for("evidencia.prehlad"))
        try:
            obsah = precitaj_subor(subor.filename, subor.read())
        except ImportError:
            flash("Na serveri chýba knižnica na čítanie .xls. Spustite znova aktualizuj.sh "
                  "alebo súbor uložte v Exceli ako .xlsx.")
            return redirect(url_for("evidencia.prehlad"))
        except Exception:
            flash("Súbor sa nepodarilo prečítať. Musí to byť zoznam v Exceli (.xls alebo .xlsx).")
            return redirect(url_for("evidencia.prehlad"))
        if not obsah:
            flash("V súbore sa nenašiel žiadny hárok so zoznamom ZO (názov hárku musí začínať číslom, napr. „10.BBVV“).")
            return redirect(url_for("evidencia.prehlad"))
        spojit = request.form.get("rezim") == "spojit"
        teraz = datetime.now().strftime("%d.%m.%Y %H:%M")
        spolu = 0
        archivovanych = 0
        for slug, (stlpce, clenovia) in obsah.items():
            if spojit:
                povodne = db.execute("SELECT stlpce FROM evidencia_stlpce WHERE zo_slug = ?", (slug,)).fetchone()
                vsetky = json.loads(povodne["stlpce"]) if povodne else []
                vsetky += [s for s in stlpce if s not in vsetky]
                existujuce = {r["kluc"]: r for r in db.execute(
                    "SELECT id, kluc, farba, udaje FROM evidencia_clen WHERE zo_slug = ?", (slug,))}
                dalsie = db.execute("SELECT COALESCE(MAX(poradie), 0) FROM evidencia_clen WHERE zo_slug = ?",
                                    (slug,)).fetchone()[0]
                for farba, udaje in clenovia:
                    k = kluc_clena(udaje)
                    if k in existujuce:
                        stare = json.loads(existujuce[k]["udaje"])
                        povodne_udaje = dict(stare)
                        stare.update(udaje)
                        zapamataj_mena(povodne_udaje, stare)
                        db.execute("UPDATE evidencia_clen SET udaje = ?, farba = ? WHERE id = ?",
                                   (json.dumps(stare, ensure_ascii=False), farba or existujuce[k]["farba"],
                                    existujuce[k]["id"]))
                    else:
                        dalsie += 1
                        db.execute("INSERT INTO evidencia_clen (zo_slug, poradie, kluc, farba, udaje) "
                                   "VALUES (?, ?, ?, ?, ?)",
                                   (slug, dalsie, k, farba, json.dumps(udaje, ensure_ascii=False)))
                stlpce = vsetky
            else:
                nove_kluce = set()
                for _, u in clenovia:
                    nove_kluce |= kluce_osoby(u)
                odisli = 0
                for r in db.execute("SELECT kluc, farba, udaje FROM evidencia_clen WHERE zo_slug = ?",
                                    (slug,)).fetchall():
                    if not (kluce_osoby(json.loads(r["udaje"])) & nove_kluce):
                        archivuj(db, slug, r["kluc"], r["farba"], r["udaje"], "nie je v novom zozname")
                        odisli += 1
                archivovanych += odisli
                predtym = {r["kluc"]: json.loads(r["udaje"]) for r in db.execute(
                    "SELECT kluc, udaje FROM evidencia_clen WHERE zo_slug = ?", (slug,))}
                for _, u in clenovia:
                    if kluc_clena(u) in predtym and kluc_clena(u).startswith("id:"):
                        zapamataj_mena(predtym[kluc_clena(u)], u)
                db.execute("DELETE FROM evidencia_clen WHERE zo_slug = ?", (slug,))
                db.executemany(
                    "INSERT INTO evidencia_clen (zo_slug, poradie, kluc, farba, udaje) VALUES (?, ?, ?, ?, ?)",
                    [(slug, i, kluc_clena(u), f, json.dumps(u, ensure_ascii=False))
                     for i, (f, u) in enumerate(clenovia, 1)],
                )
            db.execute(
                "INSERT INTO evidencia_stlpce (zo_slug, stlpce, nahrate) VALUES (?, ?, ?) "
                "ON CONFLICT(zo_slug) DO UPDATE SET stlpce = excluded.stlpce, nahrate = excluded.nahrate",
                (slug, json.dumps(stlpce, ensure_ascii=False), teraz),
            )
            spolu += len(clenovia)
        db.commit()
        synchronizuj_prihlasovanie(db)
        flash(f"Nahraté: {len(obsah)} ZO, spolu {spolu} členov "
              f"({'údaje spojené s doterajšími' if spojit else 'zoznamy týchto ZO nahradené'})."
              + (f" Do archívu odídených presunutých: {archivovanych}." if archivovanych else ""))
        return redirect(url_for("evidencia.prehlad"))

    organizacie = db.execute(
        """SELECT z.slug, z.mesto,
                  (SELECT COUNT(*) FROM evidencia_clen e WHERE e.zo_slug = z.slug) AS pocet,
                  (SELECT COUNT(*) FROM evidencia_clen e WHERE e.zo_slug = z.slug AND e.farba = 'zelena') AS zelenych,
                  (SELECT COUNT(*) FROM evidencia_clen e WHERE e.zo_slug = z.slug AND e.farba = 'cervena') AS cervenych,
                  s.nahrate
           FROM zakladna_organizacia z LEFT JOIN evidencia_stlpce s ON s.zo_slug = z.slug
           ORDER BY z.poradie, z.mesto"""
    ).fetchall()
    organizacie = sorted(organizacie, key=lambda z: abecedne(z["mesto"]))
    return render_template("evidencia/prehlad.html", organizacie=organizacie)


def _nacitaj_zo(slug):
    db = get_db()
    zo = db.execute("SELECT slug, mesto, nazov FROM zakladna_organizacia WHERE slug = ?", (slug,)).fetchone()
    if zo is None:
        abort(404)
    st = db.execute("SELECT stlpce, nahrate FROM evidencia_stlpce WHERE zo_slug = ?", (slug,)).fetchone()
    stlpce = STANDARDNE_STLPCE + [x for x in (json.loads(st["stlpce"]) if st else []) if x not in STANDARDNE_STLPCE]
    clenovia = [
        {"id": r["id"], "farba": r["farba"], "udaje": json.loads(r["udaje"])}
        for r in db.execute("SELECT id, farba, udaje FROM evidencia_clen WHERE zo_slug = ? ORDER BY poradie, id",
                            (slug,))
    ]
    stlpce = [s for s in stlpce if s.lower() != "číslo"]
    return zo, stlpce, clenovia, (st["nahrate"] if st else None)


ZORADENIA = {
    "subor": "ako v súbore",
    "abeceda": "podľa abecedy (priezvisko)",
    "cislo": "podľa identifikačného čísla",
    "vstup": "podľa dátumu vstupu",
}


def _datum(text):
    m = re.match(r"\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text or "")
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (9999, 0, 0)


def _vyber(clenovia):
    """Vyhľadávanie a zoradenie podľa parametrov v adrese (spoločné pre tabuľku aj export)."""
    hladat = request.args.get("hladat", "").strip().lower()
    if hladat:
        clenovia = [c for c in clenovia if any(hladat in v.lower() for v in c["udaje"].values())]
    zoradit = request.args.get("zoradit", "subor")
    if zoradit == "abeceda":
        clenovia = sorted(clenovia, key=lambda c: (abecedne(c["udaje"].get("Priezvisko / Názov poisteného", "")),
                                                   abecedne(c["udaje"].get("Meno poisteného", ""))))
    elif zoradit == "cislo":
        clenovia = sorted(clenovia, key=lambda c: (len(c["udaje"].get("Identifikačné číslo", "")) or 99,
                                                   c["udaje"].get("Identifikačné číslo", "")))
    elif zoradit == "vstup":
        clenovia = sorted(clenovia, key=lambda c: _datum(c["udaje"].get("Dátum vstupu do poistenia", "")))
    else:
        zoradit = "subor"
    return clenovia, hladat, zoradit


@bp.route("/<slug>")
@vyzaduj()
def zo(slug):
    zo, stlpce, clenovia, nahrate = _nacitaj_zo(slug)
    clenovia, hladat, zoradit = _vyber(clenovia)
    zmeny = [dict(r) | {"zmeny": json.loads(r["zmeny"])} for r in get_db().execute(
        "SELECT cas, meno, cislo, zmeny FROM evidencia_zmena WHERE zo_slug = ? ORDER BY id DESC LIMIT 30", (slug,))]
    vsetci = _nacitaj_zo(slug)[2]
    return render_template("evidencia/zo.html", zo=zo, stlpce=stlpce, clenovia=clenovia, nahrate=nahrate,
                           hladat=hladat, zoradit=zoradit, zoradenia=ZORADENIA, farby=FARBY, zmeny=zmeny,
                           vedenie=vedenie_zo(get_db(), slug), na_vyber=sorted(
                               [c for c in vsetci if c["udaje"].get("Identifikačné číslo")],
                               key=lambda c: (abecedne(c["udaje"].get("Priezvisko / Názov poisteného", "")),
                                              abecedne(c["udaje"].get("Meno poisteného", "")))))


FUNKCIE = ("predseda", "podpredseda")


def vedenie_zo(db, slug):
    """{funkcia: {cislo, meno}} pre predsedu a podpredsedu ZO."""
    return {r["funkcia"]: {"cislo": r["cislo"], "meno": r["meno"] or r["cislo"]} for r in db.execute(
        "SELECT u.cislo, u.funkcia, COALESCE(c.meno, '') AS meno FROM ucet u LEFT JOIN clen c ON c.cislo = u.cislo "
        "WHERE u.zo_slug = ? AND u.funkcia IN ('predseda', 'podpredseda')", (slug,))}


@bp.route("/<slug>/vedenie", methods=["POST"])
def nastav_vedenie(slug):
    """Správca vyberie predsedu a podpredsedu ZO z jej členov; dostanú prístup na zmeny v svojej ZO."""
    if not je_spravca():
        abort(403)
    zo, _, clenovia, _ = _nacitaj_zo(slug)
    db = get_db()
    platne = {re.sub(r"\s+", "", c["udaje"].get("Identifikačné číslo", "")) for c in clenovia} - {""}
    for funkcia in FUNKCIE:
        cislo = re.sub(r"\s+", "", request.form.get(funkcia, ""))
        if cislo and cislo not in platne:
            continue
        db.execute("UPDATE ucet SET funkcia = '', zo_slug = '', "
                   "rola = CASE WHEN rola = 'zo' THEN 'clen' ELSE rola END "
                   "WHERE zo_slug = ? AND funkcia = ? AND cislo != ?", (slug, funkcia, cislo))
        if cislo:
            db.execute(
                "INSERT INTO ucet (cislo, rola, zo_slug, funkcia) VALUES (?, 'zo', ?, ?) "
                "ON CONFLICT(cislo) DO UPDATE SET zo_slug = excluded.zo_slug, funkcia = excluded.funkcia, "
                "rola = CASE WHEN ucet.rola IN ('spravca', 'vedenie') THEN ucet.rola ELSE 'zo' END",
                (cislo, slug, funkcia))
    db.commit()
    flash(f"Vedenie ZO {zo['mesto']} bolo uložené. Predseda a podpredseda môžu meniť členov svojej ZO.")
    return redirect(url_for("evidencia.zo", slug=slug))


@bp.route("/<slug>/export.csv")
@vyzaduj()
def export(slug):
    zo, stlpce, clenovia, _ = _nacitaj_zo(slug)
    clenovia, _, _ = _vyber(clenovia)
    vystup = io.StringIO()
    w = csv.writer(vystup, delimiter=";")
    w.writerow(["Farba"] + stlpce)
    for c in clenovia:
        w.writerow([FARBY.get(c["farba"], "")] + [c["udaje"].get(s, "") for s in stlpce])
    return Response(
        "\ufeff" + vystup.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="clenovia-{slug}.csv"'},
    )


@bp.route("/<slug>/export.xlsx")
@vyzaduj()
def export_xlsx(slug):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        flash("Na serveri chýba knižnica pre Excel, stiahnite zoznam ako CSV.")
        return redirect(url_for("evidencia.zo", slug=slug))
    zo, stlpce, clenovia, _ = _nacitaj_zo(slug)
    clenovia, _, _ = _vyber(clenovia)
    wb = Workbook()
    ws = wb.active
    ws.title = zo["mesto"][:31]
    ws.append(["P. č."] + stlpce)
    for bunka in ws[1]:
        bunka.font = Font(bold=True)
    vypln = {"zelena": PatternFill("solid", fgColor="C6EFCE"), "cervena": PatternFill("solid", fgColor="FFC7CE")}
    for i, c in enumerate(clenovia, 1):
        ws.append([i] + [c["udaje"].get(s, "") for s in stlpce])
        if c["farba"] in vypln:
            for bunka in ws[ws.max_row]:
                bunka.fill = vypln[c["farba"]]
    for stlpec in ws.columns:
        sirka = max(len(str(b.value or "")) for b in stlpec)
        ws.column_dimensions[stlpec[0].column_letter].width = min(max(sirka + 2, 6), 45)
    ws.freeze_panes = "B2"
    vystup = io.BytesIO()
    wb.save(vystup)
    return Response(
        vystup.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="clenovia-{slug}.xlsx"'},
    )


@bp.route("/<slug>/clen/novy", methods=["GET", "POST"])
@bp.route("/<slug>/clen/<int:clen_id>", methods=["GET", "POST"])
@vyzaduj(menit=True)
def uprav_clena(slug, clen_id=None):
    zo, stlpce, _, _ = _nacitaj_zo(slug)
    db = get_db()
    clen = {"farba": "", "udaje": {}}
    if clen_id is not None:
        r = db.execute("SELECT farba, udaje FROM evidencia_clen WHERE id = ? AND zo_slug = ?",
                       (clen_id, slug)).fetchone()
        if r is None:
            abort(404)
        clen = {"farba": r["farba"], "udaje": json.loads(r["udaje"])}
    if request.method == "POST":
        akcia = request.form.get("akcia")
        if akcia == "zmazat" and clen_id is not None:
            r = db.execute("SELECT kluc, farba, udaje FROM evidencia_clen WHERE id = ?", (clen_id,)).fetchone()
            archivuj(db, slug, r["kluc"], r["farba"], r["udaje"], "vymazaný ručne")
            db.execute("DELETE FROM evidencia_clen WHERE id = ?", (clen_id,))
            db.commit()
            synchronizuj_prihlasovanie(db)
            flash("Člen bol vymazaný z evidencie a presunutý do archívu odídených.")
            return redirect(url_for("evidencia.zo", slug=slug))
        udaje = {st: request.form.get(f"st_{i}", "").strip() for i, st in enumerate(stlpce)}
        udaje = {k: v for k, v in udaje.items() if v}
        farba = request.form.get("farba", "") if request.form.get("farba") in FARBY else ""
        if clen_id is None:
            poradie = db.execute("SELECT COALESCE(MAX(poradie), 0) + 1 FROM evidencia_clen WHERE zo_slug = ?",
                                 (slug,)).fetchone()[0]
            db.execute("INSERT INTO evidencia_clen (zo_slug, poradie, kluc, farba, udaje) VALUES (?, ?, ?, ?, ?)",
                       (slug, poradie, kluc_clena(udaje), farba, json.dumps(udaje, ensure_ascii=False)))
        else:
            zapamataj_mena(clen["udaje"], udaje)
            db.execute("UPDATE evidencia_clen SET kluc = ?, farba = ?, udaje = ? WHERE id = ?",
                       (kluc_clena(udaje), farba, json.dumps(udaje, ensure_ascii=False), clen_id))
        db.commit()
        synchronizuj_prihlasovanie(db)
        flash("Údaje člena boli uložené.")
        return redirect(url_for("evidencia.zo", slug=slug))
    obdobia = obdobia_clenstva(db, clen["udaje"]) if clen_id else []
    return render_template("evidencia/clen.html", zo=zo, stlpce=stlpce, clen=clen, clen_id=clen_id, farby=FARBY,
                           obdobia=obdobia, dlzka=dlzka_clenstva(obdobia))


@bp.route("/<slug>/zmazat", methods=["POST"])
def zmazat(slug):
    if not je_spravca():
        abort(403)
    db = get_db()
    db.execute("DELETE FROM evidencia_clen WHERE zo_slug = ?", (slug,))
    db.execute("DELETE FROM evidencia_stlpce WHERE zo_slug = ?", (slug,))
    db.commit()
    synchronizuj_prihlasovanie(db)
    flash("Evidencia členov tejto ZO bola zmazaná.")
    return redirect(url_for("evidencia.prehlad"))


# --- archív odídených členov -------------------------------------------------

ARCHIV_STLPCE = ["Identifikačné číslo", "Titul", "Meno poisteného", "Priezvisko / Názov poisteného",
                 "Dátum narodenia", "Dátum vstupu do poistenia", "Dátum výstupu z poistenia"]


def _archiv(slug=None):
    db = get_db()
    sql = ("SELECT a.id, a.zo_slug, a.udaje, a.farba, a.archivovane, a.dovod, z.mesto FROM evidencia_archiv a "
           "JOIN zakladna_organizacia z ON z.slug = a.zo_slug")
    riadky = db.execute(sql + (" WHERE a.zo_slug = ?" if slug else "") + " ORDER BY a.id DESC",
                        (slug,) if slug else ()).fetchall()
    return [dict(r) | {"udaje": json.loads(r["udaje"])} for r in riadky]


@bp.route("/archiv")
def archiv_vsetci():
    p = pristup()
    clen = g.get("clen")
    if p is None:
        if clen is not None and clen["rola"] == "zo" and clen["zo_slug"]:
            return redirect(url_for("evidencia.archiv_zo", slug=clen["zo_slug"]))
        if clen is None and not je_spravca():
            return redirect(url_for("ucet.prihlasenie", dalej=request.path))
        abort(403)
    g.moze_menit = p == "menit"
    return render_template("evidencia/archiv.html", zo=None, zaznamy=_archiv(), stlpce=ARCHIV_STLPCE)


@bp.route("/<slug>/archiv")
@vyzaduj()
def archiv_zo(slug):
    zo, _, _, _ = _nacitaj_zo(slug)
    return render_template("evidencia/archiv.html", zo=zo, zaznamy=_archiv(slug), stlpce=ARCHIV_STLPCE)


@bp.route("/<slug>/archiv/<int:zaznam_id>/obnovit", methods=["POST"])
@vyzaduj(menit=True)
def archiv_obnovit(slug, zaznam_id):
    db = get_db()
    r = db.execute("SELECT * FROM evidencia_archiv WHERE id = ? AND zo_slug = ?", (zaznam_id, slug)).fetchone()
    if r is None:
        abort(404)
    poradie = db.execute("SELECT COALESCE(MAX(poradie), 0) + 1 FROM evidencia_clen WHERE zo_slug = ?",
                         (slug,)).fetchone()[0]
    db.execute("INSERT INTO evidencia_clen (zo_slug, poradie, kluc, farba, udaje) VALUES (?, ?, ?, ?, ?)",
               (slug, poradie, r["kluc"], r["farba"], r["udaje"]))
    db.execute("DELETE FROM evidencia_archiv WHERE id = ?", (zaznam_id,))
    db.commit()
    synchronizuj_prihlasovanie(db)
    flash("Člen bol vrátený z archívu medzi členov.")
    return redirect(url_for("evidencia.archiv_zo", slug=slug))
