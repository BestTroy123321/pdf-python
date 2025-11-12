from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import pikepdf
from lxml import etree


def _get_pdf_root(pdf: pikepdf.Pdf):
    """Zwróć katalog główny PDF (/Root) w sposób kompatybilny między wersjami pikepdf."""
    # Preferuj właściwość z dużej litery, następnie z małej, na końcu trailer
    root = getattr(pdf, "Root", None) or getattr(pdf, "root", None)
    if root is None:
        try:
            root = pdf.trailer["/Root"]
        except Exception:
            root = None
    if root is None:
        raise ValueError("PDF nie zawiera katalogu /Root – niepoprawny dokument.")
    return root


def _obj_to_bytes(obj) -> bytes:
    """Zwróć dane obiektu XFA jako bytes.

    Obsługuje zarówno strumienie (Stream) jak i obiekty tekstowe (String/Name -> str).
    """
    if hasattr(obj, "read_bytes"):
        try:
            return obj.read_bytes()  # type: ignore[attr-defined]
        except Exception:
            pass
    # Fallback dla String/Name – konwersja do tekstu
    s = str(obj)
    return s.encode("utf-8", errors="ignore")


def read_xfa_packets(pdf_path: str | Path) -> Dict[str, bytes]:
    """Odczytaj pakiety XFA z pliku PDF.

    Zwraca słownik: nazwa_pakietu -> bytes (surowe XML/XDP).

    Obsługiwane przypadki:
    - /AcroForm posiada /XFA jako tablicę naprzemiennie: nazwa, strumień
    - /AcroForm posiada /XFA jako pojedynczy strumień (bez nazw pakietów)

    :param pdf_path: Ścieżka do pliku PDF (XFA)
    :raises ValueError: gdy PDF nie zawiera XFA
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {pdf_path}")

    packets: Dict[str, bytes] = {}
    with pikepdf.open(str(pdf_path)) as pdf:
        root = _get_pdf_root(pdf)
        acro_form = root.get("/AcroForm", None)
        if acro_form is None:
            raise ValueError("PDF nie zawiera /AcroForm – brak XFA.")

        xfa = acro_form.get("/XFA", None)
        if xfa is None:
            raise ValueError("PDF nie zawiera /XFA – brak pakietów XFA.")

        # Gdy XFA jest tablicą: [name, stream, name, stream, ...]
        if isinstance(xfa, pikepdf.Array):
            # iteruj parami: (nazwa, strumień)
            items = list(xfa)
            # W niektórych plikach może wystąpić nieparzysta długość – zabezpieczenie.
            for i in range(0, len(items) - 1, 2):
                name_obj, stream_obj = items[i], items[i + 1]
                # Nazwa pakietu jest zwykle typu Name, np. "/template" – usuń wiodące '/'
                name = str(name_obj).lstrip("/")
                data = _obj_to_bytes(stream_obj)
                packets[name] = data
        else:
            # Gdy XFA jest pojedynczym strumieniem – brak nazw. Nadaj domyślną.
            data = _obj_to_bytes(xfa)
            packets["xfa"] = data

    if not packets:
        raise ValueError("Nie udało się odczytać żadnych pakietów XFA.")

    return packets


def choose_packet(
    packets: Dict[str, bytes], preferred: Optional[str] = None
) -> Tuple[str, bytes]:
    """Wybierz pakiet do zwrotu.

    Preferowany porządek:
    1) nazwa `preferred` jeżeli istnieje
    2) `template` (struktura pól)
    3) `datasets` (wartości danych)
    4) pierwszy dostępny pakiet
    """
    if preferred and preferred in packets:
        return preferred, packets[preferred]

    for candidate in ("template", "datasets"):
        if candidate in packets:
            return candidate, packets[candidate]

    # fallback – weź pierwszy
    name = next(iter(packets.keys()))
    return name, packets[name]


def bytes_to_pretty_xml(xml_bytes: bytes) -> Optional[str]:
    """Spróbuj sparsować i sformatować XML do czytelnej postaci.

    Zwraca tekst XML (unicode) lub None jeśli parser nie powiedzie się.
    """
    try:
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        root = etree.fromstring(xml_bytes, parser)
        return etree.tostring(root, pretty_print=True, encoding="unicode")
    except Exception:
        return None


def save_packet(
    xml_bytes: bytes, output_path: str | Path, pretty: bool = False
) -> Path:
    """Zapisz pakiet XFA do pliku.

    Jeżeli `pretty=True`, spróbuj zapisać ładnie sformatowany XML, inaczej surowe bytes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if pretty:
        pretty_xml = bytes_to_pretty_xml(xml_bytes)
        if pretty_xml is not None:
            output_path.write_text(pretty_xml, encoding="utf-8")
            return output_path

    # fallback: zapisz surowe bajty (oryginalne kodowanie pozostaje)
    output_path.write_bytes(xml_bytes)
    return output_path


def extract_xfa_xml(
    pdf_path: str | Path,
    packet: Optional[str] = None,
    pretty: bool = False,
) -> Tuple[str, str]:
    """Ekstraktuj wskazany pakiet XFA i zwróć (nazwa, xml_text).

    Jeżeli `pretty=False`, zwrócony tekst może nie być sformatowany.
    """
    packets = read_xfa_packets(pdf_path)
    name, xml_bytes = choose_packet(packets, preferred=packet)
    pretty_xml = bytes_to_pretty_xml(xml_bytes) if pretty else None
    if pretty_xml is None:
        # Spróbuj zdekodować jako utf-8, w razie błędu użyj latin-1 (bezpieczny fallback)
        try:
            xml_text = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                xml_text = xml_bytes.decode("utf-16")
            except UnicodeDecodeError:
                xml_text = xml_bytes.decode("latin-1")
    else:
        xml_text = pretty_xml
    return name, xml_text


def extract_template_from_xdp_bytes(xml_bytes: bytes) -> Optional[str]:
    """Wyodrębnij tylko element `<template>` z pełnego XDP/XML.

    Ignoruje przestrzenie nazw, szuka dowolnego elementu o nazwie `template`.
    Zwraca sformatowany XML (unicode) lub None, jeśli nie znaleziono.
    """
    try:
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        root = etree.fromstring(xml_bytes, parser)
        # Wyszukiwanie niezależne od namespace: {namespace}template
        template_el = root.find('.//{*}template')
        if template_el is None:
            return None
        return etree.tostring(template_el, pretty_print=True, encoding="unicode")
    except Exception:
        return None


def extract_template_xml(pdf_path: str | Path, pretty: bool = True) -> str:
    """Zwróć tylko strukturę pól formularza (pakiet `template`).

    - Jeśli PDF zawiera osobny pakiet `template`, użyj go.
    - Jeśli nie, spróbuj znaleźć `<template>` wewnątrz pełnego XDP.
    - W przypadku niepowodzenia zgłoś szczegółowy błąd.
    """
    packets = read_xfa_packets(pdf_path)

    # 1) Osobny pakiet `template`
    if "template" in packets:
        xml_bytes = packets["template"]
        text = bytes_to_pretty_xml(xml_bytes) if pretty else None
        if text is None:
            try:
                text = xml_bytes.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = xml_bytes.decode("utf-16")
                except UnicodeDecodeError:
                    text = xml_bytes.decode("latin-1")
        return text

    # 2) Brak pakietu – spróbuj odnaleźć `<template>` w XDP
    # Preferuj pełny strumień XFA jeśli dostępny
    xml_source_name, xml_source_bytes = (
        ("xfa", packets.get("xfa")) if packets.get("xfa") is not None else choose_packet(packets)
    )

    extracted = extract_template_from_xdp_bytes(xml_source_bytes)
    if extracted is None:
        raise ValueError(
            "Nie znaleziono elementu <template> w strumieniu XFA (pakiet: "
            f"{xml_source_name}). Dokument może nie zawierać szablonu pól."
        )
    return extracted


# --- Ekstrakcja nazw pól ---

def _sanitize_ref_name(ref: str) -> Optional[str]:
    """Wyczyść ścieżkę ref z XFA (bind/@ref) do nazwy pola.

    Pobiera ostatni segment ścieżki, usuwa indeksy typu [0].
    """
    if not ref:
        return None
    s = ref.strip()
    # Usuń preambule typu '$.'
    if s.startswith("$."):
        s = s[2:]
    # Rozbij po kropce lub ukośniku
    import re

    parts = [p for p in re.split(r"[./]", s) if p]
    last = parts[-1] if parts else s
    # Usuń indeksy [n]
    last = re.sub(r"\[.*?\]", "", last)
    return last or None


def _extract_field_names_from_template_xml(xml_bytes: bytes) -> set[str]:
    """Zwróć zestaw nazw pól z XFA `<template>`.

    Zbiera `field@name`, `exclGroup@name` oraz ostatni segment z `bind/@ref`.
    """
    names: set[str] = set()
    try:
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        root = etree.fromstring(xml_bytes, parser)
        for el in root.findall('.//{*}field'):
            name = el.get('name')
            if name:
                names.add(name)
        for el in root.findall('.//{*}exclGroup'):
            name = el.get('name')
            if name:
                names.add(name)
        for el in root.findall('.//{*}bind[@ref]'):
            ref = el.get('ref')
            nm = _sanitize_ref_name(ref) if ref else None
            if nm:
                names.add(nm)
    except Exception:
        pass
    return names


def _extract_acroform_field_names(pdf_path: Path) -> set[str]:
    """Zwróć nazwy pól z klasycznego AcroForm (`/Fields`)."""
    names: set[str] = set()
    with pikepdf.open(str(pdf_path)) as pdf:
        root = _get_pdf_root(pdf)
        acro_form = root.get("/AcroForm", None)
        if acro_form is None:
            return names
        fields = acro_form.get("/Fields", pikepdf.Array())

        def is_readonly(field_dict) -> bool:
            try:
                ff = field_dict.get("/Ff", 0)
                ff_int = int(ff) if ff is not None else 0
                return (ff_int & 1) == 1
            except Exception:
                return False

        def walk(field_obj, parent_parts: list[str]):
            # field_obj powinien być słownikiem
            name_part = field_obj.get("/T", None)
            name_part = str(name_part) if name_part is not None else None
            parts = parent_parts + ([name_part] if name_part else [])

            kids = field_obj.get("/Kids", None)
            ft = field_obj.get("/FT", None)

            # Dodaj, jeśli to pole (ma typ) i nie jest tylko kontenerem
            if ft is not None and parts:
                if not is_readonly(field_obj):
                    names.add(".".join(parts))

            # Rekurencja w dół jeśli są dzieci
            if isinstance(kids, pikepdf.Array):
                for k in kids:
                    try:
                        walk(k, parts)
                    except Exception:
                        continue

        if isinstance(fields, pikepdf.Array):
            for f in fields:
                try:
                    walk(f, [])
                except Exception:
                    continue

    return names


def extract_field_keys(pdf_path: str | Path) -> list[str]:
    """Zwróć listę kluczy pól do wypełnienia (XFA lub AcroForm)."""
    pdf_path = Path(pdf_path)

    # Spróbuj XFA/template
    packets = read_xfa_packets(pdf_path)
    names: set[str] = set()

    if "template" in packets:
        names |= _extract_field_names_from_template_xml(packets["template"])
    else:
        # Brak osobnego pakietu – użyj pełnego XFA/XDP jeśli dostępny
        xfa_bytes = packets.get("xfa")
        if xfa_bytes:
            names |= _extract_field_names_from_template_xml(xfa_bytes)

    # Jeżeli z XFA nic nie znaleziono, spróbuj AcroForm
    if not names:
        names |= _extract_acroform_field_names(pdf_path)

    return sorted(names)


def get_bindings_from_template(xml_bytes: bytes) -> Dict[str, str]:
    """Zbuduj mapę: nazwa_pola -> bind/@ref (surowa ścieżka).

    Szuka elementów `<field name>` oraz ich potomków `<bind ref>`. Jeśli `bind` nie
    występuje, pole jest pomijane. Nie sanitizujemy ścieżki – zachowujemy oryginał,
    aby lepiej pasował do danych XDP.
    """
    out: Dict[str, str] = {}
    try:
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        root = etree.fromstring(xml_bytes, parser)
        for el in root.findall('.//{*}field'):
            name = el.get('name')
            if not name:
                continue
            bind = el.find('.//{*}bind')
            if bind is None:
                continue
            ref = bind.get('ref')
            if ref:
                out[name] = ref
        # Grupy wykluczające mogą także posiadać bind
        for el in root.findall('.//{*}exclGroup'):
            name = el.get('name')
            if not name:
                continue
            bind = el.find('.//{*}bind')
            if bind is None:
                continue
            ref = bind.get('ref')
            if ref:
                out[name] = ref
    except Exception:
        pass
    return out


def get_som_paths_from_template(xml_bytes: bytes) -> Dict[str, str]:
    """Zbuduj mapę: nazwa_pola -> pełna ścieżka SOM (np. form1.SUB1.SUB102.Nazwisko).

    Ścieżka SOM powstaje z nazw kolejnych `<subform name>` zakończona `field@name`.
    Dla pól powtarzalnych zwracamy pierwsze znalezione wystąpienie.
    """
    result: Dict[str, str] = {}
    try:
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        root = etree.fromstring(xml_bytes, parser)

        def local_name(el: etree._Element) -> str:
            tag = el.tag
            if isinstance(tag, str) and tag.startswith('{'):
                return tag.split('}', 1)[1]
            return tag if isinstance(tag, str) else ''

        for field in root.findall('.//{*}field[@name]'):
            field_name = field.get('name')
            if not field_name:
                continue
            # Zbierz nazwy subformów w górę drzewa
            names = [field_name]
            p = field.getparent()
            while p is not None:
                ln = local_name(p)
                if ln == 'subform':
                    n = p.get('name')
                    if n:
                        names.append(n)
                p = p.getparent()
            som_path = '.'.join(reversed(names))
            # Zapisz tylko pierwsze wystąpienie
            result.setdefault(field_name, som_path)
    except Exception:
        pass
    return result


def _derive_output_path(
    pdf_path: Path, out_dir: Path, packet_name: str
) -> Path:
    base = pdf_path.stem
    filename = f"{base}.{packet_name}.xml"
    return out_dir / filename


def main() -> None:
    """CLI: Ekstrakcja XFA XML z PDF.

    Przykłady:
    - python -m src.xfa_extract input.pdf --out out/ --packet template --pretty
    - python -m src.xfa_extract input.pdf --print
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Ekstrakcja XML (XFA) z plików PDF XFA",
    )
    parser.add_argument("pdf", help="Ścieżka do pliku PDF (XFA)")
    parser.add_argument(
        "--out",
        dest="out_dir",
        default="out",
        help="Katalog wyjściowy do zapisu XML (domyślnie: out)",
    )
    parser.add_argument(
        "--packet",
        dest="packet",
        default=None,
        help="Nazwa pakietu XFA do wyciągnięcia (np. template, datasets)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Formatuj XML do czytelnej postaci",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Wypisz XML na stdout zamiast zapisywać do pliku",
    )

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    packet_name, xml_text = extract_xfa_xml(pdf_path, packet=args.packet, pretty=args.pretty)

    if args.do_print:
        print(xml_text)
        return

    out_dir = Path(args.out_dir)
    output_path = _derive_output_path(pdf_path, out_dir, packet_name)
    save_packet(xml_text.encode("utf-8"), output_path, pretty=False)
    print(f"Zapisano XML pakietu '{packet_name}' do: {output_path}")


if __name__ == "__main__":
    main()