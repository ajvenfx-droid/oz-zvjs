# Dotazníky pridávané cez GitHub

Každý súbor `*.txt` v tomto priečinku sa pri aktualizácii stránky pridá ako dotazník.

```
nazov: Názov dotazníka
zo: banska-bystrica          (prázdne = pre všetkých členov)
koniec: 2026-11-10T22:00     (nepovinné)

---
Úvodný text (nepovinné, medzi riadkami ---)
---
Otázka
- možnosť
- možnosť
Anonymná otázka [anonymná] [max 2]   ([max 2] = pri „+“ najviac 2 možnosti)
+ možnosť (viac možností)
+ možnosť
Otázka s vlastnou odpoveďou
Podotázka [ak 1.2]           (zobrazí sa len ak v otázke 1 zvolí 2. možnosť)
- Iné [vpísať]                (možnosť, pri ktorej člen doplní text)
- možnosť
```

Kým dotazník nikto nezačal vypĺňať, zmeny v súbore sa prenesú na stránku.
Dotazník zmazaný na stránke sa znovu nevytvorí.
