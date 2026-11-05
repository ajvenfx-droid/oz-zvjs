# Stránka OZ ZVJS

Webová stránka Odborového zväzu Zboru väzenskej a justičnej stráže.
Aktuálne obsahuje hlavnú stránku s názvom zväzu, zoznam základných organizácií (ZO),
podstránku pre každú ZO a ankety pre členov (pozri `NAVOD-ANKETA.md`).

## Ako spustiť (vnútorný server)

Potrebný je Python 3.10 alebo novší.

```bash
cd oz-zvjs
python3 -m venv venv
# Linux/macOS:
source venv/bin/activate
# Windows:
# venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Stránka potom beží na adrese `http://localhost:5000`. Z iných počítačov vo vnútornej
sieti je dostupná na `http://<IP-adresa-servera>:5000`.

Voliteľné nastavenia (premenné prostredia):

- `OZ_PORT` – port (predvolene 5000)
- `OZ_HOST` – `127.0.0.1` ak má byť stránka dostupná len z toho istého počítača
- `OZ_DB_PATH` – cesta k databáze (predvolene `oz_zvjs.db` vedľa `app.py`)

Databáza SQLite sa vytvorí automaticky pri prvom spustení.

## Štruktúra

- `app.py` – aplikácia (Flask)
- `db.py` – pripojenie k databáze
- `anketa.py` – ankety pre členov
- `ucet.py` – kontá a prihlasovanie členov
- `nastav_heslo.py` – nastavenie hesla správcu ankiet
- `templates/` – HTML šablóny stránok
- `static/style.css` – vzhľad

## Pridanie ďalšej základnej organizácie

Zatiaľ sa ZO pridávajú do zoznamu `POCIATOCNE_ZO` v `app.py`
(skratka do adresy, mesto, plný názov) a vložia sa pri ďalšom spustení.
Neskôr bude na to správa priamo na stránke.

## Kontá členov

- Prihlasovacie meno je odborárske číslo zo zoznamu členov (nahráva sa v správe).
- Správca v časti **Kontá členov** (`/spravca/konta`) vytvorí aktivačné kódy a stiahne ich do Excelu.
- Člen na `/aktivacia` zadá číslo, kód a zvolí si heslo. Potom sa prihlasuje na `/prihlasenie`.
- Zabudnuté heslo: správca vydá nový aktivačný kód, staré heslo prestane platiť.
- Po 5 nesprávnych pokusoch sa konto na 15 minút zamkne.
- Každé konto má rolu (zatiaľ len `clen`), ďalšie práva pribudnú neskôr.
