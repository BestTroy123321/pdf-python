from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

import pikepdf
from lxml import etree

from src.xfa_extract import read_xfa_packets, get_bindings_from_template, get_som_paths_from_template


def _parse_xml(xml_bytes: bytes) -> etree._Element:
    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    return etree.fromstring(xml_bytes, parser)


def _ensure_datasets(root: etree._Element) -> etree._Element:
    """Zapewnij istnienie `<xfa:datasets>` z poprawną przestrzenią nazw XFA-data."""
    NS_XFA_DATA = "http://www.xfa.org/schema/xfa-data/1.0/"
    # Spróbuj znaleźć istniejące datasets (dowolny ns)
    datasets = root.find('.//{*}datasets')
    if datasets is None:
        # Utwórz z przestrzenią nazw XFA-data
        datasets = etree.Element(f"{{{NS_XFA_DATA}}}datasets", nsmap={"xfa": NS_XFA_DATA})
        root.append(datasets)
    return datasets


def _get_or_create_data_root(datasets: etree._Element) -> etree._Element:
    NS_XFA_DATA = datasets.nsmap.get('xfa', 'http://www.xfa.org/schema/xfa-data/1.0/')
    data = datasets.find('./{*}data')
    if data is None:
        data = etree.SubElement(datasets, f"{{{NS_XFA_DATA}}}data")
    return data


def _set_value_by_ref(data_root: etree._Element, ref: str, value: Any) -> None:
    # Proste odwzorowanie: ścieżka z `bind/@ref` rozdzielona po kropkach lub ukośnikach
    import re

    path_parts = [p for p in re.split(r"[./]", ref.strip()) if p]
    if not path_parts:
        return

    # Iteracyjnie twórz węzły
    current = data_root
    for idx, part in enumerate(path_parts):
        # Usuń indeksy [n]
        name = re.sub(r"\[.*?\]", "", part)
        child = current.find(f'./*[@name="{name}"]')  # mało wiarygodne dla XFA, fallback poniżej
        if child is None:
            # XFA datasets zwykle używa elementów bez atrybutu name; tworzymy tag z nazwą
            child = current.find(f'./{name}')
        if child is None:
            child = etree.SubElement(current, name)
        current = child

    # Ustaw wartość końcową
    if isinstance(value, (dict, list)):
        current.text = json.dumps(value, ensure_ascii=False)
    elif value is None:
        current.text = ""
    else:
        current.text = str(value)


def fill_xfa_with_json(pdf_in: Path, json_obj: Dict[str, Any], pdf_out: Path) -> None:
    packets = read_xfa_packets(pdf_in)
    if 'template' not in packets and 'xfa' not in packets:
        raise ValueError('PDF nie zawiera XFA – wymagany do wypełniania.')

    # Zbuduj mapę name->ref z template lub z pełnego XDP
    if 'template' in packets:
        bindings = get_bindings_from_template(packets['template'])
        som_paths = get_som_paths_from_template(packets['template'])
    else:
        bindings = get_bindings_from_template(packets['xfa'])
        som_paths = get_som_paths_from_template(packets['xfa'])

    # Przygotuj datasets do modyfikacji
    existing_datasets_bytes = packets.get('datasets')
    if existing_datasets_bytes:
        datasets_root = _parse_xml(existing_datasets_bytes)
    else:
        # Utwórz pusty datasets
        datasets_root = etree.Element('datasets')
    data_root = _get_or_create_data_root(datasets_root)

    # Mapowanie kluczy JSON do bind/@ref: próbujemy dopasować po:
    # 1) bezpośrednim kluczu == ref
    # 2) kluczu odpowiadającemu nazwie pola
    # 3) w przeciwnym razie traktuj klucz jako ścieżkę
    for field_name, ref in bindings.items():
        if ref in json_obj:
            _set_value_by_ref(data_root, ref, json_obj[ref])
    for field_name, ref in bindings.items():
        if field_name in json_obj:
            _set_value_by_ref(data_root, ref, json_obj[field_name])
    # 2b) Jeśli mamy ścieżkę SOM dla nazwy pola, użyj jej (często bez bind)
    for field_name, som in som_paths.items():
        if field_name in json_obj:
            _set_value_by_ref(data_root, som, json_obj[field_name])
    for k, v in json_obj.items():
        if k not in bindings and k not in bindings.values():
            _set_value_by_ref(data_root, k, v)

    datasets_bytes = etree.tostring(datasets_root, xml_declaration=False, encoding='utf-8')

    # Zapisz do PDF – zaktualizuj tylko strumień 'datasets' (dla XFA array)
    with pikepdf.open(str(pdf_in)) as pdf:
        root = getattr(pdf, 'root', None) or getattr(pdf, 'Root', None) or pdf.trailer['/Root']
        acro = root['/AcroForm']
        xfa = acro['/XFA']

        if isinstance(xfa, pikepdf.Array):
            replaced = False
            for i in range(0, len(xfa) - 1, 2):
                key = str(xfa[i]).lstrip('/')
                if key == 'datasets':
                    stream = xfa[i + 1]
                    try:
                        stream.set_bytes(datasets_bytes)
                    except Exception:
                        xfa[i + 1] = pikepdf.Stream(pdf, datasets_bytes)
                    replaced = True
                    break
            if not replaced:
                xfa.append(pikepdf.Name('/datasets'))
                xfa.append(pikepdf.Stream(pdf, datasets_bytes))
        else:
            # pojedynczy strumień XFA – musimy zaktualizować pełny XDP
            xdp_root = _parse_xml(packets['xfa'])
            xdp_datasets = _ensure_datasets(xdp_root)
            # wymień datasets na nasz
            parent = xdp_datasets.getparent()
            if parent is not None:
                parent.replace(xdp_datasets, datasets_root)
            updated_xdp = etree.tostring(xdp_root, xml_declaration=True, encoding='utf-8')
            try:
                xfa.set_bytes(updated_xdp)  # type: ignore[attr-defined]
            except Exception:
                acro['/XFA'] = pikepdf.Stream(pdf, updated_xdp)

        pdf.save(str(pdf_out))


def main() -> int:
    pdf_in = Path('xfa.pdf')
    out_pdf = Path('wypelniony.pdf')
    json_path = Path('dane.json')

    if not pdf_in.exists():
        print("Błąd: Brak pliku xfa.pdf w katalogu głównym.")
        return 1
    if not json_path.exists():
        print("Błąd: Brak pliku dane.json w katalogu głównym.")
        return 1

    try:
        json_obj = json.loads(json_path.read_text(encoding='utf-8'))
        if not isinstance(json_obj, dict):
            print('Błąd: JSON musi być obiektem na poziomie głównym.')
            return 1
    except Exception as exc:
        print(f'Błąd odczytu JSON: {exc}')
        return 1

    try:
        fill_xfa_with_json(pdf_in, json_obj, out_pdf)
    except PermissionError:
        alt = Path('wypelniony_alt.pdf')
        try:
            fill_xfa_with_json(pdf_in, json_obj, alt)
            print('Uwaga: plik wypelniony.pdf jest otwarty/zablokowany. Zapisano do: wypelniony_alt.pdf')
            return 0
        except Exception as exc:
            print(f'Błąd wypełniania XFA (fallback): {exc}')
            return 1
    except Exception as exc:
        print(f'Błąd wypełniania XFA: {exc}')
        return 1

    print(f'Zapisano wypełniony PDF: {out_pdf}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())