import asyncio
import os
import sys
import threading
from dataclasses import dataclass

import flet as ft

from src.config import get_config, get_pdf_storage_dir, pop_load_warnings
from src.licensing import LICENSE_FILENAME, check_license, install_license_file
from src.pdf_processor import (
    compile_pdf_from_results,
    delete_pdf_file,
    import_pdf_to_storage,
    list_pdf_files,
    render_page_preview,
    search_pdfs_in_directory,
)

THEME_MODE_MAP = {
    "Dark": ft.ThemeMode.DARK,
    "Light": ft.ThemeMode.LIGHT,
    "System": ft.ThemeMode.SYSTEM,
}

APP_LOGO_PATH = "protocolFinderLogo.png"


def _app_logo_icon(size: int = 18) -> ft.Image:
    """Logo de la app (fondo azul, letras blancas): tiene su propio color fijo,
    así que se muestra tal cual, sin reteñido por tema."""
    return ft.Image(
        src=APP_LOGO_PATH,
        width=size,
        height=size,
        border_radius=ft.BorderRadius.all(size * 0.22),
        fit=ft.BoxFit.COVER,
    )


@dataclass
class ResultRow:
    id: int
    filepath: str
    page_num: int
    detected_letters: str
    detected_numbers: str
    criterio: str


class ProtocolFinderApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.config = get_config()

        self.results: list[ResultRow] = []
        self._next_result_id = 1
        self.selected_ids: set[int] = set()
        self.active_result_id: int | None = None

        self.search_mode = "individual"
        self.batch_queue: list[tuple[str, str]] = []

        self.cancel_event = threading.Event()
        self.is_searching = False
        self._search_result_count = 0
        self._search_errors: list[tuple[str, str]] = []

        self._build_pickers()
        self._build_search_controls()
        self._build_title_bar()
        self._build_results_controls()
        self._build_preview_controls()
        self._build_settings_dialog()
        self._build_layout()

        self.page.theme_mode = THEME_MODE_MAP.get(self.config.theme_mode, ft.ThemeMode.DARK)
        self._show_startup_warnings()

    # ------------------------------------------------------------
    # Construcción de controles
    # ------------------------------------------------------------
    def _build_pickers(self):
        self.file_picker_import = ft.FilePicker()
        self.file_picker_save = ft.FilePicker()
        self.page.services.extend([self.file_picker_import, self.file_picker_save])

    def _build_title_bar(self):
        """Barra de título personalizada: reemplaza el appbar nativo de Windows,
        fusionando ícono + título + selector de modo + ajustes + botones de
        minimizar/maximizar/cerrar en una sola franja arrastrable."""
        self.page.window.title_bar_hidden = True

        self.minimize_button = ft.IconButton(
            icon=ft.Icons.REMOVE, icon_size=18, tooltip="Minimizar",
            on_click=self._on_minimize_window,
        )
        self.maximize_button = ft.IconButton(
            icon=ft.Icons.CROP_SQUARE, icon_size=15, tooltip="Maximizar",
            on_click=self._on_toggle_maximize_window,
        )
        self.close_button = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=18, tooltip="Cerrar",
            on_click=self._on_close_window,
        )

        self.custom_title_bar = ft.WindowDragArea(
            content=ft.Container(
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                _app_logo_icon(32),
                                ft.Text(
                                    "Protocol Finder",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    font_family=FONT_BOLD,
                                ),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.mode_selector,
                        ft.Container(expand=True),
                        self.settings_button,
                        self.minimize_button,
                        self.maximize_button,
                        self.close_button,
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding(left=16, right=8, top=8, bottom=8),
                bgcolor=ft.Colors.SURFACE,
            ),
            maximizable=True,
        )

    def _on_minimize_window(self, e):
        self.page.window.minimized = True
        self.page.window.update()

    def _on_toggle_maximize_window(self, e):
        self.page.window.maximized = not self.page.window.maximized
        self.maximize_button.icon = (
            ft.Icons.FILTER_NONE if self.page.window.maximized else ft.Icons.CROP_SQUARE
        )
        self.page.window.update()
        self.maximize_button.update()

    async def _on_close_window(self, e):
        await self.page.window.close()

    def _build_search_controls(self):
        self.mode_selector = ft.SegmentedButton(
            segments=[
                ft.Segment(value="individual", label="Individual"),
                ft.Segment(value="lote", label="Por lote"),
            ],
            selected=["individual"],
            allow_empty_selection=False,
            on_change=self._on_mode_change,
        )

        _field_padding = ft.Padding(left=12, right=12, top=14, bottom=14)

        self.letters_field = ft.TextField(
            label="Código Letras",
            hint_text="ej. ABCDEF",
            width=170,
            content_padding=_field_padding,
            max_length=6,
            counter="",
            input_filter=ft.TextOnlyInputFilter(),
            on_change=self._force_upper,
        )
        self.numbers_field = ft.TextField(
            label="Código Números",
            hint_text="ej. 33016475",
            width=200,
            content_padding=_field_padding,
            max_length=8,
            counter="",
            input_filter=ft.NumbersOnlyInputFilter(),
        )
        self.mode_individual_group = ft.Row(
            [self.letters_field, self.numbers_field],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.batch_letters_field = ft.TextField(
            label="Letras",
            hint_text="ej. ABCDEF",
            width=170,
            content_padding=_field_padding,
            max_length=6,
            counter="",
            input_filter=ft.TextOnlyInputFilter(),
            on_change=self._force_upper,
        )
        self.batch_numbers_field = ft.TextField(
            label="Números",
            hint_text="ej. 33016475",
            width=190,
            content_padding=_field_padding,
            max_length=8,
            counter="",
            input_filter=ft.NumbersOnlyInputFilter(),
        )
        self.batch_add_button = ft.IconButton(
            icon=ft.Icons.ADD,
            icon_size=18,
            tooltip="Agregar criterio a la cola",
            width=36,
            height=36,
            on_click=self._on_add_criteria,
        )
        self.mode_batch_group = ft.Row(
            [self.batch_letters_field, self.batch_numbers_field, self.batch_add_button],
            spacing=6,
            visible=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.batch_chips_row = ft.Row(spacing=6, visible=False, scroll=ft.ScrollMode.AUTO)

        self.search_button = ft.ElevatedButton(
            "Buscar", icon=ft.Icons.SEARCH, on_click=self._on_click_search
        )
        self.stop_button = ft.OutlinedButton(
            "Detener", icon=ft.Icons.STOP_CIRCLE, on_click=self._on_click_stop, disabled=True
        )
        self.settings_button = ft.IconButton(
            icon=ft.Icons.SETTINGS, tooltip="Configurar región de corte", on_click=self._open_settings_dialog
        )
        self.upload_button = ft.OutlinedButton(
            "Cargar PDF", icon=ft.Icons.UPLOAD_FILE, on_click=self._on_click_upload
        )

        self.progress_bar = ft.ProgressBar(value=0, expand=True)
        self.progress_label = ft.Text("Listo para buscar", size=11, color=ft.Colors.GREY)

    def _build_results_controls(self):
        self.results_list_view = ft.ListView(expand=True, spacing=4, padding=4)
        self.results_empty_state = ft.Text(
            "Sin resultados todavía. Ejecuta una búsqueda para ver coincidencias aquí.",
            size=12,
            color=ft.Colors.GREY,
        )
        self.results_list_view.controls.append(self.results_empty_state)
        self.compile_button = ft.ElevatedButton(
            "Generar PDF con seleccionados",
            icon=ft.Icons.SAVE,
            on_click=self._on_click_compile,
            disabled=True,
        )

    def _build_preview_controls(self):
        self.preview_title = ft.Text(
            "Vista previa de la página", size=14, weight=ft.FontWeight.BOLD, font_family=FONT_BOLD
        )
        self.preview_area = ft.Container(
            content=ft.Column(
                [ft.Text(
                    "Selecciona un resultado para visualizar la página del PDF.",
                    size=12,
                    color=ft.Colors.GREY,
                    text_align=ft.TextAlign.CENTER,
                )],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            expand=True,
            aspect_ratio=0.62,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border_radius=8,
            padding=8,
            alignment=ft.Alignment.CENTER,
        )

    def _build_settings_dialog(self):
        self.settings_x0 = ft.TextField(label="Margen Izquierdo", width=150)
        self.settings_y0 = ft.TextField(label="Margen Superior", width=150)
        self.settings_w = ft.TextField(label="Ancho de Caja", width=150)
        self.settings_h = ft.TextField(label="Alto de Caja", width=150)

        self.theme_selector = ft.SegmentedButton(
            segments=[
                ft.Segment(value="Light", label="Claro", icon=ft.Icons.LIGHT_MODE),
                ft.Segment(value="Dark", label="Oscuro", icon=ft.Icons.DARK_MODE),
                ft.Segment(value="System", label="Sistema", icon=ft.Icons.BRIGHTNESS_AUTO),
            ],
            selected=[self.config.theme_mode],
            allow_empty_selection=False,
            on_change=self._on_theme_change,
        )

        self.loaded_pdfs_title = ft.Text(
            "PDFs cargados (0)", size=13, weight=ft.FontWeight.BOLD, font_family=FONT_BOLD
        )
        self.loaded_pdfs_list = ft.ListView(height=160, spacing=4)

        self.settings_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Configuración"),
            content=ft.Column(
                [
                    ft.Text("Tema de la aplicación", size=13, weight=ft.FontWeight.BOLD, font_family=FONT_BOLD),
                    self.theme_selector,
                    ft.Divider(height=1),
                    ft.Text(
                        "Coordenadas del área de corte, en puntos PDF, usada para leer\n",
                        size=12,
                        color=ft.Colors.GREY,
                    ),
                    ft.Row([self.settings_x0, self.settings_y0], spacing=10),
                    ft.Row([self.settings_w, self.settings_h], spacing=10),
                    ft.Divider(height=1),
                    self.loaded_pdfs_title,
                    ft.Container(
                        content=self.loaded_pdfs_list,
                        bgcolor=ft.Colors.BLACK12,
                        border_radius=6,
                        padding=4,
                    ),
                ],
                spacing=12,
                width=540,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton("Guardar", on_click=self._save_settings),
            ],
        )

    def _refresh_loaded_pdfs_list(self):
        filepaths = list_pdf_files(get_pdf_storage_dir())
        self.loaded_pdfs_title.value = f"PDFs cargados ({len(filepaths)})"
        if not filepaths:
            self.loaded_pdfs_list.controls = [
                ft.Text("Ningún PDF cargado todavía.", size=12, color=ft.Colors.GREY)
            ]
        else:
            self.loaded_pdfs_list.controls = [
                ft.Row(
                    [
                        ft.Text(
                            os.path.basename(fp),
                            size=12,
                            expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.OPEN_IN_NEW,
                            icon_size=16,
                            tooltip="Abrir PDF con la aplicación predeterminada",
                            on_click=lambda e, p=fp: self._open_pdf_externally(p),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=16,
                            icon_color=ft.Colors.RED_300,
                            tooltip="Eliminar de la biblioteca",
                            on_click=lambda e, p=fp: self._confirm_delete_pdf(p),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                for fp in filepaths
            ]

    def _confirm_delete_pdf(self, filepath: str):
        def on_confirm(e):
            self.page.pop_dialog()
            error = delete_pdf_file(filepath)
            self._refresh_loaded_pdfs_list()
            self.loaded_pdfs_list.update()
            self.loaded_pdfs_title.update()
            self._show_snack(
                error if error else f"'{os.path.basename(filepath)}' eliminado de la biblioteca."
            )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Eliminar PDF"),
            content=ft.Text(
                f"¿Eliminar '{os.path.basename(filepath)}' de la biblioteca? "
                "Esta acción no se puede deshacer."
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton("Eliminar", icon=ft.Icons.DELETE, on_click=on_confirm),
            ],
        )
        self.page.show_dialog(dialog)

    def _panel_header(self, content: ft.Control) -> ft.Container:
        return ft.Container(
            content=content,
            padding=ft.Padding(left=12, right=12, top=10, bottom=10),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

    def _build_layout(self):
        search_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row(
                                [self.mode_individual_group, self.mode_batch_group],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                [self.search_button, self.stop_button, self.upload_button],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.batch_chips_row,
                ],
                spacing=8,
            ),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=12,
            padding=10,
        )

        top_bar = ft.Container(
            content=ft.Column(
                [
                    search_card,
                    ft.Row([self.progress_bar, self.progress_label], spacing=12,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ],
                spacing=10,
            ),
            padding=16,
        )

        left_column = ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            self._panel_header(
                                ft.Text(
                                    "Resultados (marca las hojas a incluir en el PDF final)",
                                    size=13,
                                    weight=ft.FontWeight.BOLD,
                                    font_family=FONT_BOLD,
                                )
                            ),
                            ft.Container(
                                content=self.results_list_view,
                                expand=True,
                                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                                padding=8,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    expand=True,
                    border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=12,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
                self.compile_button,
            ],
            expand=1,
            spacing=10,
        )

        right_column = ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            self._panel_header(self.preview_title),
                            ft.Container(
                                content=self.preview_area,
                                expand=True,
                                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                                padding=16,
                                alignment=ft.Alignment.CENTER,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    expand=True,
                    border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=12,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
            ],
            expand=1,
            spacing=10,
        )

        main_row = ft.Row([left_column, right_column], expand=True, spacing=16)

        self.page.add(
            ft.Column(
                [
                    self.custom_title_bar,
                    top_bar,
                    ft.Divider(height=1),
                    ft.Container(content=main_row, expand=True, padding=16),
                ],
                expand=True,
                spacing=0,
            )
        )

    # ------------------------------------------------------------
    # Helpers de UI
    # ------------------------------------------------------------
    def _show_snack(self, message: str):
        self.page.show_dialog(ft.SnackBar(content=ft.Text(message)))

    def _show_startup_warnings(self):
        warnings = pop_load_warnings()
        if warnings:
            self._show_snack("\n".join(warnings))

    def _force_upper(self, e: ft.ControlEvent):
        field = e.control
        if field.value and field.value != field.value.upper():
            field.value = field.value.upper()
            field.update()

    @staticmethod
    def _criteria_label(letters: str, numbers: str) -> str:
        if letters and numbers:
            return f"{letters} + {numbers}"
        return letters or numbers

    # ------------------------------------------------------------
    # Modo de búsqueda (individual / lote)
    # ------------------------------------------------------------
    def _on_mode_change(self, e: ft.ControlEvent):
        selected = e.control.selected
        self.search_mode = "lote" if "lote" in selected else "individual"
        self.mode_individual_group.visible = self.search_mode == "individual"
        self.mode_batch_group.visible = self.search_mode == "lote"
        self.batch_chips_row.visible = self.search_mode == "lote"
        self.mode_individual_group.update()
        self.mode_batch_group.update()
        self.batch_chips_row.update()

    def _on_add_criteria(self, e):
        letters = (self.batch_letters_field.value or "").strip().upper()
        numbers = (self.batch_numbers_field.value or "").strip()
        if not letters and not numbers:
            self._show_snack("Ingresa letras o números para agregar a la cola.")
            return
        if (letters, numbers) in self.batch_queue:
            return

        self.batch_queue.append((letters, numbers))
        self.batch_letters_field.value = ""
        self.batch_numbers_field.value = ""
        self.batch_letters_field.update()
        self.batch_numbers_field.update()
        self._refresh_batch_chips()

    def _remove_criteria(self, index: int):
        if 0 <= index < len(self.batch_queue):
            self.batch_queue.pop(index)
            self._refresh_batch_chips()

    def _refresh_batch_chips(self):
        self.batch_chips_row.controls = [
            ft.Container(
                content=ft.Row(
                    [
                        ft.Text(self._criteria_label(letters, numbers), size=11),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=14,
                            on_click=lambda e, idx=i: self._remove_criteria(idx),
                        ),
                    ],
                    spacing=2,
                ),
                bgcolor=ft.Colors.BLUE_GREY_700,
                border_radius=16,
                padding=6,
            )
            for i, (letters, numbers) in enumerate(self.batch_queue)
        ]
        self.batch_chips_row.update()

    # ------------------------------------------------------------
    # Carga de PDFs a la biblioteca de la app
    # ------------------------------------------------------------
    async def _on_click_upload(self, e):
        files = await self.file_picker_import.pick_files(
            dialog_title="Seleccionar PDF para agregar a la biblioteca",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pdf"],
            allow_multiple=True,
        )
        if not files:
            return

        storage_dir = get_pdf_storage_dir()
        imported = 0
        errors: list[tuple[str, str]] = []
        for f in files:
            if not f.path:
                errors.append((f.name, "No se pudo acceder a la ruta del archivo."))
                continue
            _dest, error = import_pdf_to_storage(f.path, storage_dir)
            if error:
                errors.append((f.name, error))
            else:
                imported += 1

        parts = []
        if imported:
            parts.append(f"{imported} PDF(s) agregado(s) a la biblioteca.")
        if errors:
            parts.append("No se pudieron agregar: " + "; ".join(f"{n} ({m})" for n, m in errors))
        if parts:
            self._show_snack("\n".join(parts))

    # ------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------
    def _on_click_search(self, e):
        if self.is_searching:
            return

        search_pairs = None
        letters, numbers = "", ""

        if self.search_mode == "lote":
            if not self.batch_queue:
                self._show_snack("Agrega al menos un criterio a la cola de búsqueda por lote.")
                return
            search_pairs = [
                (sl, sn, self._criteria_label(sl, sn)) for sl, sn in self.batch_queue
            ]
        else:
            letters = (self.letters_field.value or "").strip()
            numbers = (self.numbers_field.value or "").strip()
            if not letters and not numbers:
                self._show_snack("Ingresa al menos un código de letras o números para buscar.")
                return

        storage_dir = get_pdf_storage_dir()
        if not os.listdir(storage_dir):
            self._show_snack("La biblioteca de PDFs está vacía. Usa 'Cargar PDF' para agregar archivos.")
            return

        crop_coords = (
            self.config.crop_x0,
            self.config.crop_y0,
            self.config.crop_width,
            self.config.crop_height,
        )

        self.cancel_event = threading.Event()
        self.is_searching = True
        self._search_errors = []
        self._search_result_count = 0
        self._set_search_controls_enabled(False)
        self.progress_bar.value = 0
        self.progress_label.value = "Iniciando búsqueda..."
        self.progress_bar.update()
        self.progress_label.update()

        cancel_event = self.cancel_event

        def worker():
            search_pdfs_in_directory(
                directory=storage_dir,
                search_letters=letters,
                search_numbers=numbers,
                crop_coords=crop_coords,
                progress_callback=lambda cur, total, name: self.page.run_task(self._on_progress, cur, total, name),
                result_callback=lambda **kw: self.page.run_task(self._on_result, kw),
                error_callback=lambda fp, msg: self.page.run_task(self._on_search_error, fp, msg),
                cancel_event=cancel_event,
                search_pairs=search_pairs,
            )
            self.page.run_task(self._on_search_finished, cancel_event)

        self.page.run_thread(worker)

    def _on_click_stop(self, e):
        if self.is_searching:
            self.cancel_event.set()
            self.progress_label.value = "Cancelando..."
            self.progress_label.update()

    def _set_search_controls_enabled(self, enabled: bool):
        self.search_button.disabled = not enabled
        self.stop_button.disabled = enabled
        self.upload_button.disabled = not enabled
        self.mode_selector.disabled = not enabled
        self.letters_field.disabled = not enabled
        self.numbers_field.disabled = not enabled
        self.batch_letters_field.disabled = not enabled
        self.batch_numbers_field.disabled = not enabled
        self.batch_add_button.disabled = not enabled
        for control in (
            self.search_button, self.stop_button, self.upload_button, self.mode_selector,
            self.letters_field, self.numbers_field, self.batch_letters_field,
            self.batch_numbers_field, self.batch_add_button,
        ):
            control.update()

    async def _on_progress(self, current: int, total: int, filename: str):
        self.progress_bar.value = current / total if total else 0
        self.progress_label.value = f"Procesado {current}/{total}: {filename}"
        self.progress_bar.update()
        self.progress_label.update()

    async def _on_result(self, kw: dict):
        filepath = kw["filepath"]
        page_num = kw["page_num"]

        for r in self.results:
            if r.filepath == filepath and r.page_num == page_num:
                return

        row = ResultRow(
            id=self._next_result_id,
            filepath=filepath,
            page_num=page_num,
            detected_letters=kw["detected_letters"],
            detected_numbers=kw["detected_numbers"],
            criterio=kw["criterio"],
        )
        self._next_result_id += 1
        self.results.append(row)
        self._search_result_count += 1
        if self.results_empty_state in self.results_list_view.controls:
            self.results_list_view.controls.remove(self.results_empty_state)
        self.results_list_view.controls.append(self._build_result_row(row))
        self.results_list_view.update()

    async def _on_search_error(self, filepath: str, message: str):
        self._search_errors.append((filepath, message))

    async def _on_search_finished(self, cancel_event: threading.Event):
        self.is_searching = False
        self._set_search_controls_enabled(True)
        cancelled = cancel_event.is_set()
        self.progress_label.value = "Búsqueda cancelada." if cancelled else "Búsqueda finalizada."
        self.progress_label.update()

        parts = []
        if self._search_errors:
            n = len(self._search_errors)
            parts.append(f"{n} archivo(s) no se pudieron procesar durante la búsqueda.")
            self._search_errors = []
        if not cancelled and self._search_result_count == 0:
            parts.append("No se encontraron coincidencias para esta búsqueda.")
            if not self.results:
                self.results_empty_state.value = "No se encontraron coincidencias para tu búsqueda."
                self.results_empty_state.update()
        if parts:
            self._show_snack("\n".join(parts))

    # ------------------------------------------------------------
    # Resultados: filas, selección y apertura externa
    # ------------------------------------------------------------
    def _build_result_row(self, row: ResultRow) -> ft.Control:
        is_active = row.id == self.active_result_id

        checkbox = ft.Checkbox(
            value=row.id in self.selected_ids,
            on_change=lambda e, rid=row.id: self._on_toggle_selected(rid, e.control.value),
        )

        info = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Text(
                                    row.criterio, size=10, weight=ft.FontWeight.BOLD, font_family=FONT_BOLD
                                ),
                                bgcolor=ft.Colors.BLUE_700,
                                padding=6,
                                border_radius=6,
                            ),
                            ft.Text(
                                os.path.basename(row.filepath),
                                size=13,
                                weight=ft.FontWeight.W_500,
                                font_family=FONT_MEDIUM,
                                expand=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(f"Pág. {row.page_num}", size=11, color=ft.Colors.GREY),
                        ],
                        spacing=8,
                    ),
                    ft.Text(f"{row.detected_letters}  ·  {row.detected_numbers}", size=11, color=ft.Colors.GREY),
                ],
                spacing=2,
            ),
            on_click=lambda e, rid=row.id: self._on_select_result(rid),
            ink=True,
            expand=True,
            padding=6,
            border_radius=6,
        )

        open_button = ft.IconButton(
            icon=ft.Icons.OPEN_IN_NEW,
            icon_size=18,
            tooltip="Abrir PDF con la aplicación predeterminada",
            on_click=lambda e, fp=row.filepath: self._open_pdf_externally(fp),
        )

        return ft.Container(
            content=ft.Row([checkbox, info, open_button], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ft.Colors.BLUE_GREY_900 if is_active else None,
            border_radius=8,
            padding=2,
        )

    def _refresh_results_list(self):
        self.results_list_view.controls = [self._build_result_row(r) for r in self.results]
        self.results_list_view.update()

    def _on_toggle_selected(self, result_id: int, checked: bool):
        if checked:
            self.selected_ids.add(result_id)
        else:
            self.selected_ids.discard(result_id)
        self.compile_button.disabled = len(self.selected_ids) == 0
        self.compile_button.update()

    def _open_pdf_externally(self, filepath: str):
        try:
            os.startfile(filepath)
        except OSError as e:
            self._show_snack(f"No se pudo abrir el archivo:\n{e}")

    # ------------------------------------------------------------
    # Vista previa
    # ------------------------------------------------------------
    def _on_select_result(self, result_id: int):
        self.active_result_id = result_id
        row = next((r for r in self.results if r.id == result_id), None)
        if row is None:
            return
        self._refresh_results_list()
        self._render_preview(row)

    def _render_preview(self, row: ResultRow):
        crop_coords = (
            self.config.crop_x0,
            self.config.crop_y0,
            self.config.crop_width,
            self.config.crop_height,
        )
        self.preview_title.value = f"{os.path.basename(row.filepath)} — Pág. {row.page_num}"
        self.preview_title.update()
        self.preview_area.content = ft.Text("Cargando vista previa...", size=12, color=ft.Colors.GREY)
        self.preview_area.update()

        def worker():
            png_bytes, _text, error = render_page_preview(row.filepath, row.page_num, crop_coords)
            self.page.run_task(self._on_preview_ready, png_bytes, error)

        self.page.run_thread(worker)

    async def _on_preview_ready(self, png_bytes: bytes | None, error: str | None):
        if error:
            self.preview_area.content = ft.Text(error, size=12, color=ft.Colors.RED_300)
        else:
            self.preview_area.content = ft.Image(src=png_bytes, fit=ft.BoxFit.CONTAIN, expand=True, border_radius=6)
        self.preview_area.update()

    # ------------------------------------------------------------
    # Configuración de la región de corte
    # ------------------------------------------------------------
    def _open_settings_dialog(self, e):
        self.settings_x0.value = str(self.config.crop_x0)
        self.settings_y0.value = str(self.config.crop_y0)
        self.settings_w.value = str(self.config.crop_width)
        self.settings_h.value = str(self.config.crop_height)
        self.theme_selector.selected = [self.config.theme_mode]
        self._refresh_loaded_pdfs_list()
        self.page.show_dialog(self.settings_dialog)

    def _on_theme_change(self, e: ft.ControlEvent):
        mode = next(iter(e.control.selected), "Dark")
        self.page.theme_mode = THEME_MODE_MAP.get(mode, ft.ThemeMode.DARK)
        self.page.update()

        self.config.theme_mode = mode
        error = self.config.save()
        if error:
            self._show_snack(error)

    def _save_settings(self, e):
        try:
            x0 = int(self.settings_x0.value)
            y0 = int(self.settings_y0.value)
            width = int(self.settings_w.value)
            height = int(self.settings_h.value)
            if width <= 0 or height <= 0:
                raise ValueError("width/height deben ser positivos")
        except (TypeError, ValueError):
            self._show_snack("Las coordenadas deben ser números enteros válidos (ancho y alto mayores a 0).")
            return

        self.config.crop_x0 = x0
        self.config.crop_y0 = y0
        self.config.crop_width = width
        self.config.crop_height = height
        error = self.config.save()

        self.page.pop_dialog()
        self._show_snack(error if error else "Región de corte actualizada.")

    # ------------------------------------------------------------
    # Compilación del PDF final
    # ------------------------------------------------------------
    async def _on_click_compile(self, e):
        if not self.selected_ids:
            return

        save_path = await self.file_picker_save.save_file(
            dialog_title="Guardar PDF consolidado",
            file_name="consolidado.pdf",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pdf"],
        )
        if not save_path:
            return
        if not save_path.lower().endswith(".pdf"):
            save_path += ".pdf"

        selected_rows = [r for r in self.results if r.id in self.selected_ids]
        items = [{"filepath": r.filepath, "page_num": r.page_num} for r in selected_rows]

        self.compile_button.disabled = True
        self.compile_button.update()

        def worker():
            errors: list[tuple[str, str]] = []
            try:
                n = compile_pdf_from_results(items, save_path, error_callback=lambda fp, msg: errors.append((fp, msg)))
                self.page.run_task(self._on_compile_done, n, errors, save_path)
            except Exception as ex:
                self.page.run_task(self._on_compile_failed, str(ex))

        self.page.run_thread(worker)

    async def _on_compile_done(self, n: int, errors: list[tuple[str, str]], save_path: str):
        self.compile_button.disabled = len(self.selected_ids) == 0
        self.compile_button.update()
        message = f"PDF generado con {n} hoja(s) en:\n{save_path}"
        if errors:
            details = "\n".join(f"- {os.path.basename(fp)}: {msg}" for fp, msg in errors)
            message += f"\n\nAlgunos elementos no se pudieron incluir:\n{details}"
        self._show_snack(message)

    async def _on_compile_failed(self, message: str):
        self.compile_button.disabled = len(self.selected_ids) == 0
        self.compile_button.update()
        self._show_snack(f"No se pudo generar el PDF:\n{message}")


FONT_REGULAR = "JetBrains Mono Nerd Font"
FONT_MEDIUM = "JetBrains Mono Nerd Font Medium"
FONT_BOLD = "JetBrains Mono Nerd Font Bold"


def _build_locked_screen(page: ft.Page, hardware_id: str):
    page.window.title_bar_hidden = True
    page.window.width = 680
    page.window.height = 620
    page.window.min_width = 680
    page.window.min_height = 620
    page.theme_mode = ft.ThemeMode.DARK

    minimize_button = ft.IconButton(icon=ft.Icons.REMOVE, icon_size=16, tooltip="Minimizar")
    close_button = ft.IconButton(icon=ft.Icons.CLOSE, icon_size=16, tooltip="Cerrar")

    def on_minimize(e):
        page.window.minimized = True
        page.window.update()

    async def on_close(e):
        await page.window.close()

    minimize_button.on_click = on_minimize
    close_button.on_click = on_close

    clipboard = ft.Clipboard()
    page.services.append(clipboard)

    id_field = ft.TextField(
        value=hardware_id, read_only=True, text_align=ft.TextAlign.CENTER, width=480,
        text_style=ft.TextStyle(size=12, font_family=FONT_MEDIUM),
    )

    async def on_copy(e):
        await clipboard.set(hardware_id)
        snack = ft.SnackBar(content=ft.Text("Copiado."))
        page.show_dialog(snack)

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    selected_path: list[str | None] = [None]

    file_status_text = ft.Text("Ningún archivo seleccionado.", size=12, color=ft.Colors.GREY)
    validate_status_text = ft.Text("", size=12)

    validate_button = ft.ElevatedButton(
        "Validar licencia", icon=ft.Icons.CHECK_CIRCLE_OUTLINE, disabled=True,
    )

    def _apply_selected_file(path: str | None):
        selected_path[0] = path
        validate_status_text.value = ""
        if not path:
            file_status_text.value = "Ningún archivo seleccionado."
            file_status_text.color = ft.Colors.GREY
            validate_button.disabled = True
        else:
            name = os.path.basename(path)
            if name != LICENSE_FILENAME:
                file_status_text.value = f'"{name}" no es válido (se espera "{LICENSE_FILENAME}").'
                file_status_text.color = ft.Colors.RED
                validate_button.disabled = True
            else:
                file_status_text.value = f"Archivo listo: {name}"
                file_status_text.color = ft.Colors.GREEN
                validate_button.disabled = False
        file_status_text.update()
        validate_status_text.update()
        validate_button.update()

    async def on_browse(e):
        files = await file_picker.pick_files(
            dialog_title="Seleccionar archivo de licencia",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["lic"],
            allow_multiple=False,
        )
        if files:
            _apply_selected_file(files[0].path)

    async def on_validate(e):
        path = selected_path[0]
        if not path:
            return
        validate_button.disabled = True
        validate_button.update()
        ok, message = install_license_file(path)
        validate_status_text.value = message
        validate_status_text.color = ft.Colors.GREEN if ok else ft.Colors.RED
        validate_status_text.update()
        if ok:
            await asyncio.sleep(1)
            page.clean()
            page.window.width = 1400
            page.window.height = 800
            page.window.min_width = 1100
            page.window.min_height = 650
            page.window.update()
            ProtocolFinderApp(page)
        else:
            validate_button.disabled = False
            validate_button.update()

    drop_zone = ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.UPLOAD_FILE, size=26, color=ft.Colors.GREY),
                ft.Text(
                    f'Subí aquí tu archivo "{LICENSE_FILENAME}"',
                    size=12, color=ft.Colors.GREY, text_align=ft.TextAlign.CENTER,
                ),
                file_status_text,
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        width=480,
        padding=16,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=10,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        on_click=on_browse,
    )

    license_upload_section = ft.Column(
        [
            drop_zone,
            ft.Row(
                [
                    ft.OutlinedButton("Buscar archivo", icon=ft.Icons.FOLDER_OPEN, on_click=on_browse),
                    validate_button,
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            validate_status_text,
        ],
        spacing=12,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    validate_button.on_click = on_validate

    title_bar = ft.WindowDragArea(
        content=ft.Container(
            content=ft.Row(
                [
                    _app_logo_icon(),
                    ft.Container(expand=True),
                    minimize_button,
                    close_button,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=12, right=4, top=2, bottom=2),
            bgcolor=ft.Colors.SURFACE,
            height=36,
        ),
        maximizable=False,
    )

    page.add(
        ft.Column(
            [
                title_bar,
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.Icons.LOCK_OUTLINE, size=40, color=ft.Colors.GREY),
                            ft.Text(
                                "Protocol Finder no está activado en este equipo",
                                size=16, weight=ft.FontWeight.BOLD, font_family=FONT_BOLD,
                                text_align=ft.TextAlign.CENTER,
                            ),
                            ft.Text(
                                "Envía el siguiente código a tu proveedor para recibir "
                                "el archivo de activación de este equipo.",
                                size=12, color=ft.Colors.GREY, text_align=ft.TextAlign.CENTER,
                            ),
                            ft.Row(
                                [id_field, ft.IconButton(icon=ft.Icons.COPY, tooltip="Copiar", on_click=on_copy)],
                                spacing=8,
                            ),
                            ft.Divider(height=1),
                            ft.Text(
                                "¿Ya tienes el archivo de activación? Cárgalo aquí.",
                                size=12, color=ft.Colors.GREY, text_align=ft.TextAlign.CENTER,
                            ),
                            license_upload_section,
                        ],
                        spacing=16,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    expand=True,
                    padding=32,
                    alignment=ft.Alignment.CENTER,
                ),
            ],
            expand=True,
            spacing=0,
        )
    )


def main(page: ft.Page):
    page.title = "Protocol Finder"
    page.window.width = 1400
    page.window.height = 800
    page.window.min_width = 1100
    page.window.min_height = 650
    # Requiere ruta absoluta: una ruta relativa tipo "icon.ico" hace que el cliente de
    # escritorio no encuentre el archivo y la app se cierre sola sin ningún error visible.
    # No se puede derivar de __file__: en el .exe empaquetado el código no vive en
    # archivos .py sueltos, así que esa ruta apunta a un lugar inexistente. En cambio,
    # tanto en desarrollo (venv/Scripts/python.exe) como en el build empaquetado
    # (app/venv/Scripts/python.exe) la carpeta "assets" está siempre tres niveles
    # arriba del intérprete en uso.
    app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))))
    page.window.icon = os.path.join(app_root, "assets", "icon.ico")

    page.fonts = {
        FONT_REGULAR: "fonts/JetBrainsMonoNerdFont-Regular.ttf",
        FONT_MEDIUM: "fonts/JetBrainsMonoNerdFont-Medium.ttf",
        FONT_BOLD: "fonts/JetBrainsMonoNerdFont-Bold.ttf",
    }
    page.theme = ft.Theme(font_family=FONT_REGULAR)
    page.dark_theme = ft.Theme(font_family=FONT_REGULAR)

    is_licensed, info = check_license()
    if not is_licensed:
        _build_locked_screen(page, hardware_id=info)
        return

    ProtocolFinderApp(page)
