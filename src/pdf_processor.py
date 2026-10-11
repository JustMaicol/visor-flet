import concurrent.futures
import os
import re
import shutil
import threading
from dataclasses import dataclass

import pymupdf as fitz

EXCLUDE_KEYWORDS = {
    "SERIE", "NO", "NRO", "NUMERO", "PAGINA", "HOJA", "FECHA", "CODIGO",
    "USUARIO", "CLIENTE", "ID", "FACTURA", "TELEFONO", "TOTAL", "SUBTOTAL",
    "IVA", "EMISION", "VENCIMIENTO", "RUT", "NIT", "RFC", "CUIT",
    "FRENTE", "REVERSO", "ORIGINAL", "COPIA", "INFORME", "MENSUAL",
    "ACTIVIDADES", "REPUBLICA", "NICARAGUA", "REGISTRO", "ESTADO"
}

LETTERS_PATTERN = re.compile(r"\b[A-Z]{3,15}\b")
NUMBERS_PATTERN = re.compile(r"\b\d{4,15}\b")

DEFAULT_MAX_WORKERS = min(8, (os.cpu_count() or 4) + 2)


def extract_codes_from_text(text: str) -> tuple[str | None, str | None]:
    """
    Analyze the text of a region and try to extract the letter code and the numeric code.
    1. Both appearing on the same line of text (takes priority).
    2. Regional extraction of independent candidates (fallback for multi-line formats).
    """
    text_clean = text.strip()
    if not text_clean:
        return None, None

    lines = [line.strip() for line in text_clean.split("\n") if line.strip()]

    for line in lines:
        letters = [w for w in LETTERS_PATTERN.findall(line) if w not in EXCLUDE_KEYWORDS]
        numbers = NUMBERS_PATTERN.findall(line)
        if letters and numbers:
            return letters[0], numbers[0]

    letter_candidates = [w for w in LETTERS_PATTERN.findall(text_clean) if w not in EXCLUDE_KEYWORDS]
    number_candidates = NUMBERS_PATTERN.findall(text_clean)

    if letter_candidates and number_candidates:
        return letter_candidates[0], number_candidates[0]

    return None, None


@dataclass
class SearchCriterion:
    letters: str
    numbers: str
    label: str

    def matches(self, det_letters: str | None, det_numbers: str | None) -> bool:
        match_letters = True
        match_numbers = True
        if self.letters:
            match_letters = bool(det_letters) and self.letters in det_letters
        if self.numbers:
            match_numbers = bool(det_numbers) and self.numbers in det_numbers
        return match_letters and match_numbers


def _build_criteria(search_letters, search_numbers, search_pairs) -> list[SearchCriterion]:
    if search_pairs:
        return [
            SearchCriterion(sl.strip().upper(), sn.strip(), label)
            for sl, sn, label in search_pairs
        ]

    s_letters = (search_letters or "").strip().upper()
    s_numbers = (search_numbers or "").strip()
    if s_letters and s_numbers:
        label = f"{s_letters} + {s_numbers}"
    elif s_letters:
        label = s_letters
    elif s_numbers:
        label = s_numbers
    else:
        label = "Todo"
    return [SearchCriterion(s_letters, s_numbers, label)]


def _find_pdf_files(directory: str) -> list[str]:
    pdf_files = []
    for root, _dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, file))
    return pdf_files


def list_pdf_files(directory: str) -> list[str]:
    """List the paths of the PDFs managed by the app, sorted by name."""
    return sorted(_find_pdf_files(directory), key=lambda p: os.path.basename(p).lower())


def _search_single_pdf(
    filepath: str,
    rect: "fitz.Rect",
    criteria: list[SearchCriterion],
    result_callback,
    cancel_event,
    error_callback,
) -> None:
    """Analyze the odd pages (fronts) of a single PDF against all active criteria."""
    try:
        with fitz.open(filepath) as doc:
            for page_idx in range(0, doc.page_count, 2):
                if cancel_event and cancel_event.is_set():
                    return

                text = doc[page_idx].get_text("text", clip=rect)
                det_letters, det_numbers = extract_codes_from_text(text)
                if not det_letters and not det_numbers:
                    continue

                for criterion in criteria:
                    if criterion.matches(det_letters, det_numbers):
                        if result_callback:
                            result_callback(
                                filepath=filepath,
                                page_num=page_idx + 1,
                                detected_letters=det_letters or "N/D",
                                detected_numbers=det_numbers or "N/D",
                                criterio=criterion.label,
                            )
                        # A file/page already matched to a criterion does not need to check the rest.
                        break
    except Exception as e:
        if error_callback:
            error_callback(filepath, str(e))


def search_pdfs_in_directory(
    directory,
    search_letters="",
    search_numbers="",
    crop_coords=(0, 0, 250, 150),
    progress_callback=None,
    result_callback=None,
    cancel_event=None,
    search_pairs=None,
    error_callback=None,
    max_workers=DEFAULT_MAX_WORKERS,
):
    """
    Search for PDF files in the directory (recursively), analyzing only the odd pages
    (fronts) of each one. Processes several PDFs in parallel with a thread pool, since each
    file is independent and most of the time is spent on disk I/O / MuPDF.

    Supports two modes:
    - Single search: uses search_letters and search_numbers (if search_pairs is empty).
    - Batch search: uses search_pairs = [(letters, numbers, criterion_label), ...].
      Each page is checked against all criteria; the first match is reported.

    crop_coords: tuple (x0, y0, width, height)
    progress_callback: function(completed_files, total_files, file_name)
    result_callback: function(filepath, page_num, detected_letters, detected_numbers, criterion)
    error_callback: function(filepath, message) for files that could not be processed
    cancel_event: threading.Event to cancel the search
    """
    x0, y0, width, height = crop_coords
    rect = fitz.Rect(x0, y0, x0 + width, y0 + height)

    try:
        pdf_files = _find_pdf_files(directory)
    except OSError as e:
        if error_callback:
            error_callback(directory, f"No se pudo escanear el directorio: {e}")
        return

    total_files = len(pdf_files)
    if total_files == 0:
        return

    criteria = _build_criteria(search_letters, search_numbers, search_pairs)

    completed = 0
    progress_lock = threading.Lock()

    def process(filepath: str) -> None:
        nonlocal completed
        if cancel_event and cancel_event.is_set():
            return

        _search_single_pdf(filepath, rect, criteria, result_callback, cancel_event, error_callback)

        with progress_lock:
            completed += 1
            current = completed
        if progress_callback:
            progress_callback(current, total_files, os.path.basename(filepath))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process, filepath) for filepath in pdf_files]
        # Each task checks cancel_event on start, so a cancellation propagates
        # quickly without needing to cancel futures that are already queued.
        for future in concurrent.futures.as_completed(futures):
            future.result()


def compile_pdf_from_results(selected_items, output_filepath, error_callback=None) -> int:
    """
    Compile a consolidated PDF from the selected sheets. Each selected item is an
    odd page (front); it is included together with the next even page (the back),
    e.g. if the chosen page is 17, pages 17 and 18 are included. If there is no
    next page, only the selected one is included.

    Files/pages that fail are reported via error_callback and skipped, instead of
    aborting the whole compilation.

    selected_items: list of dicts [{"filepath": str, "page_num": int}]
    output_filepath: destination path of the consolidated PDF
    Returns the number of items successfully included.
    """
    included = 0

    with fitz.open() as output_doc:
        for item in selected_items:
            filepath = item["filepath"]
            page_num = item["page_num"]

            if not os.path.exists(filepath):
                if error_callback:
                    error_callback(filepath, "El archivo ya no existe en el disco.")
                continue

            try:
                with fitz.open(filepath) as src_doc:
                    page_idx = page_num - 1
                    if page_idx < 0 or page_idx >= src_doc.page_count:
                        if error_callback:
                            error_callback(filepath, f"La página {page_num} no existe en el archivo.")
                        continue

                    to_page_idx = page_idx + 1 if page_idx + 1 < src_doc.page_count else page_idx
                    output_doc.insert_pdf(src_doc, from_page=page_idx, to_page=to_page_idx)
                    included += 1
            except Exception as e:
                if error_callback:
                    error_callback(filepath, str(e))
                continue

        if included == 0:
            raise ValueError("Ninguna de las páginas seleccionadas pudo incluirse en el PDF.")

        output_doc.save(output_filepath)

    return included


def _unique_destination_path(storage_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(storage_dir, filename)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(storage_dir, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def import_pdf_to_storage(source_path: str, storage_dir: str) -> tuple[str | None, str | None]:
    """
    Copy an external PDF into the app's own storage folder (used by the UI's
    "Load PDF" button), without overwriting existing files with the same name.

    Returns (destination_path, None) on success, or (None, error_message) readable by the UI.
    """
    if not os.path.isfile(source_path):
        return None, "El archivo seleccionado no existe."
    if not source_path.lower().endswith(".pdf"):
        return None, "El archivo seleccionado no es un PDF."

    try:
        with fitz.open(source_path) as doc:
            if not doc.is_pdf:
                return None, "El archivo seleccionado no es un PDF válido."
    except Exception as e:
        return None, f"No se pudo validar el archivo PDF: {e}"

    try:
        os.makedirs(storage_dir, exist_ok=True)
        dest_path = _unique_destination_path(storage_dir, os.path.basename(source_path))
        shutil.copy2(source_path, dest_path)
    except OSError as e:
        return None, f"No se pudo copiar el archivo a la carpeta de la aplicación: {e}"

    return dest_path, None


def delete_pdf_file(filepath: str) -> str | None:
    """
    Delete a PDF from the app's storage folder (used by the delete button
    in the settings "Loaded PDFs" list).

    Returns None on success, or an error message readable by the UI.
    """
    try:
        os.remove(filepath)
        return None
    except OSError as e:
        return f"No se pudo eliminar el archivo: {e}"


def render_page_preview(
    filepath: str,
    page_num: int,
    crop_coords: tuple[int, int, int, int],
    dpi: int = 120,
) -> tuple[bytes | None, str, str | None]:
    """
    Render a PDF page to PNG (with the crop region outlined in red) and also return
    the text extracted from that region. Intended for the UI's preview panel.

    Returns (png_bytes, extracted_text, error). On error, png_bytes is None and
    extracted_text is "".
    """
    x0, y0, width, height = crop_coords
    rect = fitz.Rect(x0, y0, x0 + width, y0 + height)

    try:
        with fitz.open(filepath) as doc:
            page_idx = page_num - 1
            if page_idx < 0 or page_idx >= doc.page_count:
                return None, "", f"La página {page_num} no existe en este archivo."

            page = doc[page_idx]
            extracted_text = page.get_text("text", clip=rect).strip()
            page.draw_rect(rect, color=(1, 0, 0), width=2.5)
            pix = page.get_pixmap(dpi=dpi)
            return pix.tobytes("png"), extracted_text, None
    except Exception as e:
        return None, "", f"No se pudo renderizar la página: {e}"
