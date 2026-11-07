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
bp_clen = Blueprint("hodinky_clen", __name__, url_prefix="/benefity")
DNI_REZERVACIE = 30  # na koľko dní dopredu si člen môže rezervovať
MAX_HODIN = 48  # rezervácia najviac na 2 dni
DNI_TYZDNA = ["po", "ut", "st", "št", "pi", "so", "ne"]

NEZISTENE = "Nezistené – nevie sa, kto mal"

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
            typ TEXT NOT NULL DEFAULT 'pozicanie',   -- pozicanie | dobitie | rozdiel (úbytok, nevie sa kto mal)
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
    # rezervácie: čas od – do (pôvodne len deň, verzia 1.081)
    stlpce = [r[1] for r in db.execute("PRAGMA table_info(hodinky_rezervacia)")]
    if stlpce and "od" not in stlpce:
        db.execute("ALTER TABLE hodinky_rezervacia RENAME TO hodinky_rezervacia_stara")
    db.execute(
        """CREATE TABLE IF NOT EXISTS hodinky_rezervacia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hodinky_id INTEGER NOT NULL REFERENCES hodinky(id) ON DELETE CASCADE,
            od TEXT NOT NULL,                 -- YYYY-MM-DD HH:MM
            do TEXT NOT NULL,
            cislo TEXT NOT NULL,
            meno TEXT NOT NULL DEFAULT '',
            vytvorene TEXT NOT NULL DEFAULT '',
            zaznam_id INTEGER                 -- záznam požičania so zostatkom, ktorý zapísal člen
        )""")
    if stlpce and "od" not in stlpce:
        db.execute("INSERT INTO hodinky_rezervacia (hodinky_id, od, do, cislo, meno, vytvorene) "
                   "SELECT hodinky_id, datum || ' 00:00', datum || ' 23:59', cislo, meno, vytvorene "
                   "FROM hodinky_rezervacia_stara")
        db.execute("DROP TABLE hodinky_rezervacia_stara")
    db.commit()
    # prvé spustenie: ZO Banská Bystrica má benefit hodinky na plaváreň
    if db.execute("SELECT 1 FROM benefit LIMIT 1").fetchone() is None:
        db.execute("INSERT INTO benefit (zo_slug, nazov, popis, typ, poradie) VALUES (?, ?, ?, 'hodinky', 1)",
                   ("banska-bystrica", "PLAVÁREŇ - permanentky",
                    "Požičiavanie hodiniek (permanentiek) na plaváreň pre členov ZO."))
        db.commit()
    # stav na začiatku mesiaca nižší ako predtým = nezistený úbytok
    for z in db.execute("SELECT * FROM hodinky_zaznam WHERE typ = 'dobitie' AND zapisal = 'import'").fetchall():
        pred = db.execute("SELECT zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND datum < ? AND zostatok IS NOT NULL "
                          "ORDER BY datum DESC, id DESC LIMIT 1", (z["hodinky_id"], z["datum"])).fetchone()
        if pred is not None and z["zostatok"] is not None and z["zostatok"] < pred["zostatok"]:
            db.execute("UPDATE hodinky_zaznam SET typ = 'rozdiel', meno = ? WHERE id = ?", (NEZISTENE, z["id"]))
    # ZO BB, hodinky 4: tabuľka v auguste 2026 začína stavom 17 (v júni skončili na 35) – doplní sa raz
    h4 = db.execute("SELECT id FROM hodinky WHERE zo_slug = 'banska-bystrica' AND nazov = '4'").fetchone()
    if h4 and not db.execute("SELECT 1 FROM hodinky_zaznam WHERE hodinky_id = ? AND datum = '2026-08-01' "
                             "AND typ IN ('rozdiel', 'dobitie')", (h4["id"],)).fetchone():
        pred = db.execute("SELECT zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND datum < '2026-08-01' "
                          "AND zostatok IS NOT NULL ORDER BY datum DESC, id DESC LIMIT 1", (h4["id"],)).fetchone()
        if pred is not None and pred["zostatok"] == 35:
            db.execute("INSERT INTO hodinky_zaznam (hodinky_id, datum, typ, meno, zostatok, zapisal, cas) "
                       "VALUES (?, '2026-08-01', 'rozdiel', ?, 17, 'import', ?)",
                       (h4["id"], NEZISTENE, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
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
        if z["typ"] in ("pozicanie", "rozdiel") and pred is not None:
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
    clenovia, _ = clenovia_zo(slug)
    minul = {}
    for h in stlpce:
        minul.update(minute_body(db, h))
    rez = {}
    for r in db.execute("SELECT r.* FROM hodinky_rezervacia r JOIN hodinky h ON h.id = r.hodinky_id "
                        "WHERE h.zo_slug = ? AND r.zaznam_id IS NULL AND r.od <= ? AND r.do >= ?",
                        (slug, do + " 23:59", od + " 00:00")):
        for d in _dni_intervalu(r["od"], r["do"]):
            rez.setdefault((d, r["hodinky_id"]), []).append(r)
    return render_template(
        "hodinky/prehlad.html", minul=minul, rez=rez, rezervacie=rezervacie_zo(db, slug), zo=zo, slug=slug, stav=stav, stlpce=stlpce, dni=dni, zaznamy=zaznamy,
        rok=rok, mesiac=mesiac, nazov_mesiaca=MESIACE[mesiac - 1], predch="%04d-%02d" % _posun(rok, mesiac, -1),
        dalsi="%04d-%02d" % _posun(rok, mesiac, 1), dnes=date.today().isoformat(), clenovia=clenovia,
        aktivne=[h for h in vsetky if h["aktivne"]])


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


@bp.route("/<slug>/hodinky/statistika")
@vyzaduj()
def statistika(slug):
    """Členovia, ktorí mali požičané hodinky, a súčet minutých bodov za zvolené obdobie."""
    db = get_db()
    zo = _nacitaj_zo(slug)[0]
    a = request.args
    dnes = date.today()
    obdobie = a.get("obdobie", "rok")
    if obdobie == "mesiac":
        r, m = _mesiac(a.get("mesiac"))
        od, do = f"{r:04d}-{m:02d}-01", f"{r:04d}-{m:02d}-{calendar.monthrange(r, m)[1]:02d}"
        popis = f"{MESIACE[m - 1]} {r}"
    elif obdobie == "vlastne":
        od = _datum(a.get("od")) or f"{dnes.year}-01-01"
        do = _datum(a.get("do")) or dnes.isoformat()
        if od > do:
            od, do = do, od
        popis = f"{datetime.strptime(od, '%Y-%m-%d'):%d.%m.%Y} – {datetime.strptime(do, '%Y-%m-%d'):%d.%m.%Y}"
    else:
        obdobie = "rok"
        r = int(a.get("rok")) if (a.get("rok") or "").isdigit() else dnes.year
        od, do = f"{r}-01-01", f"{r}-12-31"
        popis = f"rok {r}"
    minul = {}
    for h in hodinky_zo(db, slug):
        minul.update(minute_body(db, h))
    clenovia, index = clenovia_zo(slug)
    platne = {c[1]: c[2] for c in clenovia if c[1]}
    ludia = {}
    for z in db.execute("SELECT z.* FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id WHERE h.zo_slug = ? "
                        "AND z.typ IN ('pozicanie', 'rozdiel') AND z.datum BETWEEN ? AND ? ORDER BY z.datum, z.id",
                        (slug, od, do)):
        if z["typ"] == "rozdiel":
            p = ludia.setdefault("", {"meno": NEZISTENE, "cislo": "", "clen": False, "nezistene": True,
                                      "body": 0, "pozicani": 0, "dni": set(), "bez_zostatku": 0})
            p["body"] += minul.get(z["id"]) or 0
            continue
        cislo = z["cislo"] if z["cislo"] in platne else index.get(_slova(z["meno"]), "")
        k = cislo or "?" + " ".join(sorted(_slova(z["meno"])))
        p = ludia.setdefault(k, {"meno": platne.get(cislo) or z["meno"], "cislo": cislo, "clen": bool(cislo),
                                 "body": 0, "pozicani": 0, "dni": set(), "bez_zostatku": 0})
        p["pozicani"] += 1
        p["dni"].add(z["datum"])
        if z["id"] in minul:
            p["body"] += minul[z["id"]]
        else:
            p["bez_zostatku"] += 1
    zoznam = sorted(ludia.values(), key=lambda p: (p.get("nezistene", False), not p["clen"], -p["body"],
                                                    abecedne(p["meno"])))
    for p in zoznam:
        p["dni"] = len(p["dni"])
    roky = sorted({int(r[0][:4]) for r in db.execute(
        "SELECT z.datum FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id WHERE h.zo_slug = ?", (slug,))}
        | {dnes.year}, reverse=True)
    return render_template("hodinky/statistika.html", zo=zo, slug=slug, zoznam=zoznam, obdobie=obdobie, popis=popis,
                           od=od, do=do, roky=roky, rok=a.get("rok") or str(dnes.year),
                           mesiac=a.get("mesiac") or dnes.strftime("%Y-%m"),
                           spolu=sum(p["body"] for p in zoznam), osob=sum(1 for p in zoznam if not p.get("nezistene")), pozicani=sum(p["pozicani"] for p in zoznam))


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
    db.execute("UPDATE hodinky_rezervacia SET zaznam_id = NULL WHERE zaznam_id = ?", (zaznam_id,))
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
    poradie, pociatok, zaznamy, posledny_harok, stavy = [], {}, [], set(), []
    for ws in wb.worksheets:
        riadky = list(ws.iter_rows(values_only=True))
        if len(riadky) < 3 or str(riadky[0][0] or "").strip().lower() != "hodinky":
            continue
        hlavicka = riadky[0]
        stlpce = []  # (index stĺpca s menom, názov hodiniek)
        posledny_harok = set()
        prvy_den = next(((d.date() if isinstance(d, datetime) else d).isoformat() for d in (r[0] for r in riadky[2:])
                         if isinstance(d, (datetime, date))), None)
        for i in range(1, len(hlavicka), 2):
            if hlavicka[i] is not None and str(hlavicka[i]).strip() != "":
                nazov = str(hlavicka[i]).strip()
                if isinstance(hlavicka[i], float) and hlavicka[i].is_integer():
                    nazov = str(int(hlavicka[i]))
                stlpce.append((i, nazov))
                posledny_harok.add(nazov)
                z = riadky[1][i + 1] if len(riadky[1]) > i + 1 else None
                z = int(z) if isinstance(z, (int, float)) else None
                if nazov not in pociatok:
                    poradie.append(nazov)
                    pociatok[nazov] = z
                elif z is not None and prvy_den:
                    stavy.append((nazov, prvy_den, z))
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
    return [(n, pociatok[n], n in posledny_harok) for n in poradie], zaznamy, stavy


@bp.route("/<slug>/hodinky/import", methods=["POST"])
@vyzaduj(menit=True)
def importuj(slug):
    subor = request.files.get("subor")
    if not subor or not subor.filename.lower().endswith(".xlsx"):
        flash("Vyberte súbor .xlsx.")
        return _spat(slug)
    try:
        nove_hodinky, zaznamy, stavy = precitaj_excel(subor.read())
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
    # stav na začiatku mesiaca (2. riadok hárku), ak sa líši od posledného zapísaného zostatku
    for nazov, datum, z in stavy:
        pred = db.execute("SELECT zostatok FROM hodinky_zaznam WHERE hodinky_id = ? AND datum < ? AND zostatok IS NOT NULL "
                          "ORDER BY datum DESC, id DESC LIMIT 1", (ids[nazov], datum)).fetchone()
        if pred is not None and pred["zostatok"] != z:
            rozdiel = z < pred["zostatok"]
            db.execute("INSERT INTO hodinky_zaznam (hodinky_id, datum, typ, meno, zostatok, zapisal, cas) "
                       "VALUES (?, ?, ?, ?, ?, 'import', ?)",
                       (ids[nazov], datum, "rozdiel" if rozdiel else "dobitie",
                        NEZISTENE if rozdiel else "Stav na začiatku mesiaca (z tabuľky)", z, cas))
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


# --- rezervácia hodiniek členom ------------------------------------------------


def _cas(text):
    """„YYYY-MM-DDTHH:MM“ z formulára -> „YYYY-MM-DD HH:MM“ alebo None."""
    try:
        return datetime.strptime((text or "").replace(" ", "T")[:16], "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _dni_intervalu(od, do):
    d1, d2 = date.fromisoformat(od[:10]), date.fromisoformat(do[:10])
    return [date.fromordinal(o).isoformat() for o in range(d1.toordinal(), d2.toordinal() + 1)]


def _kolizie(db, hodinky_id, od, do, okrem=None):
    """Prekrýva sa interval s inou rezerváciou alebo s požičaním zapísaným predsedom (celý deň)?"""
    if db.execute("SELECT 1 FROM hodinky_rezervacia WHERE hodinky_id = ? AND od < ? AND do > ? AND id IS NOT ?",
                  (hodinky_id, do, od, okrem)).fetchone():
        return True
    return db.execute(
        "SELECT 1 FROM hodinky_zaznam z WHERE z.hodinky_id = ? AND z.typ = 'pozicanie' AND z.datum BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM hodinky_rezervacia r WHERE r.zaznam_id = z.id)",
        (hodinky_id, od[:10], do[:10])).fetchone() is not None


def _obsadenost(db, slug, od, do):
    """{(dátum, hodinky_id): [(text, moje_cislo)]} – obsadené časy v jednotlivých dňoch."""
    obs = {}
    for z in db.execute("SELECT z.* FROM hodinky_zaznam z JOIN hodinky h ON h.id = z.hodinky_id WHERE h.zo_slug = ? "
                        "AND z.typ = 'pozicanie' AND z.datum BETWEEN ? AND ? "
                        "AND NOT EXISTS (SELECT 1 FROM hodinky_rezervacia r WHERE r.zaznam_id = z.id)", (slug, od, do)):
        obs.setdefault((z["datum"], z["hodinky_id"]), []).append(("celý deň", ""))
    for r in db.execute("SELECT r.* FROM hodinky_rezervacia r JOIN hodinky h ON h.id = r.hodinky_id WHERE h.zo_slug = ? "
                        "AND r.od <= ? AND r.do >= ? ORDER BY r.od", (slug, do + " 23:59", od + " 00:00")):
        for d in _dni_intervalu(r["od"], r["do"]):
            zac = r["od"][11:] if r["od"][:10] == d else "00:00"
            kon = r["do"][11:] if r["do"][:10] == d else "24:00"
            obs.setdefault((d, r["hodinky_id"]), []).append((f"{zac}–{kon}", r["cislo"]))
    return obs


def rezervacie_zo(db, slug):
    """Aktuálne a budúce rezervácie a tie, pri ktorých ešte chýba zostatok."""
    teraz = datetime.now().strftime("%Y-%m-%d %H:%M")
    return db.execute("SELECT r.*, h.nazov, z.zostatok FROM hodinky_rezervacia r JOIN hodinky h ON h.id = r.hodinky_id "
                      "LEFT JOIN hodinky_zaznam z ON z.id = r.zaznam_id "
                      "WHERE h.zo_slug = ? AND (r.do >= ? OR r.zaznam_id IS NULL) AND r.od >= ? "
                      "ORDER BY r.od, h.poradie", (slug, teraz, _pred_dnami(30))).fetchall()


def _pred_dnami(n):
    return date.fromordinal(date.today().toordinal() - n).isoformat()


def _clen_zo(slug):
    """Prihlásený člen, ktorý patrí do ZO s benefitom hodiniek; inak presmerovanie / 403."""
    from anketa import zo_clena

    clen = g.get("clen")
    if clen is None:
        return None
    db = get_db()
    if slug not in zo_clena(db, clen["cislo"]) and clen["zo_slug"] != slug:
        abort(403)
    if not db.execute("SELECT 1 FROM benefit WHERE zo_slug = ? AND typ = 'hodinky'", (slug,)).fetchone():
        abort(404)
    return clen


def _fmt(cas):
    return datetime.strptime(cas, "%Y-%m-%d %H:%M").strftime("%d.%m.%Y %H:%M")


@bp_clen.route("/<slug>/hodinky", methods=["GET", "POST"])
def rezervacia(slug):
    clen = _clen_zo(slug)
    if clen is None:
        return redirect(url_for("ucet.prihlasenie", dalej=request.path))
    db = get_db()
    zo = _nacitaj_zo(slug)[0]
    cislo = re.sub(r"\s+", "", clen["cislo"])
    meno = clen["meno"] or cislo
    dnes = date.today()
    teraz = datetime.now().strftime("%Y-%m-%d %H:%M")
    hranica = date.fromordinal(dnes.toordinal() + DNI_REZERVACIE).isoformat() + " 23:59"
    hodinky = hodinky_zo(db, slug, aj_neaktivne=False)
    spat = redirect(url_for("hodinky_clen.rezervacia", slug=slug))
    if request.method == "POST":
        f = request.form
        akcia = f.get("akcia")
        if akcia in ("zrusit", "zostatok"):
            r = db.execute("SELECT r.*, h.nazov FROM hodinky_rezervacia r JOIN hodinky h ON h.id = r.hodinky_id "
                           "WHERE r.id = ? AND r.cislo = ? AND h.zo_slug = ?",
                           (int(f.get("rezervacia_id", 0) or 0), cislo, slug)).fetchone()
            if r is None:
                abort(404)
            if akcia == "zrusit":
                if r["od"] <= teraz:
                    flash("Rezervácia už začala, nedá sa zrušiť.")
                else:
                    db.execute("DELETE FROM hodinky_rezervacia WHERE id = ?", (r["id"],))
                    db.commit()
                    flash("Rezervácia bola zrušená.")
                return spat
            zostatok = f.get("zostatok", "").strip()
            if r["od"] > teraz:
                flash("Zostatok sa dá zapísať až po začiatku rezervácie.")
            elif not re.fullmatch(r"\d+", zostatok):
                flash("Zostatok zadajte ako celé číslo.")
            else:
                cas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if r["zaznam_id"] and db.execute("SELECT 1 FROM hodinky_zaznam WHERE id = ?", (r["zaznam_id"],)).fetchone():
                    db.execute("UPDATE hodinky_zaznam SET zostatok = ?, zapisal = ?, cas = ? WHERE id = ?",
                               (int(zostatok), meno, cas, r["zaznam_id"]))
                else:
                    zid = db.execute(
                        "INSERT INTO hodinky_zaznam (hodinky_id, datum, typ, cislo, meno, zostatok, zapisal, cas) "
                        "VALUES (?, ?, 'pozicanie', ?, ?, ?, ?, ?)",
                        (r["hodinky_id"], r["od"][:10], cislo, meno, int(zostatok), meno, cas)).lastrowid
                    db.execute("UPDATE hodinky_rezervacia SET zaznam_id = ? WHERE id = ?", (zid, r["id"]))
                db.commit()
                flash(f"Zapísaný zostatok {zostatok} pri hodinkách {r['nazov']}. Ďakujeme.")
            return spat
        od, do = _cas(f.get("od")), _cas(f.get("do"))
        vybrane = [h for h in hodinky if f.get(f"hodinky_{h['id']}")]
        if not od or not do:
            flash("Zadajte dátum a čas od – do.")
        elif do <= od:
            flash("Čas „do“ musí byť neskôr ako „od“.")
        elif (datetime.strptime(do, "%Y-%m-%d %H:%M") - datetime.strptime(od, "%Y-%m-%d %H:%M")).total_seconds() \
                > MAX_HODIN * 3600:
            flash("Rezervácia môže byť najviac na 2 dni (48 hodín).")
        elif od < dnes.isoformat() + " 00:00" or od > hranica:
            flash(f"Začiatok rezervácie musí byť od dnes do {DNI_REZERVACIE} dní.")
        elif not vybrane:
            flash("Označte aspoň jedny hodinky.")
        elif any(_kolizie(db, h["id"], od, do) for h in vybrane):
            obs = [h["nazov"] for h in vybrane if _kolizie(db, h["id"], od, do)]
            flash("V tomto čase sú obsadené hodinky " + ", ".join(obs) + ". Vyberte iný čas alebo iné hodinky.")
        else:
            cas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for h in vybrane:
                db.execute("INSERT INTO hodinky_rezervacia (hodinky_id, od, do, cislo, meno, vytvorene) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (h["id"], od, do, cislo, meno, cas))
            db.commit()
            flash(f"Rezervované {_fmt(od)} – {_fmt(do)}: hodinky " + ", ".join(h["nazov"] for h in vybrane) + ".")
            return spat
        return redirect(url_for("hodinky_clen.rezervacia", slug=slug, od=f.get("od") or None, do=f.get("do") or None))
    obs = _obsadenost(db, slug, dnes.isoformat(), hranica[:10])
    dni = []
    for i in range(DNI_REZERVACIE + 1):
        d = date.fromordinal(dnes.toordinal() + i)
        iso = d.isoformat()
        stav = []
        for h in hodinky:
            o = obs.get((iso, h["id"]), [])
            stav.append({"h": h, "casy": [t for t, _ in o], "moje": bool(o) and all(c == cislo for _, c in o),
                         "cely": any(t in ("celý deň", "00:00–24:00") for t, _ in o)})
        dni.append({"iso": iso, "d": d, "den": DNI_TYZDNA[d.weekday()], "vikend": d.weekday() >= 5, "stav": stav})
    moje = db.execute("SELECT r.*, h.nazov, z.zostatok FROM hodinky_rezervacia r JOIN hodinky h ON h.id = r.hodinky_id "
                      "LEFT JOIN hodinky_zaznam z ON z.id = r.zaznam_id "
                      "WHERE r.cislo = ? AND h.zo_slug = ? AND (r.do >= ? OR r.od >= ?) ORDER BY r.od, h.poradie",
                      (cislo, slug, teraz, _pred_dnami(30))).fetchall()
    stav_hodiniek = {h["id"]: aktualny_stav(db, h)[0] for h in hodinky}
    return render_template("hodinky/rezervacia.html", zo=zo, slug=slug, dni=dni, hodinky=hodinky, moje=moje,
                           teraz=teraz, stav_hodiniek=stav_hodiniek, max_od=hranica[:10] + "T23:59",
                           min_od=dnes.isoformat() + "T00:00", od=request.args.get("od", ""),
                           do=request.args.get("do", ""))


@bp.route("/<slug>/hodinky/rezervacia/<int:rezervacia_id>/zrusit", methods=["POST"])
@vyzaduj(menit=True)
def zrus_rezervaciu(slug, rezervacia_id):
    db = get_db()
    r = db.execute("SELECT r.id FROM hodinky_rezervacia r JOIN hodinky h ON h.id = r.hodinky_id "
                   "WHERE r.id = ? AND h.zo_slug = ?", (rezervacia_id, slug)).fetchone()
    if r is None:
        abort(404)
    db.execute("DELETE FROM hodinky_rezervacia WHERE id = ?", (rezervacia_id,))
    db.commit()
    flash("Rezervácia bola zrušená.")
    return _spat(slug)
