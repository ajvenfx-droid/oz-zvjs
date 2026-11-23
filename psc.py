"""PSČ podľa adresy zo zoznamu Slovenskej pošty (OBCE.xlsx, ULICE.xlsx zo ZIP súboru na posta.sk/psc).

- psc_data.json v repozitári je zoznam pošty, z ktorého sa vychádza.
- Správca môže v Správe stránky nahrať novší ZIP od pošty; uloží sa ako psc_nahrate.json vedľa databázy
  a má prednosť (nejde na GitHub).
- Formulár člena sa pýta /psc/hladat?mesto=…&ulica=… a /psc/mesto?psc=…
"""
import io
import json
import os
import re
import unicodedata
import zipfile

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

ADRESAR = os.path.dirname(os.path.abspath(__file__))
ZAKLAD = os.path.join(ADRESAR, "psc_data.json")


def _nahrate():
    db = os.environ.get("OZ_DB_PATH", os.path.join(ADRESAR, "oz_zvjs.db"))
    return os.path.join(os.path.dirname(os.path.abspath(db)), "psc_nahrate.json")


_cache = {"cesta": None, "cas": None, "data": None}


def data():
    cesta = _nahrate() if os.path.exists(_nahrate()) else ZAKLAD
    if not os.path.exists(cesta):
        return None
    cas = os.path.getmtime(cesta)
    if _cache["cesta"] != cesta or _cache["cas"] != cas:
        with open(cesta, encoding="utf-8") as f:
            _cache.update(cesta=cesta, cas=cas, data=json.load(f))
    return _cache["data"]


def kluc(text):
    t = "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if not unicodedata.combining(c))
    t = re.sub(r"\b(ulica|ul)\b\.?", " ", t)
    return re.sub(r"[^a-z0-9]", "", t)


def kluc_obce(text):
    return kluc(text)


# --- zostavenie z xlsx -----------------------------------------------------------------------------

def zostav(zip_bytes):
    """Z ZIP súboru pošty (OBCE.xlsx, ULICE.xlsx) vytvorí slovník pre data()."""
    from openpyxl import load_workbook
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    mena = {os.path.basename(n).upper(): n for n in z.namelist()}
    if "OBCE.XLSX" not in mena or "ULICE.XLSX" not in mena:
        raise ValueError("V ZIP súbore chýba OBCE.xlsx alebo ULICE.xlsx.")

    def riadky(nazov):
        ws = load_workbook(io.BytesIO(z.read(mena[nazov])), read_only=True).active
        it = ws.iter_rows(values_only=True)
        hlavicka = [str(h or "").strip().upper() for h in next(it)]
        for r in it:
            yield {h: (str(v).strip() if v is not None else "") for h, v in zip(hlavicka, r) if h}

    obce, z_psc, ulice, posty = {}, {}, {}, {}
    nazvy_obci, nazvy_ulic, viac = set(), {}, {}
    for r in riadky("OBCE.XLSX"):
        nazov, psc = r.get("DOBEC") or r.get("OBEC"), r.get("PSC")
        if not nazov:
            continue
        nazvy_obci.add(nazov)
        posta = re.sub(r"\s*\d+$", "", r.get("DPOSTA") or r.get("POSTA") or "")
        if psc and posta == nazov:  # obec, v ktorej je pošta s týmto PSČ
            posty.setdefault(psc.replace(" ", ""), nazov)
        if psc:
            obce.setdefault(kluc_obce(nazov), [psc, nazov])
            if [psc, r.get("OKRES", "")] not in viac.setdefault(kluc_obce(nazov), []):
                viac[kluc_obce(nazov)].append([psc, r.get("OKRES", "")])
            z_psc.setdefault(psc.replace(" ", ""), [])
            if nazov not in z_psc[psc.replace(" ", "")]:
                z_psc[psc.replace(" ", "")].append(nazov)
    for r in riadky("ULICE.XLSX"):
        ulica, psc, obec = r.get("DULICA") or r.get("ULICA"), r.get("PSC"), r.get("OBCE")
        posta = re.sub(r"\s*\d+$", "", r.get("DPOSTA") or r.get("POSTA") or "")
        if psc and obec and posta == obec:
            posty.setdefault(psc.replace(" ", ""), obec)
        if ulica and psc and obec:
            nazvy_obci.add(obec)
            nazvy_ulic.setdefault(kluc_obce(obec), set()).add(ulica)
            ulice.setdefault(kluc_obce(obec), {}).setdefault(kluc(ulica), []).append([psc, r.get("POZNAMKA", "")])
            z_psc.setdefault(psc.replace(" ", ""), [])
            if obec not in z_psc[psc.replace(" ", "")]:
                z_psc[psc.replace(" ", "")].append(obec)
    if not obce or not ulice:
        raise ValueError("Súbory OBCE.xlsx a ULICE.xlsx nemajú očakávaný obsah.")
    return {"obce": obce, "z_psc": z_psc, "ulice": ulice, "posty": posty,
            "obce_viac": {k: v for k, v in viac.items() if len({p for p, _ in v}) > 1},
            "nazvy_obci": sorted(nazvy_obci), "nazvy_ulic": {k: sorted(v) for k, v in nazvy_ulic.items()}}


# --- vyhľadávanie ----------------------------------------------------------------------------------

def _sedi_cislo(poznamka, cisla):
    """Platí poznámka pošty (napr. „1-19,2A“, „párne 86-116“, „1/3865“) pre niektoré z čísel domu?"""
    for cast in re.split(r"[,;]", poznamka):
        c = cast.lower()
        parita = 1 if "nepár" in c or "nepar" in c else 0 if "pár" in c or "par" in c else None
        rozsah = re.search(r"(\d+)\s*-\s*(\d+)", c)
        if rozsah:
            od, do = int(rozsah.group(1)), int(rozsah.group(2))
            if any(od <= n <= do and (parita is None or n % 2 == parita) for n in cisla):
                return True
            continue
        jedno = re.search(r"(\d+)", c)
        if jedno and int(jedno.group(1)) in cisla:
            return True
    return False


def rozdel_ulicu(ulica):
    """„Rudlovská cesta 60“ → („Rudlovská cesta“, {60}); „29. augusta 5“ → („29. augusta“, {5}); „SNP 149/62“ → {149, 62}."""
    ulica = (ulica or "").strip()
    m = re.match(r"^(.*?\D)[\s,.]*((?:\d+\s*[A-Za-z]?\s*[/,-]?\s*)+)$", ulica)
    if m and re.search(r"[^\W\d_]", m.group(1)):
        return m.group(1).strip(" ,"), {int(x) for x in re.findall(r"\d+", m.group(2))}
    return ulica, set()


def najdi_psc(mesto, ulica):
    d = data()
    if not d or not mesto:
        return ""
    km = kluc_obce(mesto)
    kandidati = [km]
    if "-" in mesto:  # napr. „Bratislava-Petržalka“ → ulice sú pod „Bratislava“
        kandidati.append(kluc_obce(mesto.split("-")[0]))
    nazov_ulice, cisla = rozdel_ulicu(ulica)
    for k in kandidati:
        zaznamy = d["ulice"].get(k, {}).get(kluc(nazov_ulice)) if nazov_ulice else None
        if zaznamy:
            if len({p for p, _ in zaznamy}) == 1:
                return zaznamy[0][0]
            if cisla:
                for p, pozn in zaznamy:
                    if pozn and _sedi_cislo(pozn, cisla):
                        return p
            bez = [p for p, pozn in zaznamy if not pozn]
            return bez[0] if bez else zaznamy[0][0]
    for k in kandidati:
        if k in d.get("obce_viac", {}):
            return ""  # viac obcí s rovnakým názvom (napr. Hrušov) – PSČ vyberie človek z ponuky
        if k in d["obce"]:
            return d["obce"][k][0]
    return ""


def moznosti_psc(mesto):
    """Pre obce s rovnakým názvom v rôznych okresoch: [[PSČ, okres], …]."""
    return (data() or {}).get("obce_viac", {}).get(kluc_obce(mesto or ""), [])


def mesto_z_psc(psc):
    d = data() or {}
    p = re.sub(r"\s", "", psc or "")
    mesta = d.get("z_psc", {}).get(p, [])
    return mesta[0] if len(mesta) == 1 else d.get("posty", {}).get(p, "")


def ma_ulice(mesto):
    """Mesto, kde PSČ závisí od ulice (napr. Banská Bystrica) – bez ulice sa PSČ nehádá."""
    d = data()
    return bool(d and mesto and kluc_obce(mesto) in d["ulice"] and kluc_obce(mesto) not in d["obce"])


def _zacina(nazvy, q, pocet=15):
    k = kluc(q)
    if not k:
        return []
    zaciatok = [n for n in nazvy if kluc(n).startswith(k)]
    vo_vnutri = [n for n in nazvy if k in kluc(n) and n not in zaciatok] if len(k) >= 3 else []
    return (zaciatok + vo_vnutri)[:pocet]


bp = Blueprint("psc", __name__)


@bp.route("/psc/obce")
def obce():
    return jsonify(nazvy=_zacina((data() or {}).get("nazvy_obci", []), request.args.get("q", "")))


@bp.route("/psc/ulice")
def ulice():
    d, mesto = data() or {}, request.args.get("mesto", "")
    nazvy = d.get("nazvy_ulic", {}).get(kluc_obce(mesto)) or d.get("nazvy_ulic", {}).get(kluc_obce(mesto.split("-")[0]), [])
    return jsonify(nazvy=_zacina(nazvy, request.args.get("q", "")))


@bp.route("/psc/hladat")
def hladat():
    return jsonify(psc=najdi_psc(request.args.get("mesto", ""), request.args.get("ulica", "")),
                   moznosti=moznosti_psc(request.args.get("mesto", "")))


@bp.route("/psc/mesto")
def mesto():
    return jsonify(mesto=mesto_z_psc(request.args.get("psc", "")))


@bp.route("/spravca/psc", methods=["GET", "POST"])
def spravca():
    from anketa import je_spravca
    if not je_spravca():
        return redirect(url_for("anketa.prihlasenie", dalej=request.path))
    if request.method == "POST":
        subor = request.files.get("subor")
        try:
            nove = zostav(subor.read() if subor else b"")
        except (ValueError, zipfile.BadZipFile, KeyError) as e:
            flash(f"Súbor sa nepodarilo načítať: {e}")
            return redirect(url_for("psc.spravca"))
        nove["nazov"] = subor.filename
        with open(_nahrate() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(nove, f, ensure_ascii=False)
        os.replace(_nahrate() + ".tmp", _nahrate())
        flash(f"Zoznam PSČ bol nahratý: {len(nove['obce'])} obcí, {sum(len(u) for u in nove['ulice'].values())} ulíc.")
        return redirect(url_for("psc.spravca"))
    d = data() or {}
    return render_template("psc.html", nazov=d.get("nazov", ""), obci=len(d.get("obce", {})),
                           ulic=sum(len(u) for u in d.get("ulice", {}).values()), nahrate=os.path.exists(_nahrate()))
