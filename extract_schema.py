from __future__ import annotations

from pathlib import Path
import sys

from src.xfa_extract import extract_template_xml, extract_field_keys


def main() -> int:
    pdf_path = Path("xfa.pdf")
    if not pdf_path.exists():
        print("Błąd: Nie znaleziono pliku 'xfa.pdf' w katalogu głównym.")
        return 1

    try:
        xml_text = extract_template_xml(pdf_path, pretty=True)
    except Exception as exc:
        print(f"Błąd: {exc}")
        return 1

    out_path = Path("schemat.xml")
    out_path.write_text(xml_text, encoding="utf-8")
    print(f"Zapisano schemat pól do: {out_path}")

    # Drugi plik: tylko klucze pól do wypełnienia
    try:
        keys = extract_field_keys(pdf_path)
    except Exception as exc:
        print(f"Błąd podczas wyciągania kluczy pól: {exc}")
        keys = []

    keys_path = Path("pola.txt")
    keys_path.write_text("\n".join(keys), encoding="utf-8")
    print(f"Zapisano listę kluczy pól do: {keys_path} (liczba: {len(keys)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())