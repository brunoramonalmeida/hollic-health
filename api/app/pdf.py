"""A tiny PDF writer (no dependencies) for text documents such as receipts.

Produces a valid PDF 1.4: one or more A4 pages, the standard Helvetica /
Helvetica-Bold fonts (no embedding needed), text lines, horizontal rules and
JPEG images (embedded as-is with the DCTDecode filter, e.g. the hand-drawn
signature). Streams are left uncompressed so the text stays searchable (and
testable). Text is written in WinAnsi (Latin-1); characters outside it
become '?'.
"""

import struct
from dataclasses import dataclass, field

PAGE_WIDTH = 595.28  # A4, points
PAGE_HEIGHT = 841.89
MARGIN = 56.0
LINE_HEIGHT = 15.0


def _escape(text: str) -> str:
    """PDF string escaping in WinAnsi; unsupported characters become '?'."""
    encoded = text.encode("cp1252", errors="replace")
    out = []
    for byte in encoded:
        ch = chr(byte)
        if ch in "()\\":
            out.append("\\" + ch)
        elif 32 <= byte < 127:
            out.append(ch)
        else:
            out.append(f"\\{byte:03o}")
    return "".join(out)


def jpeg_info(data: bytes) -> tuple[int, int, int]:
    """(width, height, components) from the JPEG frame header; ValueError when not a JPEG."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    index = 2
    while index + 10 <= len(data):
        if data[index] != 0xFF:
            raise ValueError("corrupt JPEG")
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height, data[index + 9]
        index += 2 + length
    raise ValueError("no frame header")


@dataclass
class Image:
    name: str
    data: bytes
    width: int
    height: int
    components: int


@dataclass
class Page:
    ops: list[str] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)


class Document:
    """Build a document top-down; ``render()`` returns the PDF bytes."""

    def __init__(self):
        self.pages: list[Page] = [Page()]
        self.y = PAGE_HEIGHT - MARGIN

    @property
    def page(self) -> Page:
        return self.pages[-1]

    def _ensure_space(self, height: float) -> None:
        if self.y - height < MARGIN:
            self.pages.append(Page())
            self.y = PAGE_HEIGHT - MARGIN

    def text(self, value: str, *, size: float = 10, bold: bool = False, x: float = MARGIN, advance: bool = True) -> None:
        self._ensure_space(size * 1.5)
        font = "/F2" if bold else "/F1"
        self.page.ops.append(f"BT {font} {size:.1f} Tf {x:.2f} {self.y - size:.2f} Td ({_escape(value)}) Tj ET")
        if advance:
            self.y -= max(LINE_HEIGHT, size * 1.5)

    def columns(self, cells: list[tuple[str, float]], *, size: float = 10, bold: bool = False) -> None:
        """One line with several texts at the given x offsets (from the margin)."""
        self._ensure_space(size * 1.5)
        for value, offset in cells:
            self.text(value, size=size, bold=bold, x=MARGIN + offset, advance=False)
        self.y -= max(LINE_HEIGHT, size * 1.5)

    def rule(self) -> None:
        self._ensure_space(8)
        y = self.y - 4
        self.page.ops.append(f"0.75 w {MARGIN:.2f} {y:.2f} m {PAGE_WIDTH - MARGIN:.2f} {y:.2f} l S")
        self.y -= 10

    def gap(self, height: float = LINE_HEIGHT / 2) -> None:
        self.y -= height

    def image(self, jpeg: bytes, *, width: float, x: float = MARGIN) -> None:
        """Draw a JPEG scaled to ``width`` points, keeping its aspect ratio."""
        pixel_width, pixel_height, components = jpeg_info(jpeg)
        height = width * pixel_height / pixel_width
        self._ensure_space(height + 4)
        name = f"Im{sum(len(page.images) for page in self.pages) + 1}"
        self.page.images.append(Image(name, jpeg, pixel_width, pixel_height, components))
        y = self.y - height
        self.page.ops.append(f"q {width:.2f} 0 0 {height:.2f} {x:.2f} {y:.2f} cm /{name} Do Q")
        self.y = y - 4

    def render(self) -> bytes:
        objects: list[bytes] = []

        def add(body: str | bytes) -> int:
            objects.append(body.encode("latin-1") if isinstance(body, str) else body)
            return len(objects)

        font_regular = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        font_bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        image_ids: dict[str, int] = {}
        for page in self.pages:
            for image in page.images:
                color = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(image.components, "/DeviceRGB")
                header = (
                    f"<< /Type /XObject /Subtype /Image /Width {image.width} /Height {image.height} "
                    f"/ColorSpace {color} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(image.data)} >>\nstream\n"
                )
                image_ids[image.name] = add(header.encode("latin-1") + image.data + b"\nendstream")
        pages_id = len(objects) + 1 + 2 * len(self.pages)  # reserved after the page/content pairs
        page_ids = []
        for page in self.pages:
            content = "\n".join(page.ops).encode("latin-1")
            content_id = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
            xobjects = " ".join(f"/{image.name} {image_ids[image.name]} 0 R" for image in page.images)
            resources = f"/Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >>" + (f" /XObject << {xobjects} >>" if xobjects else "")
            page_id = add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_WIDTH:.2f} {PAGE_HEIGHT:.2f}] "
                f"/Resources << {resources} >> /Contents {content_id} 0 R >>"
            )
            page_ids.append(page_id)
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>") == pages_id
        catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for index, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
