from io import BytesIO
from PIL import Image, UnidentifiedImageError
import magic
from typing import Optional, Set
from config.settings import MAX_FILE_SIZE, ALLOWED_MIME_TYPES
from exceptions import ValidationError


class FileSizeExceededError(Exception):
    def __init__(self, message: str, max_mb: int):
        self.message = message
        self.max_mb = max_mb
        super().__init__(message)


class FileValidator:
    def __init__(
        self,
        max_size: int = MAX_FILE_SIZE,
        allowed_mime_types: Set[str] = None
    ):
        self._max_size = max_size
        self._allowed_mime_types = allowed_mime_types or set(ALLOWED_MIME_TYPES)

    def validate(self, file_bytes: bytes, content_type: Optional[str] = None) -> None:
        self._validate_size(file_bytes)
        mime = magic.from_buffer(file_bytes, mime=True)
        if mime not in self._allowed_mime_types or (content_type and content_type != mime):
            raise ValidationError("File type not allowed or inconsistent with its content")
        try:
            with Image.open(BytesIO(file_bytes)) as image:
                if Image.MIME.get(image.format) != mime or image.width * image.height > 32_000_000:
                    raise ValidationError("Invalid image format or dimensions")
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise ValidationError("Invalid image file") from exc

    def _validate_size(self, file_bytes: bytes) -> None:
        if len(file_bytes) > self._max_size:
            max_mb = self._max_size // (1024 * 1024)
            raise FileSizeExceededError(
                f"File exceeds maximum allowed size of {max_mb}MB",
                max_mb
            )
