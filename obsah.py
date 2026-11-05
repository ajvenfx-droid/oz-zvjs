"""Obsah stránky: textové stránky z menu, Aktuality, Tlačové správy a Dokumenty.

Všetko sa upravuje v správe stránky bez programovania. Súbory dokumentov sa
ukladajú priamo v databáze, takže záloha oz_zvjs.db obsahuje všetko.
"""
import re
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from markupsafe import Markup, escape

from anketa import CASOVA_ZONA, len_spravca, nastav, nastavenie
from db import get_db

bp = Blueprint("obsah", __name__)

MAX_VELKOST_SUBORU = 15 * 1024 * 1024  # 15 MB

# Textové stránky z menu ozzvjs.sk (slug, názov, len pre prihlásených členov).
STRANKY = [
    ("uvod", "Úvod (text na hlavnej stránke)", False),
    ("o-nas", "O nás", False),
    ("venujte-2-percenta", "Venujte 2 % z daní", False),
    ("bozp", "Výkon kontroly BOZP", False),
    ("benefity", "Benefity", False),
    ("podporny-fond", "Podporný fond", False),
    ("kontakty", "Kontakty", False),
    ("predsednictvo", "Predsedníctvo", False),
    ("rada-predsedov", "Rada predsedov", False),
    ("revizna-komisia", "Revízna komisia", False),
    ("pre-clenov", "Pre členov", True),
]

UVODNY_TEXT = (
    "Odborový zväz Zboru väzenskej a justičnej stráže je dobrovoľným združením základných "
    "organizácií odborového zväzu, ktoré sa združili za účelom spoločnej obhajoby práv, "
    "sociálnych istôt a oprávnených záujmov, ktoré vznikajú alebo súvisia s výkonom štátnej "
    "služby príslušníkov Zboru väzenskej a justičnej stráže a výkonom prác vo verejnom záujme "
    "zamestnancov Zboru väzenskej a justičnej stráže."
)

O_NAS_TEXT = (
    UVODNY_TEXT
    + "\n\nZ dôvodu neustáleho zlepšovania informovanosti našich členov sa na tomto webe dozviete "
    "to najdôležitejšie z našej činnosti. Informácie na tejto stránke budú podľa potreby "
    "aktualizované v časti Aktuality a budú vždy transparentne zverejňované."
    "\n\nV prípade akýchkoľvek otázok nás neváhajte kontaktovať (webmaster) na e-mailovej adrese: "
    "ozzvjs@gmail.com"
    "\n\nWeb I. generácie (starý web OZ ZVJS): http://ozzvjs.blogspot.sk/"
    "\n\nWeb II. generácie (starý web OZ ZVJS): https://sites.google.com/site/ozzvjs/"
)

BOZP_TEXT = (
    "Uznesením Rady predsedov Odborového zväzu Zboru väzenskej a justičnej stráže zo dňa "
    "17. decembra 2020 č. OZ ZVJS 1-52/2020 bol ustanovený do funkcie Odborového inšpektora "
    "Bezpečnosti a ochrany zdravia pri práci pre Odborový zväz Zboru väzenskej a justičnej stráže "
    "PaedDr. Ján KŇAŽKO."
    "\n\n## Správy o odborovej kontrole inšpektora BOZP OZ ZVJS v ZVJS za roky"
    "\nSprávy nájdete v časti Dokumenty."
    "\n\n## Interné predpisy – plány"
    "\n- Plán kontrol inšpektora BOZP OZ ZVJS"
    "\n- Štatút práce inšpektora BOZP OZ ZVJS 12/2020"
    "\n- Metodika odborovej kontroly nad BOZP v ZVJS – aktualizovaná 10.2018"
    "\n\n## Zákony, vyhlášky, nariadenia vlády SR v oblasti BOZP"
    "\n- Zákon č. 124/2006 Z. z. o bezpečnosti a ochrane zdravia pri práci"
    "\n- Zákon č. 125/2006 Z. z. o inšpekcii práce"
    "\n- Vyhláška č. 99/2016 Z. z. o podrobnostiach o ochrane zdravia pred záťažou teplom a chladom pri práci"
    "\n- Nariadenie vlády č. 395/2006 Z. z. o minimálnych požiadavkách na poskytovanie a používanie "
    "osobných ochranných pracovných prostriedkov"
    "\n- Nariadenie vlády č. 276/2006 Z. z. o minimálnych bezpečnostných a zdravotných požiadavkách "
    "pri práci so zobrazovacími jednotkami"
    "\n\n## Kontakt inšpektor BOZP za OZ ZVJS"
    "\nPaedDr. Ján Kňažko"
    "\nemail: Knazko09@gmail.com"
    "\ntel: +421 910 959 947"
)

PLAN_TEXT = (
    "## Rok 2025"
    "\n- 27. februára 2025 (štvrtok) Predsedníctvo OZ ZVJS – HnP"
    "\n- 20. marca 2025 (štvrtok) Rada predsedov a Revíznej komisie OZ ZVJS – BA"
    "\n- 24. apríla 2025 (štvrtok) Predsedníctvo OZ ZVJS – BA"
    "\n- 28. – 29. mája 2025 (streda – štvrtok) Rada predsedov a Revíznej komisie OZ ZVJS – Santovka"
)

PODPORNY_FOND_TEXT = (
    "Podporný fond Odborového zväzu ZVJS slúži na poskytovanie príspevkov v zmysle schváleného štatútu "
    "(len členom združeným vo zväze) s cieľom poskytnúť konkrétnu výhodu členom zväzu oproti nečlenom."
    "\n\n- 05.06. – 06.06.2019 Rada predsedov a Revíznej komisie Odborového zväzu ZVJS v Santovke schválila "
    "použitie prostriedkov podporného fondu od 07.06.2019 podľa Čl. 4 písm. e) na základe žiadosti "
    "„príspevok na detskú rekreáciu v zariadeniach zboru“ pre člena základnej organizácie"
    "\n- 05.06. – 06.06.2019 Rada predsedov a Revíznej komisie Odborového zväzu ZVJS v Santovke schválila "
    "zmenu u Štatútu podporného fondu s účinnosťou od 07.06.2019"
    "\n- 6.12.2018 Rada predsedov Odborového zväzu ZVJS hlasovaním schválila použitie prostriedkov "
    "z podporného fondu od 1.1.2019 podľa Čl. 4 písm. f) na základe žiadosti „príspevok pri narodení "
    "dieťaťa“ člena základnej organizácie"
    "\n- 19.04.2018 Rada predsedov Odborového zväzu ZVJS hlasovaním schválila použitie prostriedkov "
    "z podporného fondu od 20.4.2018 podľa Čl. 4 písm. b) „príspevok na pohrebné trovy v prípade úmrtia "
    "člena základnej organizácie“"
    "\n- V roku 2017 Rada predsedov Odborového zväzu ZVJS hlasovaním schválila použitie prostriedkov "
    "z podporného fondu len podľa Čl. 4 písm. a), c) na základe žiadosti o poskytnutí podpory na "
    "prekonanie ťažkej finančnej situácie člena základnej organizácie"
    "\n\n## Použitie financií z podporného fondu"
    "\n- V roku 2020 bolo spolu vyplatených členom zväzu 6626,- EUR: podľa Čl. 4 písm. a), c) 6 členom "
    "v celkovej výške 1826,- EUR; podľa Čl. 4 písm. f) 32 členom (v dvoch prípadoch za dvojičky) "
    "v celkovej výške 3400,- EUR; podľa Čl. 4 písm. e) 27 členom (v 8 prípadoch za dve deti v detskom "
    "tábore) v celkovej výške 1400,- EUR"
    "\n\nŠtatút podporného fondu a žiadosti nájdete nižšie."
)

BENEFITY_TEXT = (
    "Zľavy a výhody pre členov Odborového zväzu ZVJS."
    "\n\n## Autobenefity\nPripravujeme."
    "\n\n## Banky a poisťovne\nPripravujeme."
    "\n\n## Online obchody\nPripravujeme."
    "\n\n## Šport a wellness\nPripravujeme."
)

# Stránky, pod ktorými sa zobrazia aj dokumenty z týchto skupín.
DOKUMENTY_NA_STRANKE = {"podporny-fond": ["Podporný fond – žiadosti"]}

PREDSEDNICTVO_TEXT = (
    "## Predsedníctvo Rady predsedov zvolené na konferencii OZ ZVJS 12.12.2023"
    "\n\n## Predseda Odborového zväzu ZVJS"
    "\nMgr. Ivan CABAN"
    "\nemail: Predseda_OZ@zvjs.sk ; ozzvjs@gmail.com"
    "\ntel: +421 905 659 219"
    "\nkorešpondenčná adresa: Ústav na výkon trestu odňatia slobody Banská Bystrica – Kráľová, "
    "Sládkovičova 80, 974 05 Banská Bystrica"
    "\n\n## 1. podpredseda Odborového zväzu ZVJS"
    "\nMgr. Martin ČURI"
    "\nemail: martin.curi@zvjs.sk"
    "\ntel: +421 908 734 466"
    "\n\n## 2. podpredseda Odborového zväzu ZVJS"
    "\nMilan URBAN"
    "\nemail: milan.urban@zvjs.sk"
    "\ntel: +421 905 818 902"
    "\n\n## Členovia Predsedníctva Odborového zväzu ZVJS"
    "\nRenáta HALÁNOVÁ – renata.halanova@zvjs.sk"
    "\nJUDr. Ľuboš GAŽO – lubos.gazo@zvjs.sk"
    "\nMgr. Ľubor HUPKA – lubor.hupka@zvjs.sk"
    "\nBožena MAREČKOVÁ – bozena.mareckova@zvjs.sk"
)

# Rubriky tlačových správ (záložky ako na ozzvjs.sk).
RUBRIKY_TLACOVYCH = [
    ("zvjs", "týkajúce sa ZVJS"),
    ("zbory", "týkajúce sa ozbrojených a bezpečnostných zborov"),
    ("odbory", "týkajúce sa odborovej činnosti"),
]

RADA_TEXT = (
    "## Radu predsedov Odborového zväzu ZVJS tvoria:"
    "\na) zástupcovia každej základnej organizácie, ktorými sú spravidla predsedovia základných organizácií"
    "\nb) členovia predsedníctva"
    "\n\nZákladné organizácie Odborového zväzu Zboru väzenskej a justičnej stráže pri:"
    "\n\n## Generálnom riaditeľstve Zboru väzenskej a justičnej stráže"
    "\npredsedníčka: Božena MAREČKOVÁ"
    "\nemail: bozena.mareckova@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody a Ústave na výkon väzby v Leopoldove"
    "\npredseda: Lukáš MALICH"
    "\nemail: lukas.malich@zvjs.sk"
    "\n\n## Ústave na výkon väzby a Ústave na výkon trestu odňatia slobody v Banskej Bystrici"
    "\npredseda: Mgr. Ivan CHROMEK"
    "\nemail: ivan.chromek@zvjs.sk"
    "\n\n## Ústave na výkon väzby a Ústave na výkon trestu odňatia slobody v Nitre"
    "\npredseda: Mgr. Ľubor HUPKA"
    "\nemail: lubor.hupka@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Banskej Bystrici – Kráľovej"
    "\npredseda: Mgr. Martin BIELIK"
    "\nemail: martin.bielik@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Nitre – Chrenovej"
    "\npredsedníčka: Mgr. Monika ČERENKOVÁ"
    "\nemail: monika.cerenkova@zvjs.sk"
    "\n\n## Ústave na výkon väzby a Ústave na výkon trestu odňatia slobody v Bratislave"
    "\npredsedníčka: Renáta HALÁNOVÁ"
    "\nemail: renata.halanova@zvjs.sk"
    "\n\n## Ústave na výkon väzby v Prešove a oddelení výkonu trestu v Sabinove"
    "\npredsedníčka: Bc. Bibiana VARKONDY"
    "\nemail: varkondyb@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Dubnici nad Váhom"
    "\npredseda: Miroslav PČOLA"
    "\nemail: zoozdn@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Ružomberku"
    "\npredseda: Milan URBAN"
    "\nemail: milan.urban@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Hrnčiarovciach nad Parnou"
    "\npredseda: Jozef ŠMIDOVIČ"
    "\nemail: jozef.smidovic@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody pre mladistvých v Sučanoch"
    "\npredsedníčka: Mgr. Jana RUFUSOVÁ"
    "\nemail: jana.rufusova@zvjs.sk; zoozsu@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody a Ústave na výkon väzby v Ilave"
    "\npredseda: Mgr. Martin ČURI"
    "\nemail: martin.curi@zvjs.sk"
    "\n\n## Nemocnici pre obvinených a odsúdených a Ústave na výkon trestu odňatia slobody v Trenčíne"
    "\npredseda: Anton BALÁŽ"
    "\nemail: Anton.Balaz@zvjs.sk"
    "\n\n## Ústave na výkon väzby a Ústave na výkon trestu odňatia slobody v Košiciach"
    "\npredseda: Vladimir MARINČIN"
    "\nemail: vladimir.marincin@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Želiezovciach"
    "\npredsedníčka: Kornélia NAGYOVÁ"
    "\nemail: kornelia.nagyova@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Košiciach – Šaci"
    "\npredseda: Ing. František KLIK"
    "\nemail: frantisek.klik@zvjs.sk"
    "\n\n## Ústave na výkon väzby a Ústave na výkon trestu odňatia slobody v Žiline"
    "\npredsedníčka: JUDr. Lucia HOLIENKOVÁ"
    "\nemail: lucia.holienkova@zvjs.sk"
    "\n\n## Ústave na výkon trestu odňatia slobody v Levoči"
    "\npredseda: Bc. Martin JURČO"
    "\nemail: martin.jurco@zvjs.sk"
)

REVIZNA_TEXT = (
    "Revíznu komisiu Odborového zväzu ZVJS tvoria členovia zväzu v zmysle Čl. 10 Stanov OZ ZVJS."
    "\n\n## Predsedníčka revíznej komisie Odborového zväzu"
    "\nMUDr. Dáša BACHLEDOVÁ"
    "\nemail: Dasa.Bachledova@zvjs.sk"
    "\n\n## Členka revíznej komisie Odborového zväzu"
    "\nDarina VACHALÍKOVÁ"
    "\nemail: darina.vachalikova@zvjs.sk"
    "\n\n## Členka revíznej komisie Odborového zväzu"
    "\nIng. Eva BOBÁKOVÁ"
    "\nemail: eva.bobakova@zvjs.sk"
)

# Predvolené texty z predchádzajúcej verzie (ešte bez záznamu v nastaveniach).
STARE_TEXTY = {"rada-predsedov": '## Radu predsedov Odborového zväzu ZVJS tvoria:\na) zástupcovia každej základnej organizácie, ktorými sú spravidla predsedovia základných organizácií\nb) členovia predsedníctva\n\n## Základné organizácie Odborového zväzu Zboru väzenskej a justičnej stráže pri:\n\n## Generálnom riaditeľstve Zboru väzenskej a justičnej stráže\npredsedníčka: Božena MAREČKOVÁ\nemail: bozena.mareckova@zvjs.sk\n\n## Ústave na výkon trestu odňatia slobody a Ústave na výkon väzby v Leopoldove\npredseda: Lukáš MALICH\nemail: lukas.malich@zvjs.sk'}

PREDVOLENE_TEXTY = {"predsednictvo": PREDSEDNICTVO_TEXT, "rada-predsedov": RADA_TEXT,
                    "revizna-komisia": REVIZNA_TEXT, "podporny-fond": PODPORNY_FOND_TEXT, "benefity": BENEFITY_TEXT, "uvod": UVODNY_TEXT, "o-nas": O_NAS_TEXT, "bozp": BOZP_TEXT,
                    "dokumenty-plan-zasadnuti": PLAN_TEXT}

# Podmenu „Dokumenty“ ako na ozzvjs.sk: (slug, názov, skupiny dokumentov na tej stránke).
KATEGORIE_DOKUMENTOV = [
    ("kolektivne-zmluvy-vyssieho-stupna", "Kolektívne zmluvy vyššieho stupňa",
     ["Kolektívne zmluvy vyššieho stupňa"]),
    ("kolektivne-zmluvy-su", "Kolektívne zmluvy v SÚ", ["Kolektívne zmluvy v SÚ"]),
    ("na-stiahnutie", "Dokumenty na stiahnutie",
     ["Vnútroodborové predpisy", "Podporný fond – žiadosti", "Právna ochrana členov",
      "Služobné vzory žiadostí"]),
    ("zakony-vyhlasky-nariadenia", "Zákony, vyhlášky, nariadenia", ["Zákony", "Vyhlášky", "Nariadenia vlády"]),
    ("vystrojovanie", "Vystrojovanie", ["Výstrojné súčiastky v ZVJS", "Predpisy k výstroju"]),
    ("plan-zasadnuti", "Plán zasadnutí", ["Plány zasadnutí na stiahnutie"]),
]
VSETKY_SKUPINY = [sk for _, _, skupiny in KATEGORIE_DOKUMENTOV for sk in skupiny]

# Každá stránka dokumentov má aj voľný text pod zoznamom (napr. kontakty právnych zástupcov).
STRANKY += [("dokumenty-" + slug, f"Dokumenty – {nazov} (text pod dokumentmi)", False)
            for slug, nazov, _ in KATEGORIE_DOKUMENTOV]

# Predpisy z pôvodného webu, vložené raz ako odkazy na Slov-Lex: (skupina, rok, číslo, názov).
SLOVLEX = "https://www.slov-lex.sk/pravne-predpisy/SK/ZZ/{rok}/{cislo}/"
POCIATOCNE_PREDPISY = [
    ("Zákony", 1998, 73, "Zákon č. 73/1998 Z. z. o štátnej službe príslušníkov Policajného zboru, Slovenskej "
     "informačnej služby, Zboru väzenskej a justičnej stráže Slovenskej republiky a Železničnej polície"),
    ("Zákony", 2001, 311, "Zákon č. 311/2001 Z. z. Zákonník práce"),
    ("Zákony", 2001, 4, "Zákon č. 4/2001 Z. z. o Zbore väzenskej a justičnej stráže"),
    ("Zákony", 2002, 328, "Zákon č. 328/2002 Z. z. o sociálnom zabezpečení policajtov a vojakov"),
    ("Zákony", 2003, 552, "Zákon č. 552/2003 Z. z. o výkone práce vo verejnom záujme"),
    ("Zákony", 2003, 553, "Zákon č. 553/2003 Z. z. o odmeňovaní niektorých zamestnancov pri výkone práce "
     "vo verejnom záujme"),
    ("Zákony", 1991, 2, "Zákon č. 2/1991 Zb. o kolektívnom vyjednávaní"),
    ("Zákony", 2005, 475, "Zákon č. 475/2005 Z. z. o výkone trestu odňatia slobody"),
    ("Zákony", 2006, 221, "Zákon č. 221/2006 Z. z. o výkone väzby"),
    ("Zákony", 2005, 300, "Zákon č. 300/2005 Z. z. Trestný zákon"),
    ("Zákony", 2005, 301, "Zákon č. 301/2005 Z. z. Trestný poriadok"),
    ("Zákony", 1994, 152, "Zákon č. 152/1994 Z. z. o sociálnom fonde"),
    ("Zákony", 1990, 83, "Zákon č. 83/1990 Zb. o združovaní občanov"),
    ("Zákony", 2004, 365, "Zákon č. 365/2004 Z. z. o rovnakom zaobchádzaní v niektorých oblastiach a o ochrane "
     "pred diskrimináciou (antidiskriminačný zákon)"),
    ("Zákony", 2006, 124, "Zákon č. 124/2006 Z. z. o bezpečnosti a ochrane zdravia pri práci"),
    ("Zákony", 2006, 125, "Zákon č. 125/2006 Z. z. o inšpekcii práce"),
    ("Vyhlášky", 2006, 437, "Vyhláška č. 437/2006 Z. z. Ministerstva spravodlivosti SR, ktorou sa vydáva "
     "Poriadok výkonu väzby"),
    ("Vyhlášky", 2008, 368, "Vyhláška č. 368/2008 Z. z. Ministerstva spravodlivosti SR, ktorou sa vydáva "
     "Poriadok výkonu trestu odňatia slobody"),
    ("Vyhlášky", 2016, 99, "Vyhláška č. 99/2016 Z. z. o podrobnostiach o ochrane zdravia pred záťažou teplom "
     "a chladom pri práci"),
    ("Predpisy k výstroju", 2016, 331, "Vyhláška č. 331/2016 Z. z. Ministerstva spravodlivosti SR o "
     "podrobnostiach o služobnej rovnošate Zboru väzenskej a justičnej stráže a jej vyobrazení"),
    ("Predpisy k výstroju", 2020, 27, "Vyhláška č. 27/2020 Z. z. Ministerstva spravodlivosti SR o hodnotách "
     "výstrojných súčastí pre príslušníkov Zboru väzenskej a justičnej stráže na rok 2020"),
    ("Nariadenia vlády", 2006, 395, "Nariadenie vlády č. 395/2006 Z. z. o minimálnych požiadavkách na "
     "poskytovanie a používanie osobných ochranných pracovných prostriedkov"),
    ("Nariadenia vlády", 2006, 276, "Nariadenie vlády č. 276/2006 Z. z. o minimálnych bezpečnostných a "
     "zdravotných požiadavkách pri práci so zobrazovacími jednotkami"),
]

# Typy článkov: (url, názov v menu).
TYPY_CLANKOV = {
    "aktuality": "Aktuality",
    "tlacove-spravy": "Tlačové správy",
}


def init_db(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS stranka (
            slug TEXT PRIMARY KEY,
            nazov TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            len_pre_clenov INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS clanok (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            typ TEXT NOT NULL,
            nazov TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            datum TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS obrazok (
            kluc TEXT PRIMARY KEY,
            typ TEXT NOT NULL,
            data BLOB NOT NULL,
            verzia INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dokument (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazov TEXT NOT NULL,
            kategoria TEXT NOT NULL DEFAULT '',
            nazov_suboru TEXT NOT NULL,
            typ_suboru TEXT NOT NULL,
            data BLOB NOT NULL,
            velkost INTEGER NOT NULL,
            len_pre_clenov INTEGER NOT NULL DEFAULT 0,
            datum TEXT NOT NULL
        );
        """
    )
    if "rubrika" not in [r[1] for r in db.execute("PRAGMA table_info(clanok)")]:
        db.execute("ALTER TABLE clanok ADD COLUMN rubrika TEXT NOT NULL DEFAULT ''")
    stlpce = [r[1] for r in db.execute("PRAGMA table_info(dokument)")]
    if "odkaz" not in stlpce:
        db.execute("ALTER TABLE dokument ADD COLUMN odkaz TEXT NOT NULL DEFAULT ''")
    if not nastavenie(db, "predpisy_vlozene"):
        db.executemany(
            "INSERT INTO dokument (nazov, kategoria, nazov_suboru, typ_suboru, data, velkost, odkaz, datum) "
            "VALUES (?, ?, '', '', x'', 0, ?, ?)",
            [(nazov, skupina, SLOVLEX.format(rok=rok, cislo=cislo), dnes())
             for skupina, rok, cislo, nazov in POCIATOCNE_PREDPISY],
        )
        nastav(db, "predpisy_vlozene", "1")
    for slug, nazov, len_clenovia in STRANKY:
        predvoleny = PREDVOLENE_TEXTY.get(slug, "")
        db.execute(
            "INSERT OR IGNORE INTO stranka (slug, nazov, text, len_pre_clenov) VALUES (?, ?, ?, ?)",
            (slug, nazov, predvoleny, int(len_clenovia)),
        )
        # Nový predvolený text sa doplní, len ak stránku zatiaľ nikto neupravil.
        kluc = "predvoleny_text_" + slug
        if predvoleny and nastavenie(db, kluc) != predvoleny:
            db.execute(
                "UPDATE stranka SET text = ? WHERE slug = ? AND (text = '' OR text = ?)",
                (predvoleny, slug, nastavenie(db, kluc) or STARE_TEXTY.get(slug, "")),
            )
            nastav(db, kluc, predvoleny)
    db.commit()


def dnes():
    return datetime.now(CASOVA_ZONA).strftime("%Y-%m-%d")


def format_datum(hodnota):
    try:
        return datetime.strptime(hodnota, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return hodnota


bp.add_app_template_filter(format_datum, "datum")


ODKAZ = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)])")
EMAIL = re.compile(r"([\w.+-]+@[\w-]+(?:\.[\w-]+)+)")


def _riadok(riadok):
    html = str(escape(riadok))
    html = ODKAZ.sub(r'<a href="\1" target="_blank" rel="noopener">\1</a>', html)
    return EMAIL.sub(r'<a href="mailto:\1">\1</a>', html)


def formatuj_text(text):
    """Jednoduché písanie textu bez HTML:
    prázdny riadok = nový odsek, Enter = nový riadok, „## “ = podnadpis,
    „- “ = odrážka, adresy https:// a e-maily sú odkazy."""
    casti = []
    for blok in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        riadky = [r.rstrip() for r in blok.strip().split("\n") if r.strip()]
        odsek, odrazky = [], []

        def uzavri():
            if odsek:
                casti.append("<p>" + "<br>".join(odsek) + "</p>")
                odsek.clear()
            if odrazky:
                casti.append("<ul>" + "".join(f"<li>{o}</li>" for o in odrazky) + "</ul>")
                odrazky.clear()

        for r in riadky:
            r = r.strip()
            if r.startswith("## "):
                uzavri()
                casti.append(f"<h3>{_riadok(r[3:].strip())}</h3>")
            elif r.startswith("- "):
                if odsek:
                    uzavri()
                odrazky.append(_riadok(r[2:].strip()))
            else:
                if odrazky:
                    uzavri()
                odsek.append(_riadok(r))
        uzavri()
    return Markup("\n".join(casti))


bp.add_app_template_filter(formatuj_text, "text")


def skratka(text, dlzka=220):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= dlzka else text[:dlzka].rsplit(" ", 1)[0] + " …"


bp.add_app_template_filter(skratka, "skratka")


def nacitaj_stranku(slug):
    stranka = get_db().execute("SELECT * FROM stranka WHERE slug = ?", (slug,)).fetchone()
    if stranka is None:
        abort(404)
    return stranka


def najnovsie_clanky(db, typ=None, pocet=3):
    if typ:
        return db.execute(
            "SELECT * FROM clanok WHERE typ = ? ORDER BY datum DESC, id DESC LIMIT ?", (typ, pocet)
        ).fetchall()
    return db.execute("SELECT * FROM clanok ORDER BY datum DESC, id DESC LIMIT ?", (pocet,)).fetchall()


# --- verejné stránky ---------------------------------------------------------


@bp.route("/stranka/<slug>")
def stranka(slug):
    s = nacitaj_stranku(slug)
    if slug == "uvod":
        return redirect(url_for("index"))
    if s["len_pre_clenov"] and g.get("clen") is None:
        return redirect(url_for("ucet.prihlasenie", dalej=request.path))
    skupiny = DOKUMENTY_NA_STRANKE.get(slug, [])
    dokumenty = {}
    if skupiny:
        prihlaseny = g.get("clen") is not None
        for sk in skupiny:
            dokumenty[sk] = get_db().execute(
                "SELECT id, nazov, nazov_suboru, velkost, odkaz, len_pre_clenov FROM dokument "
                "WHERE kategoria = ? AND (len_pre_clenov = 0 OR ?) ORDER BY datum DESC, id DESC",
                (sk, int(prihlaseny)),
            ).fetchall()
    return render_template("obsah/stranka.html", stranka=s, dokumenty=dokumenty)


@bp.route("/o-nas")
def o_nas():
    db = get_db()
    organizacie = db.execute(
        "SELECT slug, mesto, nazov FROM zakladna_organizacia ORDER BY poradie, mesto"
    ).fetchall()
    return render_template("obsah/o_nas.html", stranka=nacitaj_stranku("o-nas"), organizacie=organizacie)


@bp.route("/pre-clenov")
def pre_clenov():
    if g.get("clen") is None:
        return redirect(url_for("ucet.prihlasenie", dalej=request.path))
    from anketa import aktivne_ankety

    db = get_db()
    dokumenty = db.execute(
        "SELECT id, nazov, kategoria, nazov_suboru, velkost, datum FROM dokument "
        "WHERE len_pre_clenov = 1 ORDER BY datum DESC, id DESC"
    ).fetchall()
    return render_template("obsah/pre_clenov.html", stranka=nacitaj_stranku("pre-clenov"),
                           ankety=aktivne_ankety(db), dokumenty=dokumenty)


@bp.route("/<any(aktuality, 'tlacove-spravy'):typ>")
def clanky(typ):
    rubriky = RUBRIKY_TLACOVYCH if typ == "tlacove-spravy" else []
    rubrika = request.args.get("rubrika", rubriky[0][0] if rubriky else "")
    if rubriky and rubrika not in dict(rubriky):
        abort(404)
    sql, param = "SELECT * FROM clanok WHERE typ = ?", [typ]
    if rubriky:
        # Správy bez rubriky sa ukážu v prvej záložke.
        sql += " AND (rubrika = ? OR (rubrika = '' AND ? = ?))"
        param += [rubrika, rubrika, rubriky[0][0]]
    zoznam = get_db().execute(sql + " ORDER BY datum DESC, id DESC", param).fetchall()
    roky = {}
    for c in zoznam:
        roky.setdefault(c["datum"][:4], []).append(c)
    return render_template("obsah/clanky.html", typ=typ, nadpis=TYPY_CLANKOV[typ], roky=roky,
                           rubriky=rubriky, rubrika=rubrika)


@bp.route("/<any(aktuality, 'tlacove-spravy'):typ>/<int:clanok_id>")
def clanok(typ, clanok_id):
    c = get_db().execute("SELECT * FROM clanok WHERE id = ? AND typ = ?", (clanok_id, typ)).fetchone()
    if c is None:
        abort(404)
    return render_template("obsah/clanok.html", typ=typ, nadpis=TYPY_CLANKOV[typ], clanok=c)


@bp.route("/dokumenty")
@bp.route("/dokumenty/<kategoria_slug>")
def dokumenty(kategoria_slug=None):
    menu = {slug: (nazov, skupiny) for slug, nazov, skupiny in KATEGORIE_DOKUMENTOV}
    if kategoria_slug is not None and kategoria_slug not in menu:
        abort(404)
    prihlaseny = g.get("clen") is not None
    zoznam = get_db().execute(
        "SELECT id, nazov, kategoria, nazov_suboru, velkost, datum, len_pre_clenov, odkaz FROM dokument "
        "WHERE len_pre_clenov = 0 OR ? ORDER BY datum DESC, id DESC",
        (int(prihlaseny),),
    ).fetchall()
    podla_skupiny = {}
    for d in zoznam:
        podla_skupiny.setdefault(d["kategoria"] or "Ostatné", []).append(d)
    if kategoria_slug is not None:
        nadpis, skupiny = menu[kategoria_slug]
        if kategoria_slug == "plan-zasadnuti":
            nadpis = "Plán zasadnutí výkonných orgánov Odborového zväzu ZVJS"
        kategorie = {sk: podla_skupiny.get(sk, []) for sk in skupiny}
        text = nacitaj_stranku("dokumenty-" + kategoria_slug)["text"]
    else:
        # Všetko: najprv skupiny z menu, potom vlastné podľa abecedy; prázdne sa nezobrazia.
        nadpis, text = "Dokumenty", ""
        poradie = VSETKY_SKUPINY + sorted(k for k in podla_skupiny if k not in VSETKY_SKUPINY)
        kategorie = {k: podla_skupiny[k] for k in poradie if podla_skupiny.get(k)}
    return render_template("obsah/dokumenty.html", kategorie=kategorie, nadpis=nadpis, text=text,
                           jedna_skupina=len(kategorie) == 1 and kategoria_slug is not None)


@bp.route("/dokumenty/<int:dokument_id>/<path:nazov>")
def stiahni_dokument(dokument_id, nazov):
    d = get_db().execute("SELECT * FROM dokument WHERE id = ?", (dokument_id,)).fetchone()
    if d is None:
        abort(404)
    if d["len_pre_clenov"] and g.get("clen") is None:
        return redirect(url_for("ucet.prihlasenie", dalej=request.path))
    if d["odkaz"]:
        return redirect(d["odkaz"])
    zobrazit = d["typ_suboru"] in ("application/pdf", "image/jpeg", "image/png")
    nazov_ascii = d["nazov_suboru"].encode("ascii", "ignore").decode() or "dokument"
    return Response(
        d["data"],
        mimetype=d["typ_suboru"],
        headers={"Content-Disposition": f'{"inline" if zobrazit else "attachment"}; filename="{nazov_ascii}"'},
    )


# --- obrázky na pozadí (úvod a pásy s nadpisom) ---

OBRAZKY = {"uvod": "Pozadie úvodu na hlavnej stránke", "pas": "Pozadie pásu s nadpisom na podstránkach"}


@bp.app_context_processor
def inject_obrazky():
    try:
        riadky = get_db().execute("SELECT kluc, verzia FROM obrazok").fetchall()
    except Exception:
        return {"obrazky": {}}
    return {"obrazky": {r["kluc"]: url_for("obsah.obrazok", kluc=r["kluc"], v=r["verzia"]) for r in riadky}}


@bp.route("/obrazok/<kluc>")
def obrazok(kluc):
    r = get_db().execute("SELECT typ, data FROM obrazok WHERE kluc = ?", (kluc,)).fetchone()
    if r is None:
        abort(404)
    return Response(r["data"], mimetype=r["typ"], headers={"Cache-Control": "public, max-age=31536000"})


@bp.route("/spravca/obrazok/<kluc>", methods=["POST"])
@len_spravca
def nahraj_obrazok(kluc):
    if kluc not in OBRAZKY:
        abort(404)
    db = get_db()
    if request.form.get("akcia") == "zmazat":
        db.execute("DELETE FROM obrazok WHERE kluc = ?", (kluc,))
        db.commit()
        flash("Obrázok bol odstránený.")
        return redirect(url_for("obsah.spravca_obsah"))
    subor = request.files.get("subor")
    data = subor.read() if subor and subor.filename else b""
    if not data or not (subor.mimetype or "").startswith("image/"):
        flash("Vyberte obrázok (JPG alebo PNG).")
    elif len(data) > 5 * 1024 * 1024:
        flash("Obrázok je väčší ako 5 MB.")
    else:
        db.execute(
            "INSERT INTO obrazok (kluc, typ, data, verzia) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(kluc) DO UPDATE SET typ = excluded.typ, data = excluded.data, verzia = excluded.verzia",
            (kluc, subor.mimetype, data, int(datetime.now().timestamp())),
        )
        db.commit()
        flash("Obrázok bol nahraný.")
    return redirect(url_for("obsah.spravca_obsah"))


# --- správa základných organizácií ---


@bp.route("/spravca/zo/<slug>", methods=["GET", "POST"])
@len_spravca
def uprav_zo(slug):
    db = get_db()
    zo = db.execute("SELECT * FROM zakladna_organizacia WHERE slug = ?", (slug,)).fetchone()
    if zo is None:
        abort(404)
    if request.method == "POST":
        db.execute("UPDATE zakladna_organizacia SET popis = ? WHERE slug = ?",
                   (request.form.get("popis", "").strip(), slug))
        db.commit()
        flash(f"Stránka ZO {zo['mesto']} bola uložená.")
        return redirect(url_for("obsah.spravca_obsah"))
    return render_template("obsah/uprava_zo.html", zo=zo)


# --- správa obsahu -----------------------------------------------------------


@bp.route("/spravca/obsah")
@len_spravca
def spravca_obsah():
    db = get_db()
    return render_template(
        "obsah/spravca.html",
        stranky=db.execute("SELECT slug, nazov, length(text) AS dlzka FROM stranka").fetchall(),
        clanky=db.execute("SELECT id, typ, nazov, datum FROM clanok ORDER BY datum DESC, id DESC").fetchall(),
        dokumenty=db.execute(
            "SELECT id, nazov, kategoria, nazov_suboru, velkost, len_pre_clenov, datum, odkaz FROM dokument "
            "ORDER BY datum DESC, id DESC"
        ).fetchall(),
        typy=TYPY_CLANKOV,
        kategorie_dokumentov=VSETKY_SKUPINY,
        nazvy_obrazkov=OBRAZKY,
        organizacie=db.execute("SELECT slug, mesto FROM zakladna_organizacia ORDER BY poradie, mesto").fetchall(),
    )


@bp.route("/spravca/stranka/<slug>", methods=["GET", "POST"])
@len_spravca
def uprav_stranku(slug):
    s = nacitaj_stranku(slug)
    if request.method == "POST":
        db = get_db()
        db.execute("UPDATE stranka SET text = ? WHERE slug = ?", (request.form.get("text", "").strip(), slug))
        db.commit()
        flash(f"Stránka „{s['nazov']}“ bola uložená.")
        return redirect(url_for("obsah.spravca_obsah"))
    return render_template("obsah/uprava_stranky.html", stranka=s)


@bp.route("/spravca/clanok/novy/<typ>", methods=["GET", "POST"])
@bp.route("/spravca/clanok/<int:clanok_id>", methods=["GET", "POST"])
@len_spravca
def uprav_clanok(typ=None, clanok_id=None):
    db = get_db()
    c = None
    if clanok_id is not None:
        c = db.execute("SELECT * FROM clanok WHERE id = ?", (clanok_id,)).fetchone()
        if c is None:
            abort(404)
        typ = c["typ"]
    if typ not in TYPY_CLANKOV:
        abort(404)
    if request.method == "POST":
        f = request.form
        nazov = f.get("nazov", "").strip()
        datum = f.get("datum", "").strip() or dnes()
        if not nazov:
            flash("Zadajte nadpis článku.")
            return render_template("obsah/uprava_clanku.html", clanok=f, typ=typ, nazov_typu=TYPY_CLANKOV[typ],
                                   rubriky=RUBRIKY_TLACOVYCH if typ == "tlacove-spravy" else [])
        if c is None:
            db.execute("INSERT INTO clanok (typ, nazov, text, datum, rubrika) VALUES (?, ?, ?, ?, ?)",
                       (typ, nazov, f.get("text", "").strip(), datum, f.get("rubrika", "")))
        else:
            db.execute("UPDATE clanok SET nazov = ?, text = ?, datum = ?, rubrika = ? WHERE id = ?",
                       (nazov, f.get("text", "").strip(), datum, f.get("rubrika", ""), clanok_id))
        db.commit()
        flash("Článok bol uložený.")
        return redirect(url_for("obsah.spravca_obsah"))
    return render_template("obsah/uprava_clanku.html", clanok=c or {"datum": dnes()}, typ=typ,
                           nazov_typu=TYPY_CLANKOV[typ],
                           rubriky=RUBRIKY_TLACOVYCH if typ == "tlacove-spravy" else [])


@bp.route("/spravca/clanok/<int:clanok_id>/zmazat", methods=["POST"])
@len_spravca
def zmaz_clanok(clanok_id):
    db = get_db()
    db.execute("DELETE FROM clanok WHERE id = ?", (clanok_id,))
    db.commit()
    flash("Článok bol zmazaný.")
    return redirect(url_for("obsah.spravca_obsah"))


@bp.route("/spravca/dokument", methods=["POST"])
@len_spravca
def nahraj_dokument():
    f = request.form
    subor = request.files.get("subor")
    odkaz = f.get("odkaz", "").strip()
    db = get_db()
    if subor and subor.filename:
        data = subor.read()
        if len(data) > MAX_VELKOST_SUBORU:
            flash("Súbor je väčší ako 15 MB.")
            return redirect(url_for("obsah.spravca_obsah"))
        nazov_suboru = re.sub(r"[^\w.\- ]", "_", subor.filename).strip() or "dokument"
        typ, odkaz = subor.mimetype or "application/octet-stream", ""
    elif odkaz:
        if not re.match(r"https?://", odkaz):
            flash("Odkaz musí začínať https:// alebo http://")
            return redirect(url_for("obsah.spravca_obsah"))
        data, nazov_suboru, typ = b"", "", ""
    else:
        flash("Vyberte súbor alebo vložte odkaz.")
        return redirect(url_for("obsah.spravca_obsah"))
    nazov = f.get("nazov", "").strip() or nazov_suboru or odkaz
    db.execute(
        "INSERT INTO dokument (nazov, kategoria, nazov_suboru, typ_suboru, data, velkost, len_pre_clenov, odkaz, datum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nazov, f.get("kategoria", "").strip(), nazov_suboru, typ, data, len(data),
         int(bool(f.get("len_pre_clenov"))), odkaz, dnes()),
    )
    db.commit()
    flash(f"Dokument „{nazov}“ bol pridaný.")
    return redirect(url_for("obsah.spravca_obsah"))


@bp.route("/spravca/dokument/<int:dokument_id>/zmazat", methods=["POST"])
@len_spravca
def zmaz_dokument(dokument_id):
    db = get_db()
    db.execute("DELETE FROM dokument WHERE id = ?", (dokument_id,))
    db.commit()
    flash("Dokument bol zmazaný.")
    return redirect(url_for("obsah.spravca_obsah"))
