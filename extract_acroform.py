import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pikepdf
from lxml import etree


def to_str(obj: Any) -> str:
    return str(obj) if obj is not None else ""


def is_widget(obj: pikepdf.Object) -> bool:
    subtype = obj.get("/Subtype")
    return to_str(subtype) == "/Widget"


def name_of(field: pikepdf.Object) -> str:
    return to_str(field.get("/T"))


def get_field_flags(field: pikepdf.Object) -> int:
    ff = field.get("/Ff")
    try:
        return int(ff) if ff is not None else 0
    except Exception:
        return 0


def is_read_only(ff: int) -> bool:
    # ReadOnly bit: 1 << 0
    return bool(ff & (1 << 0))


def is_push_button(ff: int) -> bool:
    # PushButton bit: 1 << 16
    return bool(ff & (1 << 16))


def is_radio(ff: int) -> bool:
    # Radio bit: 1 << 15
    return bool(ff & (1 << 15))


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

    for child in child_fields:
        entries.extend(walk_fields(child, full_name))

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


def get_choice_options(field: pikepdf.Object) -> List[str]:
    """Pobiera listę opcji z /Opt (dla /Ch). Obsługuje postać stringów i par [export, display]."""
    opt = field.get("/Opt")
    options: List[str] = []
    if opt:
        for item in opt:
            try:
                if isinstance(item, pikepdf.String):
                    options.append(str(item))
                elif isinstance(item, pikepdf.Array) and len(item) >= 1:
                    options.append(str(item[0]))
                else:
                    options.append(to_str(item))
            except Exception:
                options.append(to_str(item))
    return options


def get_appearance_names(widget: pikepdf.Object) -> Tuple[List[str], str]:
    """Zwraca listę nazw ON i nazwę OFF z /AP /N."""
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


def build_schema_xml(acro_form: pikepdf.Object) -> etree._Element:
    """Buduje XML schematu AcroForm (hierarchia pól, typy, flagi, opcje)."""
    root = etree.Element("acroForm")

    def emit_field(node_parent: etree._Element, field: pikepdf.Object, parent_name: str = ""):
        current_name = name_of(field)
        full_name = f"{parent_name}.{current_name}" if parent_name and current_name else (current_name or parent_name)
        ft = to_str(field.get("/FT"))
        ff = get_field_flags(field)
        readonly = is_read_only(ff)
        push = is_push_button(ff)
        radio = is_radio(ff)

        node = etree.SubElement(node_parent, "field")
        node.set("name", full_name or "")
        node.set("type", ft.replace("/", "") if ft else "")
        node.set("flags", str(ff))
        node.set("readonly", "true" if readonly else "false")

        if ft == "/Ch":
            opts = get_choice_options(field)
            if opts:
                opts_node = etree.SubElement(node, "options")
                for o in opts:
                    item = etree.SubElement(opts_node, "option")
                    item.text = o

        if ft == "/Btn":
            node.set("pushbutton", "true" if push else "false")
            node.set("radio", "true" if radio else "false")
            # zbierz nazwy ON z widgetów
            kids = field.get("/Kids") or []
            on_values: List[str] = []
            for kid in kids:
                if is_widget(kid):
                    on_names, _ = get_appearance_names(kid)
                    for n in on_names:
                        if n not in on_values:
                            on_values.append(n)
            if on_values:
                ons_node = etree.SubElement(node, "exports")
                for n in on_values:
                    item = etree.SubElement(ons_node, "value")
                    item.text = n

        # dzieci będące polami
        kids = field.get("/Kids") or []
        for kid in kids:
            if not is_widget(kid):
                emit_field(node, kid, full_name)

    fields = acro_form.get("/Fields") or []
    for fld in fields:
        emit_field(root, fld, "")

    return root


def write_xml(file_path: Path, root: etree._Element) -> None:
    data = etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)
    file_path.write_bytes(data)


def write_fillable_list(file_path: Path, fields: List[Dict[str, Any]]) -> None:
    lines: List[str] = []
    for f in fields:
        full_name = f.get("name") or ""
        field_obj = f["field"]
        ft = to_str(field_obj.get("/FT"))
        ff = get_field_flags(field_obj)
        if not full_name:
            continue
        if is_read_only(ff):
            continue
        if ft == "/Btn" and is_push_button(ff):
            continue
        # opcjonalnie pomijamy pola podpisu
        if ft == "/Sig":
            continue
        lines.append(full_name)
    content = ("\n".join(lines) + "\n") if lines else ""
    file_path.write_text(content, encoding="utf-8")


def extract_acroform(pdf_path: Path, xml_out: Path, keys_out: Path) -> None:
    with pikepdf.open(str(pdf_path)) as pdf:
        acro = pdf.Root.get("/AcroForm")
        if not acro:
            print("Brak AcroForm w PDF")
            return
        # schemat
        root = build_schema_xml(acro)
        write_xml(xml_out, root)
        # lista pól do wypełnienia
        flat = flatten_all_fields(acro)
        write_fillable_list(keys_out, flat)
        print(f"Zapisano: {xml_out.name}, {keys_out.name}")


def main(argv: List[str]) -> None:
    pdf_in = Path(argv[1]) if len(argv) > 1 else Path("xfa.pdf")
    xml_out = Path(argv[2]) if len(argv) > 2 else Path("schemat_acro.xml")
    keys_out = Path(argv[3]) if len(argv) > 3 else Path("pola_acro.txt")

    if not pdf_in.exists():
        print("BrakPlikuPDF")
        return

    extract_acroform(pdf_in, xml_out, keys_out)


if __name__ == "__main__":
    main(sys.argv)