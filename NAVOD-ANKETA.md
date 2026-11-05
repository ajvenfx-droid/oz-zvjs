# Anketa pre členov – nahratie a používanie

## Zmenené a nové súbory

- `app.py` (upravený) – pripája ankety, na úvodnej stránke zobrazuje prebiehajúce ankety
- `db.py` (nový) – spoločné pripojenie k databáze
- `anketa.py` (nový) – hlasovanie, správa ankiet, výsledky, export
- `nastav_heslo.py` (nový) – nastavenie hesla správcu
- `templates/base.html`, `templates/index.html` (upravené) – odkaz „Správa ankiet“ v päte, zoznam ankiet na úvode
- `templates/anketa/` (nový priečinok) – stránky ankety a správcu
- `static/style.css` (doplnený) – vzhľad formulárov a výsledkov

Databáza `oz_zvjs.db` sa nemaže, nové tabuľky sa doplnia automaticky.
Anketa „Nákup hodiniek na plaváreň“ (1 až 5 ks) sa vytvorí automaticky pri prvom spustení.

## Nahratie na PythonAnywhere

Anketa je súčasťou kódu na GitHube, na stránku sa dostane cez `aktualizuj.sh`
(pozri `NAVOD-PYTHONANYWHERE.md`). Len prvýkrát nastavte heslo správcu (aspoň 8 znakov):

```
cd ~/oz-zvjs && python3 nastav_heslo.py
```

Pri písaní hesla sa na obrazovke nič nezobrazuje, to je v poriadku.
Potom spustite `bash ~/oz-zvjs/aktualizuj.sh`, aby sa stránka reštartovala.

## Používanie

1. Otvorte `https://ozzvjs.pythonanywhere.com/anketa/spravca` (alebo odkaz **Správa ankiet** v päte stránky) a prihláste sa.
2. **Nahrajte zoznam platných odborárskych čísel.** V Exceli: prvý stĺpec odborárske číslo,
   druhý stĺpec meno (nepovinné). Uložte cez **Uložiť ako → CSV (oddelený bodkočiarkou)**.
   Kým zoznam nenahráte, nikto nemôže hlasovať.
3. Pri ankete kliknite **Upraviť** a nastavte dátum ukončenia (nepovinné).
4. Odkaz na anketu pošlite členom:
   `https://ozzvjs.pythonanywhere.com/anketa/nakup-hodiniek-na-plavaren`
5. Výsledky: **Výsledky** pri ankete. Tlačidlom **Stiahnuť do Excelu (CSV)** stiahnete súhrn aj hlasy jednotlivých členov.

## Ako písať otázky novej ankety

Každá otázka na samostatný riadok, pod ňu možnosti:

```
Nákup hodiniek na plaváreň
- 1 ks
- 2 ks
Ktoré dni chodíte na plaváreň?
+ pondelok
+ streda
Máte ďalšie návrhy?
```

- `- možnosť` – člen vyberie jednu možnosť
- `+ možnosť` – člen môže vybrať viac možností
- otázka bez možností – člen napíše vlastnú odpoveď

Keď už v ankete niekto hlasoval, otázky sa nedajú meniť (názov, úvod a dátum ukončenia áno).
