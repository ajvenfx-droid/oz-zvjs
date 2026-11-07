"""Evidencia požičiavania hodiniek (permanentiek) na plaváreň v základnej organizácii.

Každé hodinky majú zostatok (koľko na nich ešte ostáva). Pri požičaní sa zapíše dátum,
člen a zostatok po návrate – rovnako ako v doterajšej excelovej tabuľke (hárok na mesiac,
pri každých hodinkách stĺpec „Meno a Priezvisko“ a zostatok).

Prístup: rovnaký ako k evidencii členov ZO (predseda, podpredseda, správca mení; vedenie len číta).
"""
import calendar
import io
import re
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from db import get_db
from evidencia import _bez_diakritiky, _nacitaj_zo, abecedne, vyzaduj

bp = Blueprint("hodinky", __name__, url_prefix="/spravca/evidencia")

MESIACE = ["január", "február", "marec", "apríl", "máj", "jún", "júl", "august", "september", "október",
           "november", "december"]


def init_db(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS hodinky (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            nazov TEXT NOT NULL,
            poradie INTEGER NOT NULL DEFAULT 0,
            pociatocny_zostatok INTEGER,
            aktivne INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS hodinky_zaznam (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hodinky_id INTEGER NOT NULL REFERENCES hodinky(id) ON DELETE CASCADE,
            datum TEXT NOT NULL,              -- YYYY-MM-DD
            typ TEXT NOT NULL DEFAULT 'pozicanie',   -- pozicanie | dobitie
            cislo TEXT NOT NULL DEFAULT '',   -- identifikačné číslo člena, ak sa dalo určiť
            meno TEXT NOT NULL DEFAULT '',
            zostatok INTEGER,                 -- zostatok na hodinkách po vrátení
            zapisal TEXT NOT NULL DEFAULT '',
            cas TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS hodinky_zaznam_datum ON hodinky_zaznam (hodinky_id, datum);
        CREATE TABLE IF NOT EXISTS benefit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zo_slug TEXT NOT NULL,
            nazov TEXT NOT NULL,
            popis TEXT NOT NULL DEFAULT '',
            typ TEXT NOT NULL DEFAULT 'text',   -- text | hodinky (evidencia požičiavania hodiniek)
            poradie INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    # prvé spustenie: ZO Banská Bystrica má benefit hodinky na plaváreň
    if db.execute("SELECT 1 FROM benefit LIMIT 1").fetchone() is None:
        db.execute("INSERT INTO benefit (zo_slug, nazov, popis, typ, poradie) VALUES (?, ?, ?, 'hodinky', 1)",
                   ("banska-bystrica", "PLAVÁREŇ - permanentky",
                    "Požičiavanie hodiniek (permanentiek) na plaváreň pre členov ZO."))
        db.commit()
    # premenovanie podľa želania predsedu ZO BB
    db.execute("UPDATE benefit SET nazov = 'PLAVÁREŇ - permanentky' WHERE typ = 'hodinky' AND nazov = 'Hodinky na plaváreň'")
    db.commit()


# --- pomocné ------------------------------------------------------------------


def _slova(text):
    return frozenset(w for w in re.split(r"[\s,.]+", _bez_diakritiky(text or "")) if w)


def clenovia_zo(slug):
    """[(zobrazenie „Priezvisko Meno (číslo)“, číslo, meno)] a index slov mena -> číslo."""
    zoznam, index = [], {}
    for c in _nacitaj_zo(slug)[2]:
        u = c["udaje"]
        cislo = re.sub(r"\s+", "", u.get("Identifikačné číslo", ""))
        meno = f"{u.get('Priezvisko / Názov poisteného', '')} {u.get('Meno poisteného', '')}".strip()
        if not meno:
            continue
        zoznam.append((f"{meno} ({cislo})" if cislo else meno, cislo, meno))
        if cislo:
            index.setdefault(_slova(meno), cislo)
    zoznam.sort(key=lambda z: abecedne(z[2]))
    return zoznam, index


def urci_clena(text, slug, index=None):
    """Z textu „Priezvisko Meno (číslo)“ alebo len mena určí (číslo, meno)."""
    text = (text or "").strip()
    m = re.match(r"^(.*?)\s*\((\d+)\)\s*$", text)
    if m:
        return m.group(2), m.group(1).strip()
    if index is None:
        index = clenovia_zo(slug)[1]
    return index.get(_slova(text), ""), text


def hodinky_zo(db, slug, aj_neaktivne=True):
    return db.execute(
        "SELECT * FROM hodinky WHERE zo_slug = ? " + ("" if aj_neaktivne else "AND aktivne = 1 ") +
        "ORDER BY poradie, id", (slug,)).fetchall()


def aktualny_stav(db, h):
    """(zostatok, posledný záznam) – zostatok z posledného záznamu so zostatkom, inak počiatočný."""
    posledny = db.execute("SELECT * FROM hodinky_zaznam WHERE hodinky_id = ? ORDER BY datum DESC, id DESC LIMIT 1",
                          (h["id"],)).fetchone()
    so_zostatkom = db.execute(
        "SELECT zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND zostatok IS NOT NULL "
        "ORDER BY datum DESC, id DESC LIMIT 1", (h["id"],)).fetchone()
    return (so_zostatkom["zostatok"] if so_zostatkom else h["pociatocny_zostatok"]), posledny


def minute_body(db, h):
    """{id záznamu: minuté body} = predchádzajúci zapísaný zostatok − zostatok pri mene."""
    minul, pred = {}, h["pociatocny_zostatok"]
    for z in db.execute("SELECT id, typ, zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND zostatok IS NOT NULL "
                        "ORDER BY datum, id", (h["id"],)):
        if z["typ"] == "pozicanie" and pred is not None:
            minul[z["id"]] = pred - z["zostatok"]
        pred = z["zostatok"]
    return minul


def _mesiac(text):
    m = re.match(r"^(\d{4})-(\d{1,2})$", text or "")
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    d = date.today()
    return d.year, d.month


def _posun(rok, mesiac, o):
    m = rok * 12 + (mesiac - 1) + o
    return m // 12, m % 12 + 1


def _datum(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


# --- benefity ZO ------------------------------------------------------------


TYPY_BENEFITU = {"text": "Bežný benefit (názov a popis)", "hodinky": "Plaváreň – permanentky (evidencia požičiavania)"}


@bp.route("/<slug>/benefity")
@vyzaduj()
def benefity(slug):
    db = get_db()
    zo = _nacitaj_zo(slug)[0]
    zoznam = db.execute("SELECT * FROM benefit WHERE zo_slug = ? ORDER BY poradie, id", (slug,)).fetchall()
    return render_template("hodinky/benefity.html", zo=zo, slug=slug, benefity=zoznam, typy=TYPY_BENEFITU)


@bp.route("/<slug>/benefity/uloz", methods=["POST"])
@vyzaduj(menit=True)
def uloz_benefit(slug):
    db = get_db()
    f = request.form
    akcia = f.get("akcia")
    if akcia == "pridat":
        nazov, typ = f.get("nazov", "").strip(), f.get("typ", "text")
        if typ not in TYPY_BENEFITU:
            abort(400)
        if typ == "hodinky" and not nazov:
            nazov = "PLAVÁREŇ - permanentky"
        if not nazov:
            flash("Zadajte názov benefitu.")
            return redirect(url_for("hodinky.benefity", slug=slug))
        poradie = db.execute("SELECT COALESCE(MAX(poradie), 0) + 1 FROM benefit WHERE zo_slug = ?", (slug,)).fetchone()[0]
        db.execute("INSERT INTO benefit (zo_slug, nazov, popis, typ, poradie) VALUES (?, ?, ?, ?, ?)",
                   (slug, nazov, f.get("popis", "").strip(), typ, poradie))
        flash(f"Pridaný benefit {nazov}.")
    else:
        b = db.execute("SELECT * FROM benefit WHERE id = ? AND zo_slug = ?",
                       (int(f.get("benefit_id", 0) or 0), slug)).fetchone()
        if b is None:
            abort(404)
        if akcia == "upravit":
            nazov = f.get("nazov", "").strip()
            if nazov:
                db.execute("UPDATE benefit SET nazov = ?, popis = ? WHERE id = ?", (nazov, f.get("popis", "").strip(), b["id"]))
                flash("Benefit bol upravený.")
        elif akcia == "zmazat":
            db.execute("DELETE FROM benefit WHERE id = ?", (b["id"],))
            flash(f"Benefit {b['nazov']} bol odstránený."
                  + (" Záznamy o hodinkách zostali uložené." if b["typ"] == "hodinky" else ""))
    db.commit()
    return redirect(url_for("hodinky.benefity", slug=slug))


# --- stránka ------------------------------------------------------------------


@bp.route("/<slug>/hodinky")
@vyzaduj()
def prehlad(slug):
    db = get_db()
    zo = _nacitaj_zo(slug)[0]
    rok, mesiac = _mesiac(request.args.get("mesiac"))
    vsetky = hodinky_zo(db, slug)
    stav = []
    for h in vsetky:
        zostatok, posledny = aktualny_stav(db, h)
        stav.append({"h": h, "zostatok": zostatok, "posledny": posledny})
    pocet_dni = calendar.monthrange(rok, mesiac)[1]
    od, do = f"{rok:04d}-{mesiac:02d}-01", f"{rok:04d}-{mesiac:02d}-{pocet_dni:02d}"
    zaznamy = {}
    for z in db.execute(
            "SELECT z.* FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id "
            "WHERE h.zo_slug = ? AND z.datum BETWEEN ? AND ? ORDER BY z.datum, z.id", (slug, od, do)):
        zaznamy.setdefault((z["datum"], z["hodinky_id"]), []).append(z)
    # stĺpce mesiaca: aktívne hodinky + neaktívne, ktoré v mesiaci majú záznam
    stlpce = [h for h in vsetky if h["aktivne"] or any(k[1] == h["id"] for k in zaznamy)]
    dni = [(f"{rok:04d}-{mesiac:02d}-{d:02d}", d, date(rok, mesiac, d).weekday() >= 5) for d in range(1, pocet_dni + 1)]
    # štatistika za rok
    statistika = [dict(r) for r in db.execute(
        "SELECT CASE WHEN z.cislo != '' THEN z.cislo ELSE z.meno END AS kluc, MAX(z.meno) AS meno, z.cislo, "
        "COUNT(DISTINCT z.datum) AS dni, COUNT(*) AS kusov FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id "
        "WHERE h.zo_slug = ? AND z.typ = 'pozicanie' AND z.datum BETWEEN ? AND ? GROUP BY kluc "
        "ORDER BY dni DESC, kusov DESC", (slug, f"{rok}-01-01", f"{rok}-12-31"))]
    clenovia, _ = clenovia_zo(slug)
    minul = {}
    for h in stlpce:
        minul.update(minute_body(db, h))
    return render_template(
        "hodinky/prehlad.html", minul=minul, zo=zo, slug=slug, stav=stav, stlpce=stlpce, dni=dni, zaznamy=zaznamy,
        rok=rok, mesiac=mesiac, nazov_mesiaca=MESIACE[mesiac - 1], predch="%04d-%02d" % _posun(rok, mesiac, -1),
        dalsi="%04d-%02d" % _posun(rok, mesiac, 1), dnes=date.today().isoformat(), clenovia=clenovia,
        statistika=statistika, aktivne=[h for h in vsetky if h["aktivne"]])


@bp.route("/<slug>/hodinky/<int:hodinky_id>")
@vyzaduj()
def historia(slug, hodinky_id):
    """Prehľad, kto mal dané hodinky (od najnovšieho)."""
    db = get_db()
    zo = _nacitaj_zo(slug)[0]
    h = _hodinky_zo(db, slug, hodinky_id)
    zaznamy = db.execute("SELECT * FROM hodinky_zaznam WHERE hodinky_id = ? ORDER BY datum DESC, id DESC",
                         (h["id"],)).fetchall()
    zostatok, _ = aktualny_stav(db, h)
    minul = minute_body(db, h)
    pocty = {}
    for z in zaznamy:
        if z["typ"] == "pozicanie":
            k = z["cislo"] or z["meno"]
            p = pocty.setdefault(k, [z["meno"], z["cislo"], 0, 0])
            p[2] += 1
            p[3] += minul.get(z["id"]) or 0
    return render_template("hodinky/historia.html", zo=zo, slug=slug, h=h, zaznamy=zaznamy, zostatok=zostatok,
                           minul=minul, pocty=sorted(pocty.values(), key=lambda p: (-p[2], abecedne(p[0]))))


def _spat(slug):
    return redirect(url_for("hodinky.prehlad", slug=slug, mesiac=request.form.get("mesiac") or None))


def _hodinky_zo(db, slug, hodinky_id):
    h = db.execute("SELECT * FROM hodinky WHERE id = ? AND zo_slug = ?", (hodinky_id, slug)).fetchone()
    if h is None:
        abort(404)
    return h


def _kto():
    c = g.get("clen")
    return c["meno"] if c and c.get("meno") else "správca"


@bp.route("/<slug>/hodinky/pozicanie", methods=["POST"])
@vyzaduj(menit=True)
def pozicanie(slug):
    db = get_db()
    f = request.form
    datum = _datum(f.get("datum"))
    cislo, meno = urci_clena(f.get("clen"), slug)
    if not datum:
        flash("Zadajte dátum.")
        return _spat(slug)
    if not meno:
        flash("Zadajte meno člena.")
        return _spat(slug)
    cas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pocet = 0
    for h in hodinky_zo(db, slug, aj_neaktivne=False):
        if not f.get(f"hodinky_{h['id']}"):
            continue
        zostatok = f.get(f"zostatok_{h['id']}", "").strip()
        if zostatok and not re.fullmatch(r"-?\d+", zostatok):
            flash(f"Zostatok pri hodinkách {h['nazov']} musí byť celé číslo.")
            return _spat(slug)
        db.execute("INSERT INTO hodinky_zaznam (hodinky_id, datum, typ, cislo, meno, zostatok, zapisal, cas) "
                   "VALUES (?, ?, 'pozicanie', ?, ?, ?, ?, ?)",
                   (h["id"], datum, cislo, meno, int(zostatok) if zostatok else None, _kto(), cas))
        pocet += 1
    if not pocet:
        flash("Označte aspoň jedny hodinky.")
        return _spat(slug)
    db.commit()
    flash(f"Zapísané: {meno}, {pocet} {'hodinky' if pocet < 5 else 'hodiniek'}."
          + ("" if cislo else " Meno sa nenašlo v zozname členov ZO, uložilo sa tak, ako je napísané."))
    return _spat(slug)


@bp.route("/<slug>/hodinky/zaznam/<int:zaznam_id>/zmazat", methods=["POST"])
@vyzaduj(menit=True)
def zmaz_zaznam(slug, zaznam_id):
    db = get_db()
    z = db.execute("SELECT z.id FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id "
                   "WHERE z.id = ? AND h.zo_slug = ?", (zaznam_id, slug)).fetchone()
    if z is None:
        abort(404)
    db.execute("DELETE FROM hodinky_zaznam WHERE id = ?", (zaznam_id,))
    db.commit()
    flash("Záznam bol zmazaný.")
    return _spat(slug)


@bp.route("/<slug>/hodinky/sprava", methods=["POST"])
@vyzaduj(menit=True)
def sprava(slug):
    """Pridanie hodiniek, premenovanie, vyradenie a dobitie (nový zostatok)."""
    db = get_db()
    f = request.form
    akcia = f.get("akcia")
    if akcia == "pridat":
        nazov = f.get("nazov", "").strip()
        zostatok = f.get("zostatok", "").strip()
        if not nazov:
            flash("Zadajte názov hodiniek.")
            return _spat(slug)
        poradie = db.execute("SELECT COALESCE(MAX(poradie), 0) + 1 FROM hodinky WHERE zo_slug = ?", (slug,)).fetchone()[0]
        db.execute("INSERT INTO hodinky (zo_slug, nazov, poradie, pociatocny_zostatok) VALUES (?, ?, ?, ?)",
                   (slug, nazov, poradie, int(zostatok) if re.fullmatch(r"-?\d+", zostatok) else None))
        flash(f"Pridané hodinky {nazov}.")
    else:
        h = _hodinky_zo(db, slug, int(f.get("hodinky_id", 0) or 0))
        if akcia == "premenovat":
            nazov = f.get("nazov", "").strip()
            if nazov:
                db.execute("UPDATE hodinky SET nazov = ? WHERE id = ?", (nazov, h["id"]))
        elif akcia == "vyradit":
            db.execute("UPDATE hodinky SET aktivne = 1 - aktivne WHERE id = ?", (h["id"],))
        elif akcia == "dobitie":
            zostatok = f.get("zostatok", "").strip()
            datum = _datum(f.get("datum")) or date.today().isoformat()
            if not re.fullmatch(r"-?\d+", zostatok):
                flash("Zadajte nový zostatok ako celé číslo.")
                return _spat(slug)
            db.execute("INSERT INTO hodinky_zaznam (hodinky_id, datum, typ, meno, zostatok, zapisal, cas) "
                       "VALUES (?, ?, 'dobitie', 'Dobitie / nový zostatok', ?, ?, ?)",
                       (h["id"], datum, int(zostatok), _kto(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            flash(f"Hodinky {h['nazov']}: nový zostatok {zostatok}.")
        elif akcia == "zmazat":
            if db.execute("SELECT 1 FROM hodinky_zaznam WHERE hodinky_id = ?", (h["id"],)).fetchone():
                flash("Hodinky majú záznamy, preto ich nie je možné zmazať. Môžete ich vyradiť.")
                return _spat(slug)
            db.execute("DELETE FROM hodinky WHERE id = ?", (h["id"],))
    db.commit()
    return _spat(slug)


# --- import a export Excelu ---------------------------------------------------


def precitaj_excel(data):
    """Z tabuľky v tvare „HODINKY 2025-2026.xlsx“ vráti (hodinky, záznamy).

    hodinky: [(názov, počiatočný zostatok)] v poradí, ako sú v hárkoch;
    záznamy: [(názov hodiniek, dátum ISO, meno, zostatok alebo None)].
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    poradie, pociatok, zaznamy, posledny_harok = [], {}, [], set()
    for ws in wb.worksheets:
        riadky = list(ws.iter_rows(values_only=True))
        if len(riadky) < 3 or str(riadky[0][0] or "").strip().lower() != "hodinky":
            continue
        hlavicka = riadky[0]
        stlpce = []  # (index stĺpca s menom, názov hodiniek)
        posledny_harok = set()
        for i in range(1, len(hlavicka), 2):
            if hlavicka[i] is not None and str(hlavicka[i]).strip() != "":
                nazov = str(hlavicka[i]).strip()
                if isinstance(hlavicka[i], float) and hlavicka[i].is_integer():
                    nazov = str(int(hlavicka[i]))
                stlpce.append((i, nazov))
                posledny_harok.add(nazov)
                if nazov not in pociatok:
                    poradie.append(nazov)
                    z = riadky[1][i + 1] if len(riadky[1]) > i + 1 else None
                    pociatok[nazov] = int(z) if isinstance(z, (int, float)) else None
        for r in riadky[2:]:
            d = r[0]
            if not isinstance(d, (datetime, date)):
                continue
            d = d.date() if isinstance(d, datetime) else d
            for i, nazov in stlpce:
                meno = r[i] if len(r) > i else None
                if meno is None or str(meno).strip() == "":
                    continue
                z = r[i + 1] if len(r) > i + 1 else None
                zaznamy.append((nazov, d.isoformat(), str(meno).strip(), int(z) if isinstance(z, (int, float)) else None))
    return [(n, pociatok[n], n in posledny_harok) for n in poradie], zaznamy


@bp.route("/<slug>/hodinky/import", methods=["POST"])
@vyzaduj(menit=True)
def importuj(slug):
    subor = request.files.get("subor")
    if not subor or not subor.filename.lower().endswith(".xlsx"):
        flash("Vyberte súbor .xlsx.")
        return _spat(slug)
    try:
        nove_hodinky, zaznamy = precitaj_excel(subor.read())
    except Exception:  # poškodený alebo iný súbor
        flash("Súbor sa nepodarilo prečítať. Musí to byť tabuľka hodiniek (.xlsx).")
        return _spat(slug)
    if not nove_hodinky:
        flash("V súbore sa nenašli hárky s hodinkami (prvá bunka „Hodinky“).")
        return _spat(slug)
    db = get_db()
    _, index = clenovia_zo(slug)
    # Import nahradí doterajšie hodinky a záznamy tejto ZO.
    db.execute("DELETE FROM hodinky WHERE zo_slug = ?", (slug,))
    ids = {}
    # hodinky, ktoré už nie sú v poslednom hárku (napr. „5 Čierne“), sa uložia ako vyradené
    for i, (nazov, pociatok, aktivne) in enumerate(nove_hodinky, 1):
        ids[nazov] = db.execute("INSERT INTO hodinky (zo_slug, nazov, poradie, pociatocny_zostatok, aktivne) "
                                "VALUES (?, ?, ?, ?, ?)", (slug, nazov, i, pociatok, int(aktivne))).lastrowid
    cas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nenajdene = set()
    for nazov, datum, meno, zostatok in zaznamy:
        cislo, meno = urci_clena(meno, slug, index)
        if not cislo:
            nenajdene.add(meno)
        db.execute("INSERT INTO hodinky_zaznam (hodinky_id, datum, typ, cislo, meno, zostatok, zapisal, cas) "
                   "VALUES (?, ?, 'pozicanie', ?, ?, ?, ?, ?)", (ids[nazov], datum, cislo, meno, zostatok, "import", cas))
    db.commit()
    flash(f"Nahraté: {len(nove_hodinky)} hodiniek, {len(zaznamy)} záznamov."
          + (f" {len(nenajdene)} mien sa nenašlo v zozname členov ZO (uložené podľa súboru)." if nenajdene else ""))
    return _spat(slug)


@bp.route("/<slug>/hodinky/export.xlsx")
@vyzaduj()
def export(slug):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    db = get_db()
    vsetky = hodinky_zo(db, slug)
    zaznamy = db.execute(
        "SELECT z.*, h.nazov FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id WHERE h.zo_slug = ? "
        "ORDER BY z.datum, z.id", (slug,)).fetchall()
    wb = Workbook()
    wb.remove(wb.active)
    mesiace = sorted({z["datum"][:7] for z in zaznamy}) or [date.today().strftime("%Y-%m")]
    tucne, vikend = Font(bold=True), PatternFill("solid", fgColor="EEEEEE")
    for mm in mesiace:
        rok, mes = int(mm[:4]), int(mm[5:7])
        ws = wb.create_sheet(f"{mes}-{rok}")
        v_mesiaci = [z for z in zaznamy if z["datum"][:7] == mm]
        stlpce = [h for h in vsetky if h["aktivne"] or any(z["hodinky_id"] == h["id"] for z in v_mesiaci)]
        ws.cell(1, 1, "Hodinky").font = tucne
        ws.cell(2, 1, "Dátum").font = tucne
        for j, h in enumerate(stlpce):
            c = 2 + 2 * j
            ws.cell(1, c, h["nazov"]).font = tucne
            ws.merge_cells(start_row=1, start_column=c, end_row=1, end_column=c + 1)
            ws.cell(1, c).alignment = Alignment(horizontal="center")
            ws.cell(2, c, "Meno a Priezvisko").font = tucne
            # zostatok na začiatku mesiaca
            pred = db.execute("SELECT zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND datum < ? "
                              "AND zostatok IS NOT NULL ORDER BY datum DESC, id DESC LIMIT 1", (h["id"], mm + "-01")).fetchone()
            ws.cell(2, c + 1, pred["zostatok"] if pred else h["pociatocny_zostatok"]).font = tucne
            ws.column_dimensions[ws.cell(1, c).column_letter].width = 24
        pocet_dni = calendar.monthrange(rok, mes)[1]
        for d in range(1, pocet_dni + 1):
            r = 2 + d
            den = date(rok, mes, d)
            ws.cell(r, 1, den).number_format = "D.M.YYYY"
            for j, h in enumerate(stlpce):
                c = 2 + 2 * j
                tu = [z for z in v_mesiaci if z["hodinky_id"] == h["id"] and z["datum"] == den.isoformat()]
                if tu:
                    ws.cell(r, c, ", ".join(z["meno"] for z in tu))
                    zost = [z["zostatok"] for z in tu if z["zostatok"] is not None]
                    if zost:
                        ws.cell(r, c + 1, zost[-1])
            if den.weekday() >= 5:
                for c in range(1, 2 + 2 * len(stlpce)):
                    ws.cell(r, c).fill = vikend
        r = 4 + pocet_dni
        ws.cell(r, 1, "ZOSTATOK").font = tucne
        for j, h in enumerate(stlpce):
            posl = db.execute("SELECT zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND datum <= ? "
                              "AND zostatok IS NOT NULL ORDER BY datum DESC, id DESC LIMIT 1",
                              (h["id"], f"{mm}-{pocet_dni:02d}")).fetchone()
            ws.cell(r, 3 + 2 * j, posl["zostatok"] if posl else h["pociatocny_zostatok"]).font = tucne
        ws.column_dimensions["A"].width = 12
        ws.freeze_panes = "B3"
    out = io.BytesIO()
    wb.save(out)
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="hodinky-{slug}.xlsx"'})
