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


VSTUP_ODBORY = "Dátum vstupu do odborov"
VSTUP_ODBORY_VZOR = re.compile(r"vstup\w*\s+do\s+odbor\w*\D{0,10}?(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})", re.I)


VSTUP = "Dátum vstupu do poistenia"


def vstup_do_odborov(udaje):
    """Začiatok členstva v odboroch = dátum vstupu do poistenia (skorší dátum z poznámky ho už prepísal)."""
    return udaje.get(VSTUP, "")


def skorsi_vstup(stare, nove):
    """Dátum vstupu sa pri novom nahratí neposunie na neskorší (napr. štvrťročný zoznam bez poznámok)."""
    d_stare, d_nove = _den(stare.get(VSTUP)), _den(nove.get(VSTUP))
    if d_stare and (not d_nove or d_stare < d_nove):
        nove[VSTUP] = stare[VSTUP]


# Kratšie názvy stĺpcov na zobrazenie (v dátach a súboroch zostávajú pôvodné).
KRATKE_NAZVY = {
    "Priezvisko / Názov poisteného": "Priezvisko",
    "Adresa trvalého pobytu – ulica, číslo domu": "Ulica",
    "Adresa trvalého pobytu – mesto": "Mesto",
    "Adresa trvalého pobytu – PSČ": "PSČ",
    "Dátum vstupu do poistenia": "Vstup do poistenia",
    "Dátum výstupu z poistenia": "Výstup z poistenia",
    "MEDAILA, PLAKETA": "Ocenenia",
}


def nazov_stlpca(stlpec):
    return KRATKE_NAZVY.get(stlpec, stlpec)


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
        CREATE TABLE IF NOT EXISTS evidencia_organ (
            zo_slug TEXT NOT NULL,
            skupina TEXT NOT NULL,
            cislo TEXT NOT NULL,
            PRIMARY KEY (zo_slug, skupina, cislo)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS evidencia_foto (
            kluc TEXT PRIMARY KEY,
            typ TEXT NOT NULL,
            data BLOB NOT NULL,
            verzia TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prezencna_ulozena (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            nazov TEXT NOT NULL,
            parametre TEXT NOT NULL,
            vytvorene TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS volby_listok (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            nazov TEXT NOT NULL,
            parametre TEXT NOT NULL,
            vytvorene TEXT NOT NULL
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

    archivuj_cervenych(db)
    # Verzia 1.031 ukladala dátum z poznámky zvlášť; skorší z nich sa presunie do dátumu vstupu do poistenia.
    for r in db.execute("SELECT id, udaje FROM evidencia_clen WHERE udaje LIKE ?", (f'%"{VSTUP_ODBORY}"%',)).fetchall():
        u = json.loads(r["udaje"])
        skorsi_vstup({VSTUP: u.pop(VSTUP_ODBORY, "")}, u)
        db.execute("UPDATE evidencia_clen SET udaje = ? WHERE id = ?", (json.dumps(u, ensure_ascii=False), r["id"]))
    for r in db.execute("SELECT zo_slug, stlpce FROM evidencia_stlpce WHERE stlpce LIKE ?", (f'%"{VSTUP_ODBORY}"%',)).fetchall():
        db.execute("UPDATE evidencia_stlpce SET stlpce = ? WHERE zo_slug = ?",
                   (json.dumps([x for x in json.loads(r["stlpce"]) if x != VSTUP_ODBORY], ensure_ascii=False), r["zo_slug"]))
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
        poznamky = {k: n.text for k, n in getattr(sh, "cell_note_map", {}).items()}
        yield sh.name, riadky, farby, wb.datemode, poznamky


def _harky_xlsx(data):
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    for sh in wb.worksheets:
        riadky, farby, poznamky = [], [], {}
        for r, row in enumerate(sh.iter_rows()):
            riadky.append([c.value for c in row])
            for c, bunka in enumerate(row):
                if bunka.comment is not None:
                    poznamky[(r, c)] = bunka.comment.text
            rgb = None
            if len(row) > 4 and row[4].fill and row[4].fill.fill_type == "solid":
                farba = row[4].fill.fgColor
                if farba is not None and farba.type == "rgb" and isinstance(farba.rgb, str):
                    h = farba.rgb[-6:]
                    rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            farby.append(_farba(rgb))
        yield sh.title, riadky, farby, 0, poznamky


def precitaj_subor(nazov, data):
    """Vráti {zo_slug: (stĺpce, [(farba, {stĺpec: hodnota})])} pre hárky, ktoré patria nejakej ZO."""
    harky = _harky_xlsx(data) if nazov.lower().endswith(".xlsx") else _harky_xls(data)
    vysledok = {}
    for meno_harku, riadky, farby, datemode, poznamky in harky:
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
            # Poznámka pri bunke, napr. „Vstup do odborov 12.4.2009“, keď sa líši od vstupu do poistenia.
            for (pr, _), text in poznamky.items():
                m = VSTUP_ODBORY_VZOR.search(text or "") if pr == r else None
                if m:
                    skorsi_vstup({VSTUP: f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}"}, udaje)
            clenovia.append((farby[r] if r < len(farby) else "", udaje))
        vysledok[slug] = ([st for _, st in stlpce], clenovia)
    return vysledok


def kluc_clena(udaje):
    for k, v in udaje.items():
        if "identifikačné" in k.lower() and v:
            return "id:" + v
    casti = [v for k, v in udaje.items() if any(s in k.lower() for s in ("meno", "priezvisko", "narodenia"))]
    return "meno:" + "|".join(casti).lower()


def _bez_diakritiky(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


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


def archivuj_cervenych(db):
    """Červení v zozname sú odídení: presunú sa do archívu a zmiznú zo zoznamu členov."""
    pocet = 0
    for r in db.execute("SELECT id, zo_slug, kluc, farba, udaje FROM evidencia_clen WHERE farba = 'cervena'").fetchall():
        if db.execute("SELECT 1 FROM evidencia_archiv WHERE zo_slug = ? AND kluc = ?",
                      (r["zo_slug"], r["kluc"])).fetchone() is None:
            archivuj(db, r["zo_slug"], r["kluc"], r["farba"], r["udaje"], "odchod (červený v zozname)")
            pocet += 1
        db.execute("DELETE FROM evidencia_clen WHERE id = ?", (r["id"],))
    return pocet


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
            od = _den(vstup_do_odborov(u))
            do = _den(u.get("Dátum výstupu z poistenia")) or _den(r["archivovane"])
            if od and do:
                obdobia.append((od, do, f"ZO {r['mesto']}, číslo {u.get('Identifikačné číslo', '–')}"))
    od = _den(vstup_do_odborov(udaje))
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
    if slug is not None and not clen.get("ako_clen") and slug in zo_organov_clena(get_db(), clen["cislo"]):
        return "citat"  # výbor a revízna komisia ZO: len prezeranie
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
                        skorsi_vstup(stare, udaje)
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
                        skorsi_vstup(predtym[kluc_clena(u)], u)
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
        archivovanych += archivuj_cervenych(db)
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
    # Predchádzajúce mená si stránka drží v pozadí (spoznanie člena), nezobrazujú sa.
    stlpce = [s for s in stlpce if s.lower() != "číslo" and s != PREDCHADZAJUCE]
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
    hladat = request.args.get("hladat", "").strip()
    if hladat:
        # Každé slovo musí sedieť (napr. „Novák Ján“), bez ohľadu na diakritiku a veľké písmená.
        slova = [_bez_diakritiky(w) for w in hladat.split()]
        clenovia = [c for c in clenovia
                    if all(w in _bez_diakritiky(" ".join(c["udaje"].values())) for w in slova)]
    zoradit = request.args.get("zoradit", "subor")
    if zoradit == "abeceda":
        clenovia = sorted(clenovia, key=lambda c: (abecedne(c["udaje"].get("Priezvisko / Názov poisteného", "")),
                                                   abecedne(c["udaje"].get("Meno poisteného", ""))))
    elif zoradit == "cislo":
        clenovia = sorted(clenovia, key=lambda c: (len(c["udaje"].get("Identifikačné číslo", "")) or 99,
                                                   c["udaje"].get("Identifikačné číslo", "")))
    elif zoradit == "vstup":
        clenovia = sorted(clenovia, key=lambda c: _datum(vstup_do_odborov(c["udaje"])))
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
    mena = sorted({f"{c['udaje'].get('Priezvisko / Názov poisteného', '')} {c['udaje'].get('Meno poisteného', '')}".strip()
                   for c in vsetci} - {""}, key=abecedne)
    return render_template("evidencia/zo.html", mena=mena, organy=organy_zo(get_db(), slug, vsetci), skupiny=SKUPINY,
                           rk_miesta=RK_MIESTA, zo=zo, stlpce=stlpce, clenovia=clenovia, nahrate=nahrate,
                           hladat=hladat, zoradit=zoradit, zoradenia=ZORADENIA, farby=FARBY, zmeny=zmeny,
                           vedenie=vedenie_zo(get_db(), slug), nastavuje=funkcie_na_nastavenie(slug), na_vyber=sorted(
                               [c for c in vsetci if c["udaje"].get("Identifikačné číslo")],
                               key=lambda c: (abecedne(c["udaje"].get("Priezvisko / Názov poisteného", "")),
                                              abecedne(c["udaje"].get("Meno poisteného", "")))))


FUNKCIE = ("predseda", "podpredseda")
SKUPINY = {"vybor": "Členovia výboru ZO"}
# Revízna komisia má pevné miesta: predseda a dvaja členovia.
RK_MIESTA = [("rk_predseda", "Predseda revíznej komisie"), ("rk_clen1", "Člen revíznej komisie"),
             ("rk_clen2", "Člen revíznej komisie")]


def _cislo(c):
    return re.sub(r"\s+", "", c["udaje"].get("Identifikačné číslo", ""))


def _meno_clena(c):
    u = c["udaje"]
    return f"{u.get('Meno poisteného', '')} {u.get('Priezvisko / Názov poisteného', '')}".strip()


def organy_zo(db, slug, clenovia):
    """{skupina: [{cislo, meno}]} pre výbor ZO a revíznu komisiu, zoradené podľa priezviska."""
    podla_cisla = {_cislo(c): c for c in clenovia}
    vysledok = {k: [] for k in list(SKUPINY) + [k for k, _ in RK_MIESTA]}
    for r in db.execute("SELECT skupina, cislo FROM evidencia_organ WHERE zo_slug = ?", (slug,)):
        c = podla_cisla.get(r["cislo"])
        if r["skupina"] in vysledok:
            vysledok[r["skupina"]].append({"cislo": r["cislo"], "meno": _meno_clena(c) if c else r["cislo"],
                                           "kluc": abecedne(c["udaje"].get("Priezvisko / Názov poisteného", "")) if c else ""})
    for zoznam in vysledok.values():
        zoznam.sort(key=lambda x: x["kluc"])
    return vysledok


@bp.route("/<slug>/struktura", methods=["POST"])
def nastav_organ(slug):
    """Predseda ZO pridáva a odoberá členov výboru ZO a revíznej komisie."""
    if "podpredseda" not in funkcie_na_nastavenie(slug):
        abort(403)
    db = get_db()
    if request.form.get("akcia") == "revizna":
        platne = {_cislo(c) for c in _nacitaj_zo(slug)[2]} - {""}
        for miesto, _ in RK_MIESTA:
            cislo = re.sub(r"\s+", "", request.form.get(miesto, ""))
            db.execute("DELETE FROM evidencia_organ WHERE zo_slug = ? AND skupina = ?", (slug, miesto))
            if cislo in platne:
                db.execute("INSERT INTO evidencia_organ (zo_slug, skupina, cislo) VALUES (?, ?, ?)", (slug, miesto, cislo))
        db.commit()
        flash("Revízna komisia bola uložená.")
        return redirect(url_for("evidencia.zo", slug=slug) + "#struktura")
    skupina = request.form.get("skupina", "")
    cislo = re.sub(r"\s+", "", request.form.get("cislo", ""))
    if skupina not in SKUPINY or not cislo:
        return redirect(url_for("evidencia.zo", slug=slug))
    if request.form.get("akcia") == "odobrat":
        db.execute("DELETE FROM evidencia_organ WHERE zo_slug = ? AND skupina = ? AND cislo = ?", (slug, skupina, cislo))
    elif cislo in {_cislo(c) for c in _nacitaj_zo(slug)[2]}:
        db.execute("INSERT OR IGNORE INTO evidencia_organ (zo_slug, skupina, cislo) VALUES (?, ?, ?)", (slug, skupina, cislo))
    db.commit()
    return redirect(url_for("evidencia.zo", slug=slug) + "#struktura")


def vedenie_zo(db, slug):
    """{funkcia: {cislo, meno}} pre predsedu a podpredsedu ZO."""
    return {r["funkcia"]: {"cislo": r["cislo"], "meno": r["meno"] or r["cislo"]} for r in db.execute(
        "SELECT u.cislo, u.funkcia, COALESCE(c.meno, '') AS meno FROM ucet u LEFT JOIN clen c ON c.cislo = u.cislo "
        "WHERE u.zo_slug = ? AND u.funkcia IN ('predseda', 'podpredseda')", (slug,))}


def funkcie_na_nastavenie(slug):
    """Správca vyberá predsedu ZO, ostatné funkcie vyberá predseda tej ZO."""
    clen = g.get("clen")
    if clen and clen.get("funkcia") == "predseda" and clen.get("zo_slug") == slug:
        return [f for f in FUNKCIE if f != "predseda"] + (["predseda"] if je_spravca() else [])
    return ["predseda"] if je_spravca() else []


@bp.route("/<slug>/vedenie", methods=["POST"])
def nastav_vedenie(slug):
    """Správca vyberie predsedu ZO, predseda ZO vyberie ostatných; dostanú prístup na zmeny v svojej ZO."""
    povolene = funkcie_na_nastavenie(slug)
    if not povolene:
        abort(403)
    zo, _, clenovia, _ = _nacitaj_zo(slug)
    db = get_db()
    platne = {re.sub(r"\s+", "", c["udaje"].get("Identifikačné číslo", "")) for c in clenovia} - {""}
    for funkcia in povolene:
        if funkcia not in request.form:
            continue
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
    flash(f"Organizačná štruktúra ZO {zo['mesto']} bola uložená.")
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


def zo_organov_clena(db, cislo):
    """Slugy ZO, v ktorých je člen vo výbore alebo revíznej komisii."""
    if not cislo:
        return []
    clen = g.get("clen")
    if clen is not None and clen.get("cislo") == cislo and clen.get("moze_simulovat") and clen.get("zobrazenie"):
        return [clen["simulovany_organ"][0]] if clen.get("simulovany_organ") else []
    return [r["zo_slug"] for r in db.execute(
        "SELECT DISTINCT zo_slug FROM evidencia_organ WHERE cislo = ? ORDER BY zo_slug", (cislo,))]


def prezencnu_listinu_moze(slug):
    """Predseda, podpredseda ZO, výbor ZO, revízna komisia ZO (a správca, predsedníctvo)."""
    if pristup(slug) is not None:
        return True
    clen = g.get("clen")
    if clen is None or clen.get("ako_clen"):
        return False  # zobrazenie „ako člen“: bez prístupu k listinám
    return slug in zo_organov_clena(get_db(), clen["cislo"])


PREZENCNA_PARAMETRE = ("vzor", "nazov", "datum", "hlavicka", "adresa", "riadkov", "cislo", "hostia")


@bp.route("/<slug>/prezencna-listina", methods=["GET", "POST"])
def prezencna_listina(slug):
    """Prezenčná listina na schôdzu: zoznam členov ZO podľa abecedy so stĺpcom na podpis, na tlač."""
    if not prezencnu_listinu_moze(slug):
        if not je_spravca() and g.get("clen") is None:
            return redirect(url_for("ucet.prihlasenie", dalej=request.path))
        abort(403)
    g.moze_menit = pristup(slug) == "menit"
    db = get_db()
    if request.method == "POST":
        from urllib.parse import urlencode
        f = request.form
        if f.get("akcia") == "zmazat_listinu":
            db.execute("DELETE FROM prezencna_ulozena WHERE id = ? AND zo_slug = ?", (f.get("listina_id", ""), slug))
            db.commit()
            flash("Uložená prezenčná listina bola odstránená.")
            return redirect(url_for("evidencia.prezencna_listina", slug=slug))
        param = {k: f.get(k, "") for k in PREZENCNA_PARAMETRE if f.get(k)}
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", f.get("datum") or "")
        nazov_ul = ((f.get("nazov") or "Členská schôdza").strip()
                    + (f" {m.group(3)}.{m.group(2)}.{m.group(1)}" if m else ""))[:150]
        db.execute("INSERT INTO prezencna_ulozena (zo_slug, nazov, parametre, vytvorene) VALUES (?, ?, ?, ?)",
                   (slug, nazov_ul, urlencode(param), datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
        flash(f"Prezenčná listina „{nazov_ul}“ bola uložená.")
        return redirect(url_for("evidencia.prezencna_listina", slug=slug) + "?" + urlencode(param))
    zo, _, clenovia, _ = _nacitaj_zo(slug)
    a = request.args
    nazov = (a.get("nazov") or "Členská schôdza").strip()[:120]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", a.get("datum") or "")
    datum = f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else ""
    s_cislom = bool(a.get("cislo"))
    zoznam = []
    for c in clenovia:
        u = c["udaje"]
        priezvisko = u.get("Priezvisko / Názov poisteného", "").strip()
        meno = u.get("Meno poisteného", "").strip()
        if not (priezvisko or meno):
            continue
        zoznam.append({"titul": u.get("Titul", "").strip(), "meno": meno, "priezvisko": priezvisko,
                       "titul_za": u.get("Titul za menom", "").strip(),
                       "cislo": re.sub(r"\s+", "", u.get("Identifikačné číslo", ""))})
    zoznam.sort(key=lambda z: (abecedne(z["priezvisko"]), abecedne(z["meno"])))
    vzor = a.get("vzor") if a.get("vzor") in ("mena", "prazdna") else "mena"
    riadkov = int(a["riadkov"]) if (a.get("riadkov") or "").isdigit() else max(len(zoznam), 40)
    riadkov = min(max(riadkov, 1), 300)
    hostia = int(a["hostia"]) if (a.get("hostia") or "").isdigit() else 3
    organy = organy_zo(get_db(), slug, clenovia)
    rk_predseda, rk_funkcia = "", "Predseda revíznej komisie"
    for kluc, funkcia in RK_MIESTA:  # chýba predseda RK → ďalší člen revíznej komisie
        if organy.get(kluc):
            rk_predseda, rk_funkcia = organy[kluc][0]["meno"], funkcia
            break
    return render_template("evidencia/prezencna_listina.html", zo=zo, zoznam=zoznam, nazov=nazov,
                           datum=a.get("datum", ""), datum_text=datum, s_cislom=s_cislom, vzor=vzor,
                           riadkov=riadkov, hostia=min(hostia, 20), hlavicka=(a.get("hlavicka") or "").strip()[:200],
                           adresa=(a.get("adresa") or "").strip()[:200], uz_zadane=bool(a),
                           ulozene=db.execute("SELECT * FROM prezencna_ulozena WHERE zo_slug = ? ORDER BY id DESC",
                                              (slug,)).fetchall(),
                           rk_predseda=rk_predseda, rk_funkcia=rk_funkcia)


VOLBY_PARAMETRE = ("nazov", "podnadpis", "kandidati", "pokyn", "pocet", "od", "na_stranu", "peciatka", "styl")


def text_peciatky(db, zo):
    """Text pečiatky ZO na zadnú stranu hlasovacieho lístka (uložený pre ZO, inak názov ZO)."""
    r = db.execute("SELECT hodnota FROM nastavenie WHERE kluc = ?", (f"peciatka:{zo['slug']}",)).fetchone()
    return r["hodnota"] if r else zo["nazov"]


@bp.route("/<slug>/volby", methods=["GET", "POST"])
def volby(slug):
    """Voľby: hlasovacie lístky na tlač (niekoľko na A4, poradové číslo, vzadu pečiatka ZO)."""
    if not prezencnu_listinu_moze(slug):
        if not je_spravca() and g.get("clen") is None:
            return redirect(url_for("ucet.prihlasenie", dalej=request.path))
        abort(403)
    g.moze_menit = pristup(slug) == "menit"
    db = get_db()
    if request.method == "POST" and request.form.get("akcia") == "ulozit_listok":
        from urllib.parse import urlencode
        f = request.form
        param = {k: f.get(k, "") for k in VOLBY_PARAMETRE if f.get(k)}
        nazov = (f.get("podnadpis") or f.get("nazov") or "Hlasovací lístok").strip()[:120]
        db.execute("INSERT INTO volby_listok (zo_slug, nazov, parametre, vytvorene) VALUES (?, ?, ?, ?)",
                   (slug, nazov, urlencode(param), datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
        flash(f"Hlasovací lístok „{nazov}“ bol uložený.")
        return redirect(url_for("evidencia.volby", slug=slug) + "?" + urlencode(param))
    if request.method == "POST" and request.form.get("akcia") == "zmazat_listok":
        db.execute("DELETE FROM volby_listok WHERE id = ? AND zo_slug = ?", (request.form.get("listok_id", ""), slug))
        db.commit()
        flash("Uložený hlasovací lístok bol odstránený.")
        return redirect(url_for("evidencia.volby", slug=slug))
    if request.method == "POST":  # uloženie textu pečiatky ZO
        if not g.moze_menit:
            abort(403)
        text = "\n".join(r.strip() for r in request.form.get("peciatka", "").strip().splitlines())[:600]
        if text:
            db.execute("INSERT INTO nastavenie (kluc, hodnota) VALUES (?, ?) "
                       "ON CONFLICT(kluc) DO UPDATE SET hodnota = excluded.hodnota", (f"peciatka:{slug}", text))
        else:
            db.execute("DELETE FROM nastavenie WHERE kluc = ?", (f"peciatka:{slug}",))
        db.commit()
        flash("Text pečiatky bol uložený.")
        return redirect(url_for("evidencia.volby", slug=slug))
    zo, _, clenovia, _ = _nacitaj_zo(slug)
    a = request.args
    def cislo(kluc, predvolene, od, do):
        v = a.get(kluc) or ""
        return min(max(int(v), od), do) if v.isdigit() else predvolene
    pocet = cislo("pocet", max(len(clenovia), 1), 1, 1000)
    od = cislo("od", 1, 1, 100000)
    na_stranu = int(a["na_stranu"]) if a.get("na_stranu") in ("2", "3", "4", "8") else 8
    kandidati = [k.strip()[:120] for k in (a.get("kandidati") or "").splitlines() if k.strip()][:30]
    peciatka = text_peciatky(db, zo)
    s_peciatkou = a.get("peciatka") == "1" or not a
    styl = a.get("styl") if a.get("styl") in ("razitko", "text") else "razitko"
    cisla = list(range(od, od + pocet))
    strany = [cisla[i:i + na_stranu] for i in range(0, len(cisla), na_stranu)]
    return render_template("evidencia/volby.html", zo=zo, strany=strany, pocet=pocet, od=od, na_stranu=na_stranu,
                           nazov=(a.get("nazov") or "Hlasovací lístok").strip()[:120],
                           podnadpis=(a.get("podnadpis") or "").strip()[:200],
                           pokyn=(a.get("pokyn") or "").strip()[:300], kandidati=kandidati,
                           kandidati_text="\n".join(kandidati), peciatka=peciatka, s_peciatkou=s_peciatkou, styl=styl,
                           ulozene=db.execute("SELECT * FROM volby_listok WHERE zo_slug = ? ORDER BY id DESC", (slug,)).fetchall(),
                           uz_zadane=bool(a))


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
        flash("Člen je označený ako odídený (červený) a bol presunutý do archívu odídených."
              if farba == "cervena" else "Údaje člena boli uložené.")
        return redirect(url_for("evidencia.zo", slug=slug))
    obdobia = obdobia_clenstva(db, clen["udaje"]) if clen_id else []
    foto = nacitaj_foto(db, kluc_clena(clen["udaje"])) if clen_id else None
    return render_template("evidencia/clen.html", zo=zo, stlpce=stlpce, clen=clen, clen_id=clen_id, farby=FARBY,
                           obdobia=obdobia, dlzka=dlzka_clenstva(obdobia), foto=foto)


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
               (slug, poradie, r["kluc"], "" if r["farba"] == "cervena" else r["farba"], r["udaje"]))
    db.execute("DELETE FROM evidencia_archiv WHERE id = ?", (zaznam_id,))
    db.commit()
    synchronizuj_prihlasovanie(db)
    flash("Člen bol vrátený z archívu medzi členov.")
    return redirect(url_for("evidencia.archiv_zo", slug=slug))


# --- fotky členov -------------------------------------------------------------

MAX_FOTO = 8 * 1024 * 1024


def nacitaj_foto(db, kluc):
    return db.execute("SELECT typ, data, verzia FROM evidencia_foto WHERE kluc = ?", (kluc,)).fetchone()


def uprav_fotku(data):
    """Zmenší fotku na najviac 800 px a uloží ako JPEG (ak je k dispozícii knižnica Pillow)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, None
    obr = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    obr = obr.convert("RGB")
    obr.thumbnail((800, 800))
    vystup = io.BytesIO()
    obr.save(vystup, "JPEG", quality=85)
    return vystup.getvalue(), "image/jpeg"


def _clen_v_zo(slug, clen_id):
    r = get_db().execute("SELECT udaje FROM evidencia_clen WHERE id = ? AND zo_slug = ?", (clen_id, slug)).fetchone()
    if r is None:
        abort(404)
    return json.loads(r["udaje"])


@bp.route("/<slug>/clen/<int:clen_id>/foto", methods=["GET", "POST"])
@vyzaduj()
def foto(slug, clen_id):
    udaje = _clen_v_zo(slug, clen_id)
    db = get_db()
    kluc = kluc_clena(udaje)
    if request.method == "POST":
        if not g.moze_menit:
            abort(403)
        if request.form.get("akcia") == "zmazat":
            db.execute("DELETE FROM evidencia_foto WHERE kluc = ?", (kluc,))
            db.commit()
            flash("Fotka bola odstránená.")
            return redirect(url_for("evidencia.uprav_clena", slug=slug, clen_id=clen_id))
        subor = request.files.get("foto")
        data = subor.read() if subor else b""
        if not data:
            flash("Vyberte fotku.")
        elif len(data) > MAX_FOTO:
            flash("Fotka je príliš veľká (najviac 8 MB).")
        else:
            typ = subor.mimetype or ""
            try:
                data, novy_typ = uprav_fotku(data)
                typ = novy_typ or typ
            except Exception:
                flash("Súbor sa nepodarilo otvoriť ako obrázok. Použite fotku vo formáte JPG alebo PNG.")
                return redirect(url_for("evidencia.uprav_clena", slug=slug, clen_id=clen_id))
            if not typ.startswith("image/"):
                flash("Súbor nie je obrázok.")
                return redirect(url_for("evidencia.uprav_clena", slug=slug, clen_id=clen_id))
            db.execute("INSERT INTO evidencia_foto (kluc, typ, data, verzia) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT(kluc) DO UPDATE SET typ = excluded.typ, data = excluded.data, verzia = excluded.verzia",
                       (kluc, typ, data, datetime.now().strftime("%Y%m%d%H%M%S")))
            if not udaje.get("Fotka"):
                udaje["Fotka"] = "áno"
                db.execute("UPDATE evidencia_clen SET udaje = ? WHERE id = ?",
                           (json.dumps(udaje, ensure_ascii=False), clen_id))
            db.commit()
            flash("Fotka bola uložená.")
        return redirect(url_for("evidencia.uprav_clena", slug=slug, clen_id=clen_id))
    f = nacitaj_foto(db, kluc)
    if f is None:
        abort(404)
    return Response(f["data"], mimetype=f["typ"], headers={"Cache-Control": "private, max-age=86400"})


bp.add_app_template_filter(nazov_stlpca, "stlpec")
