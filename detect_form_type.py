from __future__ import annotations

from pathlib import Path
import sys

import pikepdf


def _get_pdf_root(pdf: pikepdf.Pdf):
    root = getattr(pdf, "Root", None) or getattr(pdf, "root", None)
    if root is None:
        try:
            root = pdf.trailer["/Root"]
        except Exception:
            root = None
    return root


def detect_form_type(pdf_path: Path) -> str:
    if not pdf_path.exists():
        return "BrakPliku"
    try:
        with pikepdf.open(str(pdf_path)) as pdf:
            root = _get_pdf_root(pdf)
            if root is None:
                return "NiepoprawnyPDF"
            acro_form = root.get("/AcroForm", None)
            if acro_form is None:
                return "BrakFormularza"
            xfa = acro_form.get("/XFA", None)
            if xfa is not None:
                return "XFA"
            return "AcroForm"
    except Exception:
        return "BladOdczytu"


def main() -> int:
    pdf_path = Path("xfa.pdf")
    form_type = detect_form_type(pdf_path)
    # Wypisz wyłącznie nazwę
    print(form_type)
    return 0 if form_type not in {"BrakPliku", "NiepoprawnyPDF", "BladOdczytu"} else 1


if __name__ == "__main__":
    sys.exit(main())