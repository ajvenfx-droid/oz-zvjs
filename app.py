"""Webová stránka Odborového zväzu Zboru väzenskej a justičnej stráže."""
import os

from flask import Flask, abort, g, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import anketa
import evidencia
import hodinky
import nasadenie
import obsah
import ucet
import zaloha
from db import connect, close_db, get_db

NAZOV_ZVAZU = "ODBOROVÝ ZVÄZ ZBORU VÄZENSKEJ A JUSTIČNEJ STRÁŽE"

# Základné organizácie (slug, mesto, plný názov, predvolený popis) v poradí ako v Rade predsedov.
PRI = "ZÁKLADNÁ ORGANIZÁCIA ODBOROVÉHO ZVÄZU ZBORU VÄZENSKEJ A JUSTIČNEJ STRÁŽE PRI "
POCIATOCNE_ZO = [
    ('generalne-riaditelstvo', 'Generálne riaditeľstvo ZVJS',
     PRI + 'GENERÁLNOM RIADITEĽSTVE ZBORU VÄZENSKEJ A JUSTIČNEJ STRÁŽE',
     'predsedníčka: Božena MAREČKOVÁ\nemail: bozena.mareckova@zvjs.sk'),
    ('leopoldov', 'Leopoldov',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY A ÚSTAVE NA VÝKON VÄZBY V LEOPOLDOVE',
     'predseda: Lukáš MALICH\nemail: lukas.malich@zvjs.sk'),
    ('banska-bystrica', 'Banská Bystrica',
     'ZÁKLADNÁ ORGANIZÁCIA ODBOROVÉHO ZVÄZU ZBORU VÄZENSKEJ A JUSTIČNEJ STRÁŽE PRI ÚSTAVE NA VÝKON VÄZBY A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY BANSKÁ BYSTRICA',
     'predseda: Mgr. Ivan CHROMEK\nemail: ivan.chromek@zvjs.sk'),
    ('nitra', 'Nitra',
     PRI + 'ÚSTAVE NA VÝKON VÄZBY A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V NITRE',
     'predseda: Mgr. Ľubor HUPKA\nemail: lubor.hupka@zvjs.sk'),
    ('banska-bystrica-kralova', 'Banská Bystrica – Kráľová',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V BANSKEJ BYSTRICI – KRÁĽOVEJ',
     'predseda: Mgr. Martin BIELIK\nemail: martin.bielik@zvjs.sk'),
    ('nitra-chrenova', 'Nitra – Chrenová',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V NITRE – CHRENOVEJ',
     'predsedníčka: Mgr. Monika ČERENKOVÁ\nemail: monika.cerenkova@zvjs.sk'),
    ('bratislava', 'Bratislava',
     PRI + 'ÚSTAVE NA VÝKON VÄZBY A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V BRATISLAVE',
     'predsedníčka: Renáta HALÁNOVÁ\nemail: renata.halanova@zvjs.sk'),
    ('presov-sabinov', 'Prešov a Sabinov',
     PRI + 'ÚSTAVE NA VÝKON VÄZBY V PREŠOVE A ODDELENÍ VÝKONU TRESTU V SABINOVE',
     'predsedníčka: Bc. Bibiana VARKONDY\nemail: varkondyb@zvjs.sk'),
    ('dubnica-nad-vahom', 'Dubnica nad Váhom',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V DUBNICI NAD VÁHOM',
     'predseda: Miroslav PČOLA\nemail: zoozdn@zvjs.sk'),
    ('ruzomberok', 'Ružomberok',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V RUŽOMBERKU',
     'predseda: Milan URBAN\nemail: milan.urban@zvjs.sk'),
    ('hrnciarovce-nad-parnou', 'Hrnčiarovce nad Parnou',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V HRNČIAROVCIACH NAD PARNOU',
     'predseda: Jozef ŠMIDOVIČ\nemail: jozef.smidovic@zvjs.sk'),
    ('sucany', 'Sučany',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY PRE MLADISTVÝCH V SUČANOCH',
     'predsedníčka: Mgr. Jana RUFUSOVÁ\nemail: jana.rufusova@zvjs.sk; zoozsu@zvjs.sk'),
    ('ilava', 'Ilava',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY A ÚSTAVE NA VÝKON VÄZBY V ILAVE',
     'predseda: Mgr. Martin ČURI\nemail: martin.curi@zvjs.sk'),
    ('trencin', 'Trenčín',
     PRI + 'NEMOCNICI PRE OBVINENÝCH A ODSÚDENÝCH A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V TRENČÍNE',
     'predseda: Anton BALÁŽ\nemail: Anton.Balaz@zvjs.sk'),
    ('kosice', 'Košice',
     PRI + 'ÚSTAVE NA VÝKON VÄZBY A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V KOŠICIACH',
     'predseda: Vladimir MARINČIN\nemail: vladimir.marincin@zvjs.sk'),
    ('zeliezovce', 'Želiezovce',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V ŽELIEZOVCIACH',
     'predsedníčka: Kornélia NAGYOVÁ\nemail: kornelia.nagyova@zvjs.sk'),
    ('kosice-saca', 'Košice – Šaca',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V KOŠICIACH – ŠACI',
     'predseda: Ing. František KLIK\nemail: frantisek.klik@zvjs.sk'),
    ('zilina', 'Žilina',
     PRI + 'ÚSTAVE NA VÝKON VÄZBY A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V ŽILINE',
     'predsedníčka: JUDr. Lucia HOLIENKOVÁ\nemail: lucia.holienkova@zvjs.sk'),
    ('levoca', 'Levoča',
     PRI + 'ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY V LEVOČI',
     'predseda: Bc. Martin JURČO\nemail: martin.jurco@zvjs.sk'),
]

class _Poziadavka(Flask.request_class):
    """Pri hromadnom nahrávaní fotiek povolí väčší objem (funguje aj na starších verziách Flasku)."""
    @property
    def max_content_length(self):
        if self.path.startswith("/spravca/evidencia/") and self.path.endswith("/fotky"):
            return 300 * 1024 * 1024
        from flask import current_app
        return current_app.config.get("MAX_CONTENT_LENGTH")


app = Flask(__name__)
app.request_class = _Poziadavka
# PythonAnywhere beží za proxy; vďaka tomu majú odkazy v e-mailoch správne https://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Mení sa pri každej zmene style.css, aby si prehliadače stiahli nový vzhľad.
VERZIA_STYLOV = int(os.path.getmtime(os.path.join(app.static_folder, "style.css")))
app.teardown_appcontext(close_db)
app.register_blueprint(anketa.bp)
app.register_blueprint(ucet.bp)
app.register_blueprint(obsah.bp)
app.register_blueprint(evidencia.bp)
app.register_blueprint(hodinky.bp)
app.register_blueprint(hodinky.bp_clen)
app.register_blueprint(nasadenie.bp)
app.register_blueprint(zaloha.bp)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # nahrávané dokumenty


def init_db():
    db = connect()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS zakladna_organizacia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            mesto TEXT NOT NULL,
            nazov TEXT NOT NULL,
            popis TEXT NOT NULL DEFAULT ''
        )
        """
    )
    if "poradie" not in [r[1] for r in db.execute("PRAGMA table_info(zakladna_organizacia)")]:
        db.execute("ALTER TABLE zakladna_organizacia ADD COLUMN poradie INTEGER NOT NULL DEFAULT 0")
    for poradie, (slug, mesto, nazov, popis) in enumerate(POCIATOCNE_ZO, 1):
        db.execute(
            "INSERT OR IGNORE INTO zakladna_organizacia (slug, mesto, nazov, popis) VALUES (?, ?, ?, ?)",
            (slug, mesto, nazov, popis),
        )
        # Popis sa doplní len tam, kde ho ešte nikto nevyplnil.
        db.execute(
            "UPDATE zakladna_organizacia SET poradie = ?, popis = CASE WHEN popis = '' THEN ? ELSE popis END "
            "WHERE slug = ?",
            (poradie, popis, slug),
        )
    db.commit()
    anketa.init_db(db)
    ucet.init_db(db)
    obsah.init_db(db)
    evidencia.init_db(db)
    hodinky.init_db(db)
    # Dotazníky pripravené ako súbory v priečinku dotazniky/ (pridávajú sa cez GitHub).
    anketa.importuj_dotazniky(db, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dotazniky"))
    app.secret_key = anketa.secret_key(db)
    db.close()


def zostav_menu():
    """Hlavné menu ako na ozzvjs.sk; aktívna položka sa určí podľa adresy."""
    clen = getattr(g, "clen", None)
    polozky = [
        ("Domov", url_for("index"), []),
        ("Aktuality", url_for("obsah.clanky", typ="aktuality"), []),
        ("O nás", url_for("obsah.o_nas"), [
            ("O zväze", url_for("obsah.o_nas")),
            ("Základné organizácie", url_for("obsah.o_nas") + "#organizacie"),
        ]),
        ("Venujte 2 % z daní", url_for("obsah.stranka", slug="venujte-2-percenta"), []),
        ("Výkon kontroly BOZP", url_for("obsah.stranka", slug="bozp"), []),
        ("Dokumenty", url_for("obsah.dokumenty"), [
            (nazov, url_for("obsah.dokumenty", kategoria_slug=slug))
            for slug, nazov, _ in obsah.KATEGORIE_DOKUMENTOV
        ]),
        ("Pre členov", url_for("obsah.pre_clenov"), [
            ("Benefity", url_for("obsah.stranka", slug="benefity")),
            ("Podporný fond", url_for("obsah.stranka", slug="podporny-fond")),
            ("Ankety a dokumenty pre členov", url_for("obsah.pre_clenov")),
            ("Moje konto", url_for("ucet.moje_konto")) if clen
            else ("Prihlásiť sa", url_for("ucet.prihlasenie")),
        ]),
        ("Tlačové správy", url_for("obsah.clanky", typ="tlacove-spravy"), []),
        ("Kontakty", url_for("obsah.stranka", slug="kontakty"), [
            ("Kontakty", url_for("obsah.stranka", slug="kontakty")),
            ("Predsedníctvo", url_for("obsah.stranka", slug="predsednictvo")),
            ("Rada predsedov", url_for("obsah.stranka", slug="rada-predsedov")),
            ("Revízna komisia", url_for("obsah.stranka", slug="revizna-komisia")),
        ]),
    ]
    cesta = request.path
    menu = []
    for nazov, url, podmenu in polozky:
        adresy = [url] + [u.split("#")[0] for _, u in podmenu]
        if url == "/":
            aktivna = cesta == "/"
        else:
            aktivna = any(cesta == a or cesta.startswith(a.rstrip("/") + "/") for a in adresy)
        menu.append({
            "nazov": nazov, "url": url, "aktivna": aktivna,
            "podmenu": [{"nazov": n, "url": u} for n, u in podmenu],
        })
    return menu


def verzia_stranky():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERZIA"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


VERZIA_STRANKY = verzia_stranky()


@app.context_processor
def inject_globals():
    return {
        "verzia_stranky": VERZIA_STRANKY,
        "nazov_zvazu": NAZOV_ZVAZU,
        "verzia_stylov": VERZIA_STYLOV,
        "logo_subor": "logo.png",
        "menu": zostav_menu() if request else [],
    }


@app.route("/")
def index():
    db = get_db()
    return render_template(
        "index.html",
        uvod=obsah.nacitaj_stranku("uvod"),
        aktuality=obsah.najnovsie_clanky(db, "aktuality"),
        tlacove=obsah.najnovsie_clanky(db, "tlacove-spravy"),
    )


@app.route("/zo/<slug>")
def organizacia(slug):
    zo = get_db().execute(
        "SELECT * FROM zakladna_organizacia WHERE slug = ?", (slug,)
    ).fetchone()
    if zo is None:
        abort(404)
    return render_template("organizacia.html", zo=zo)


@app.errorhandler(404)
def nenajdene(exc):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    # 0.0.0.0 = dostupné aj z ostatných počítačov vo vnútornej sieti.
    host = os.environ.get("OZ_HOST", "0.0.0.0")
    port = int(os.environ.get("OZ_PORT", "5000"))
    app.run(host=host, port=port, debug=os.environ.get("OZ_DEBUG") == "1")
