"""Structural readability checks. This is not antivirus or a trusted-content claim."""
from io import BytesIO
import warnings


def readable(blob,suffix):
    try:
        if suffix in ['.png','.jpg','.jpeg']:
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(BytesIO(blob)) as image:
                    if image.width*image.height>20_000_000:return False
                    if image.format!=('PNG' if suffix=='.png' else 'JPEG'):return False
                    image.verify()
                with Image.open(BytesIO(blob)) as image:image.load()
            return True
        if suffix=='.pdf':
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(blob),strict=True)
            return not reader.is_encrypted and 0<len(reader.pages)<=200
    except Exception:
        return False
    return False
