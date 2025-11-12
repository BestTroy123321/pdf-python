# pdf-xml – Ekstrakcja XML (XFA) z plików PDF

Prosty skrypt w Pythonie do wyciągania pakietów XML z dokumentów PDF typu **XFA**.
Domyślnie zwraca pakiet `template` (struktura pól formularza). Możesz także wybrać `datasets` (wartości danych) lub inny dostępny pakiet.

## Wymagania

- Python 3.10+
- Zależności:
  - `pikepdf` – odczyt obiektów PDF
  - `lxml` – parsowanie i formatowanie XML

Instalacja:

```bash
pip install -r requirements.txt
```

## Pliki

- `/src/xfa_extract.py` – moduł i narzędzie CLI do ekstrakcji XFA
- `/requirements.txt` – zależności

## Użycie (CLI)

Jednym poleceniem, bez argumentów. Plik PDF musi nazywać się `xfa.pdf` i znajdować się w katalogu głównym.

```bash
python extract_schema.py
```

Wynik zapisuje się w katalogu głównym jako `schemat.xml`.

Dodatkowo skrypt tworzy plik z kluczami pól do wypełnienia:

```
pola.txt
```

Każda linia zawiera jeden klucz (nazwę) pola. Nazwy są wykrywane z XFA (`template`, `bind/@ref`) lub – jeśli dokument nie jest XFA – z klasycznego AcroForm (`/Fields`). Pola oznaczone jako tylko do odczytu nie są uwzględniane.

## Wypełnianie XFA z JSON

Przygotuj plik `dane.json` w katalogu głównym (obiekt JSON), np.:

```json
{
  "Imie": "Jan",
  "Nazwisko": "Kowalski",
  "$..form1..TextField1[0]": "Przykładowa wartość"
}
```

Uruchom wypełnianie:

```bash
python fill_xfa.py
```

Wynik: `wypelniony.pdf` w katalogu głównym.

Zasady mapowania kluczy:
- Jeśli klucz równy jest ścieżce `bind/@ref`, wstawiamy wartość dokładnie w ten węzeł.
- Jeśli klucz odpowiada `field@name`, używamy zmapowanego `bind/@ref` z szablonu.
- W pozostałych przypadkach traktujemy klucz jako ścieżkę i tworzymy brakujące węzły w `<datasets>/<data>`.

Skrypt wyciąga wyłącznie strukturę pól (`template`). Jeśli pakiet `template` nie jest dostępny jako osobny strumień XFA, skrypt próbuje odnaleźć element `<template>` wewnątrz pełnego XDP i zapisać tylko tę część.

## Ekstrakcja AcroForm – schemat i pola

Dla plików PDF opartych o **AcroForm** możesz wyeksportować strukturę pól (schemat) i listę pól do wypełnienia.

```bash
python extract_acroform.py [opcjonalnie: ścieżka_pdf] [opcjonalnie: ścieżka_xml] [opcjonalnie: ścieżka_pola]
```

- Domyślnie wejście: `xfa.pdf`
- Wyjście:
  - `schemat_acro.xml` – hierarchia pól (`name`, `type`, `flags`, `readonly`, opcje dla `/Ch`, eksporty dla `/Btn`)
  - `pola_acro.txt` – płaska lista nazw pól możliwych do wypełnienia (bez `readonly`, `pushbutton`, `Sig`)

Wartości wypełnione w PDF:
- `schemat_acro.xml` zawiera także bieżące wartości pól:
  - Tekst (`/Tx`) i wybór (`/Ch`) – elementy `<value>` z aktualną wartością (dla list wielokrotnego wyboru – wiele `<value>`).
  - Checkbox/Radio (`/Btn`) –
    - Radio: `<value>` z wybranym exportem (np. `/M`), brak wartości oznacza niewybrany.
    - Checkbox: `<value>` z `true`/`false` (na podstawie `/V` i nazw ON z `/AP`).

Uwagi:
- Jeśli w dokumencie brak `/AcroForm`, skrypt wypisze komunikat i zakończy działanie.
- Nazwy pól w `pola_acro.txt` są pełnymi ścieżkami złożonymi z `/T` kolejnych poziomów (np. `Sekcja1.Dane.Imie`).

## Wypełnianie AcroForm z JSON

Ten tryb obsługuje klasyczne formularze AcroForm (bez XFA). Dane z `dane.json` są mapowane na nazwy pól AcroForm.

- Plik wejściowy PDF: `xfa.pdf` (możesz podać inną ścieżkę w argumencie)
- Plik danych JSON: `dane.json`
- Polecenie:

```
python fill_acroform.py [opcjonalnie: ścieżka_pdf] [opcjonalnie: ścieżka_json] [opcjonalnie: ścieżka_wyjścia]
```

Przykładowy `dane.json` dla AcroForm:

```json
{
  "Imie": "Jan",
  "Nazwisko": "Kowalski",
  "ZgodaMarketing": true,
  "Plec": "M"  
}
```

Zasady mapowania:
- Klucze JSON dopasowują się do pełnych nazw pól (np. `Sekcja1.Imie`) lub skrótowych `/T` (jeśli unikalne).
- Pola tekstowe (`/Tx`): wartość jako string → `/V`.
- Pola wyboru (`/Ch` – Combo/List): wartość jako string (lub pierwszy element listy).
- Checkbox/Radio (`/Btn`):
  - Checkbox: `true`/`"Yes"`/`"On"` → zaznaczone; `false`/puste → odznaczone.
  - Radio: wartość dopasowana do nazwy eksportu opcji (np. `"M"` lub `"/M"`).
- Skrypt ustawia `/NeedAppearances = true`, aby viewer wygenerował wygląd wartości.

Wyjście:
- Domyślnie zapis do `wypelniony_acro.pdf`, a w razie blokady – do `wypelniony_acro_alt.pdf`.

Uwaga: Jeśli PDF ma zarówno XFA, jak i AcroForm, użyj odpowiedniego skryptu (`fill_xfa.py` dla XFA, `fill_acroform.py` dla AcroForm). W razie braku `/AcroForm` skrypt wypisze komunikat i zakończy działanie.

## API (Python)

Przykład użycia w kodzie:

```python
from src.xfa_extract import read_xfa_packets, extract_xfa_xml

# Wszystkie pakiety XFA jako bytes
packets = read_xfa_packets("plik.pdf")  # dict: name -> bytes

# Wyciągnięcie preferowanego pakietu jako tekst (pretty=True aby sformatować XML)
name, xml_text = extract_xfa_xml("plik.pdf", packet="template", pretty=True)
print(name)
print(xml_text)
```

## Ograniczenia i uwagi

- Skrypt oczekuje, że PDF zawiera `/AcroForm` z `/XFA`.
- Niektóre PDF-y mogą mieć tylko jeden strumień XFA – wówczas nazwa pakietu będzie `xfa`.
- Przy problematycznych dokumentach formatowanie (`--pretty`) może się nie powieść; w takim wypadku zapisywany jest surowy XML.

## Typowe problemy

- `ValueError: PDF nie zawiera /XFA` – dokument nie jest XFA lub nie ma osadzonego XFA.
- `FileNotFoundError` – sprawdź poprawność ścieżki do pliku PDF.

## Licencja

Brak dodatkowej licencji – używaj w ramach projektu.