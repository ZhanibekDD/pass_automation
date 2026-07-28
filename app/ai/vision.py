from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.ai.config import AISettings
from app.ai.schemas import PreparedPage

SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


class DocumentRenderError(RuntimeError):
    pass


def _prepare_image(image: Image.Image, *, page_number: int, max_dimension: int) -> PreparedPage:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    normalized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    normalized.save(output, format="PNG", optimize=True)
    return PreparedPage(
        number=page_number,
        image_bytes=output.getvalue(),
        media_type="image/png",
        width=normalized.width,
        height=normalized.height,
    )


def iter_document_pages(path: Path, settings: AISettings) -> Iterator[PreparedPage]:
    if not path.is_file():
        raise DocumentRenderError(f"Файл не найден: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise DocumentRenderError(f"Неподдерживаемый формат {path.suffix!r}; разрешены PDF, JPG и PNG")
    if path.stat().st_size > settings.max_file_size_mb * 1024 * 1024:
        raise DocumentRenderError(f"Файл превышает лимит {settings.max_file_size_mb} МБ")

    if path.suffix.lower() != ".pdf":
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                yield _prepare_image(image, page_number=1, max_dimension=settings.max_image_dimension)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise DocumentRenderError("Изображение повреждено или небезопасно") from exc
        return

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise DocumentRenderError("Для PDF установите зависимости из requirements-ai.txt") from exc

    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:
        raise DocumentRenderError("PDF повреждён или не поддерживается") from exc

    try:
        if len(document) > settings.max_pages:
            raise DocumentRenderError(f"PDF содержит {len(document)} страниц, лимит — {settings.max_pages}")
        scale = settings.render_dpi / 72
        for page_index in range(len(document)):
            page = document[page_index]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                yield _prepare_image(
                    image,
                    page_number=page_index + 1,
                    max_dimension=settings.max_image_dimension,
                )
            finally:
                page.close()
    finally:
        document.close()
