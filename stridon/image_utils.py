"""Image helpers."""


def recompress_jpeg(data: bytes, quality: int) -> bytes:
    """Re-encode JPEG data at the given quality (1-100).

    Returns the original data when quality is 100, when re-encoding fails,
    or when the result would not be smaller.
    """
    if quality >= 100 or not data:
        return data
    try:
        import pymupdf

        pixmap = pymupdf.Pixmap(data)
        if pixmap.alpha or pixmap.colorspace is None or pixmap.colorspace.n not in (1, 3):
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap, 0)
        smaller = pixmap.tobytes("jpeg", jpg_quality=max(1, quality))
    except Exception:
        return data
    return smaller if len(smaller) < len(data) else data
