"""Extract the gii-norm XML payload from a gesetze-im-internet.de xml.zip."""

from __future__ import annotations

import io
import zipfile


def extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    """Return the bytes of the first .xml file inside ``zip_bytes``.

    Raises RuntimeError if the zip contains no .xml file.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            raise RuntimeError("Keine XML-Datei im ZIP gefunden.")
        with zf.open(xml_names[0]) as f:
            return f.read()
