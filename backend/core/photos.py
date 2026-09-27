"""Route photo uploads: decode, re-encode, strip every byte of metadata.

A phone photo carries the GPS position it was taken at in its EXIF block, which on a commute
route is often the owner's front door. Re-encoding from pixels drops EXIF, XMP and ICC-embedded
comments alike; the orientation is applied to the pixels first so nothing is lost with it.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_EDGE_PX = 2048
THUMB_EDGE_PX = 480
# Anything larger is not a photo but a decompression bomb (a 50 MP camera is 8688 x 5792).
MAX_PIXELS = 60_000_000
# MPO is what some cameras call a JPEG with a second frame. Browsers turn HEIC into JPEG on upload.
ACCEPTED_FORMATS = frozenset({"JPEG", "MPO", "PNG", "WEBP"})


class PhotoError(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedPhoto:
    image: bytes
    thumbnail: bytes
    width: int
    height: int


def _encode(image: Image.Image, edge: int, quality: int) -> bytes:
    copy = image.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    out = BytesIO()
    # No exif=, no icc_profile=: the encoder writes pixels and nothing else.
    copy.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def process_photo(data: bytes) -> ProcessedPhoto:
    """Validate an upload and return a bounded, metadata-free JPEG plus its thumbnail."""
    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.format not in ACCEPTED_FORMATS:
                raise PhotoError("Nur JPEG-, PNG- oder WebP-Fotos.")
            if probe.width * probe.height > MAX_PIXELS:
                raise PhotoError("Das Foto ist zu gross.")
            probe.load()
            image = ImageOps.exif_transpose(probe).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise PhotoError("Die Datei ist kein lesbares Foto.") from exc
    full = _encode(image, MAX_EDGE_PX, 85)
    with Image.open(BytesIO(full)) as encoded:
        width, height = encoded.size
    return ProcessedPhoto(image=full, thumbnail=_encode(image, THUMB_EDGE_PX, 78), width=width, height=height)
