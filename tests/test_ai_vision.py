import pytest
from PIL import Image

from app.ai.vision import iter_document_pages
from tests.helpers import make_settings


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_images_are_prepared_with_page_provenance(tmp_path, suffix: str) -> None:
    source = tmp_path / f"document{suffix}"
    Image.new("RGB", (2400, 1200), "white").save(source)
    pages = list(iter_document_pages(source, make_settings(tmp_path)))
    assert len(pages) == 1
    assert pages[0].number == 1
    assert pages[0].media_type == "image/png"
    assert max(pages[0].width, pages[0].height) <= 1200


def test_pdf_is_rendered_page_by_page(tmp_path) -> None:
    source = tmp_path / "document.pdf"
    Image.new("RGB", (800, 600), "white").save(source, format="PDF")
    pages = list(iter_document_pages(source, make_settings(tmp_path)))
    assert len(pages) == 1
    assert pages[0].number == 1
    assert pages[0].image_bytes.startswith(b"\x89PNG")
