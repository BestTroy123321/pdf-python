import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pikepdf


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def to_str(obj: Any) -> str:
    return str(obj) if obj is not None else ""


def name_of(field: pikepdf.Object) -> str:
    t = field.get("/T")
    return to_str(t)


def is_widget(obj: pikepdf.Object) -> bool:
    subtype = obj.get("/Subtype")
    return to_str(subtype) == "/Widget"


def walk_fields(field: pikepdf.Object, parent_name: str = "") -> List[Dict[str, Any]]:
    """Zwraca listę liści pól (mających /FT) wraz z pełną nazwą i widgetami."""
    current_name = name_of(field)
    if parent_name and current_name:
        full_name = f"{parent_name}.{current_name}"
    else:
        full_name = current_name or parent_name

    entries: List[Dict[str, Any]] = []
    kids = field.get("/Kids")
    widgets: List[pikepdf.Object] = []
    child_fields: List[pikepdf.Object] = []

    if kids:
        for kid in kids:
            if is_widget(kid):
                widgets.append(kid)
            else:
                child_fields.append(kid)

    # Jeśli są dzieci będące polami, wędruj rekurencyjnie
    for child in child_fields:
        entries.extend(walk_fields(child, full_name))

    # Jeśli to liść (ma /FT), dodaj wpis
    ft = field.get("/FT")
    if ft is not None:
        entries.append({"name": full_name, "field": field, "widgets": widgets})

    return entries


def flatten_all_fields(acro_form: pikepdf.Object) -> List[Dict[str, Any]]:
    fields = acro_form.get("/Fields") or []
    flattened: List[Dict[str, Any]] = []
    for fld in fields:
        flattened.extend(walk_fields(fld, ""))
    return flattened


def get_appearance_names(widget: pikepdf.Object) -> Tuple[List[str], str]:
    """Zwraca (on_names, off_name) na podstawie /AP /N w widgetcie."""
    ap = widget.get("/AP")
    if not ap:
        return (["/Yes"], "/Off")
    normal = ap.get("/N")
    if not normal:
        return (["/Yes"], "/Off")
    names = [to_str(k) for k in normal.keys()]
    off_name = "/Off" if "/Off" in names else "/Off"
    on_names = [n for n in names if n != off_name]
    if not on_names:
        on_names = ["/Yes"]
    return (on_names, off_name)


def get_field_flags(field: pikepdf.Object) -> int:
    ff = field.get("/Ff")
    try:
        return int(ff) if ff is not None else 0
    except Exception:
        return 0


def is_push_button(ff: int) -> bool:
    # PushButton bit (per PDF spec): 1 << 16
    return bool(ff & (1 << 16))


def is_radio(ff: int) -> bool:
    # Radio bit: 1 << 15
    return bool(ff & (1 << 15))


def normalize_on_value(val: Any, candidates: List[str]) -> Optional[str]:
    if isinstance(val, bool):
        return candidates[0] if val else None
    if isinstance(val, (int, float)):
        return candidates[0] if val else None
    if isinstance(val, str):
        v = val.strip()
        if not v:
            return None
        # akceptuj nazwy z lub bez wiodącego '/'
        if v[0] != '/':
            v = '/' + v
        # dopasowanie do kandydatów
        for c in candidates:
            if v.lower() == c.lower():
                return c
        # heurystyka: 'Yes'/'On' -> pierwszy kandydat
        if v.lower() in ('/yes', '/on', '/true', '/1'):
            return candidates[0]
        # brak dokładnego dopasowania — wybierz pierwszy
        return candidates[0]
    return None


def set_text(field: pikepdf.Object, value: Any) -> None:
    field["/V"] = str(value) if value is not None else ""


def set_choice(field: pikepdf.Object, value: Any) -> None:
    # Dla ComboBox/ListBox – pojedyncza wartość jako string
    if isinstance(value, list):
        # jeśli lista, ustaw pierwszy
        if value:
            field["/V"] = str(value[0])
    else:
        field["/V"] = str(value) if value is not None else ""


def set_checkbox_or_radio(field: pikepdf.Object, widgets: List[pikepdf.Object], value: Any) -> None:
    ff = get_field_flags(field)
    if is_push_button(ff):
        return  # nic do ustawiania

    # zbierz kandydatów z widgetów
    all_on_names: List[str] = []
    off_name = "/Off"
    for w in widgets:
        on_names, off = get_appearance_names(w)
        off_name = off or off_name
        for n in on_names:
            if n not in all_on_names:
                all_on_names.append(n)

    selected_on = normalize_on_value(value, all_on_names) if all_on_names else None

    if is_radio(ff):
        # radio: ustaw wybraną wartość, inne widgety OFF
        if selected_on:
            field["/V"] = pikepdf.Name(selected_on)
            for w in widgets:
                on_names, off = get_appearance_names(w)
                if selected_on in on_names:
                    w["/AS"] = pikepdf.Name(selected_on)
                else:
                    w["/AS"] = pikepdf.Name(off)
        else:
            # brak wyboru – ustaw OFF
            field["/V"] = pikepdf.Name(off_name)
            for w in widgets:
                w["/AS"] = pikepdf.Name(off_name)
    else:
        # checkbox: True -> ON, False/None -> OFF
        if selected_on:
            field["/V"] = pikepdf.Name(selected_on)
            for w in widgets:
                on_names, off = get_appearance_names(w)
                # checkbox zwykle ma 1 on_name; użyj pierwszego
                on = on_names[0] if on_names else "/Yes"
                w["/AS"] = pikepdf.Name(on)
        else:
            field["/V"] = pikepdf.Name(off_name)
            for w in widgets:
                w["/AS"] = pikepdf.Name(off_name)


def fill_acroform_with_json(pdf_in: Path, json_in: Path, pdf_out: Path) -> None:
    data = load_json(json_in)
    with pikepdf.open(str(pdf_in)) as pdf:
        acro = pdf.Root.get("/AcroForm")
        if not acro:
            print("Brak AcroForm w PDF – pomiń lub użyj fill_xfa.py")
            return

        # Ustaw NeedAppearances, aby viewer wygenerował wygląd wartości
        acro["/NeedAppearances"] = True

        fields = flatten_all_fields(acro)
        by_name: Dict[str, Dict[str, Any]] = {f["name"]: f for f in fields if f["name"]}

        # dopasuj także po samym '/T', bez rodzica (skrótowe nazwy)
        short_names_map: Dict[str, List[Dict[str, Any]]] = {}
        for f in fields:
            base = name_of(f["field"]) or ""
            if base:
                short_names_map.setdefault(base, []).append(f)

        def apply_value_to_field(fentry: Dict[str, Any], value: Any) -> None:
            field_obj = fentry["field"]
            widgets = fentry["widgets"]
            ft = to_str(field_obj.get("/FT"))
            if ft == "/Tx":
                set_text(field_obj, value)
            elif ft == "/Ch":
                set_choice(field_obj, value)
            elif ft == "/Btn":
                set_checkbox_or_radio(field_obj, widgets, value)
            else:
                # inne typy np. /Sig – ignoruj
                pass

        for key, value in data.items():
            # 1) pełna nazwa pola
            fentry = by_name.get(key)
            if fentry:
                apply_value_to_field(fentry, value)
                continue
            # 2) skrótowe /T (jeśli unikalne)
            candidates = short_names_map.get(key, [])
            if len(candidates) == 1:
                apply_value_to_field(candidates[0], value)
                continue
            # 3) próba dopasowania bez rozróżniania wielkości liter
            lc_key = key.lower()
            # pełne
            match_full = next((f for n, f in by_name.items() if n.lower() == lc_key), None)
            if match_full:
                apply_value_to_field(match_full, value)
                continue
            # skrótowe
            match_short = None
            for base, flist in short_names_map.items():
                if base.lower() == lc_key and len(flist) == 1:
                    match_short = flist[0]
                    break
            if match_short:
                apply_value_to_field(match_short, value)
            else:
                # brak dopasowania – pomiń
                pass

        try:
            pdf.save(str(pdf_out))
            print(f"Zapisano: {pdf_out.name}")
        except PermissionError:
            alt = pdf_out.with_name(pdf_out.stem + "_alt" + pdf_out.suffix)
            pdf.save(str(alt))
            print(f"Plik zablokowany, zapisano alternatywnie: {alt.name}")


def main(argv: List[str]) -> None:
    pdf_in = Path(argv[1]) if len(argv) > 1 else Path("xfa.pdf")
    json_in = Path(argv[2]) if len(argv) > 2 else Path("dane.json")
    pdf_out = Path(argv[3]) if len(argv) > 3 else Path("wypelniony_acro.pdf")

    if not pdf_in.exists():
        print("BrakPlikuPDF")
        return
    if not json_in.exists():
        print("BrakPlikuJSON")
        return

    fill_acroform_with_json(pdf_in, json_in, pdf_out)


if __name__ == "__main__":
    main(sys.argv)