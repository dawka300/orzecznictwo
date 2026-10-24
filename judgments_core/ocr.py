"""Text of scanned documents (OCR): a PDF page without a text layer or a photo of a page.

Local programs, nothing leaves the computer: Tesseract (tesseract-ocr with the Polish and
English data) reads the image, pdftoppm (poppler-utils) turns PDF pages into images. Both
are in the www image (docker/www.Dockerfile); without them (the hosting) the pages say so.
Print reads well; handwriting, stamps and crooked phone photos do not - check dates,
docket numbers and amounts by eye.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

LANGUAGES = "pol+eng"
DPI = 300  # mniej = szybciej, ale gorzej przy drobnym druku
MAX_PAGES = 60  # tyle stron skanu najwyżej (~3-5 s na stronę na CPU)
TIMEOUT_S = 120  # na jedną stronę
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


class OcrError(RuntimeError):
    """OCR can't run or failed; the message is for the user."""


def available():
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def _require():
    if not available():
        raise OcrError(
            "brak OCR (tesseract, pdftoppm) - lokalnie: docker compose build www;"
            " albo skopiuj tekst i wklej go w polu niżej"
        )


def _tesseract(image_path):
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", LANGUAGES],
            capture_output=True,
            timeout=TIMEOUT_S,
            check=True,
        )
    except subprocess.TimeoutExpired as error:
        raise OcrError("OCR trwał za długo (strona)") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise OcrError(f"OCR nie odczytał obrazu ({detail[0][:120]})") from error
    return result.stdout.decode("utf-8", "replace")


def image_text(data, suffix=".png"):
    """Text of one image (a photo or a scan of a page)."""
    _require()
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        path = Path(tmp) / f"strona{suffix}"
        path.write_bytes(data)
        return _tesseract(path)


def pdf_pages_text(data, pages):
    """{page number (1-based): text} for the given pages of a PDF, read by OCR."""
    _require()
    pages = sorted(pages)[:MAX_PAGES]
    out = {}
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        pdf = Path(tmp) / "dokument.pdf"
        pdf.write_bytes(data)
        for page in pages:
            prefix = Path(tmp) / f"s{page}"
            try:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        str(DPI),
                        "-gray",
                        "-png",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        str(pdf),
                        str(prefix),
                    ],
                    capture_output=True,
                    timeout=TIMEOUT_S,
                    check=True,
                )
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
                raise OcrError(f"nie można zamienić strony {page} PDF na obraz") from error
            images = sorted(Path(tmp).glob(f"s{page}*.png"))
            out[page] = "\n".join(_tesseract(image) for image in images)
            for image in images:
                image.unlink()
    return out
