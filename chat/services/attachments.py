from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from chat.models import Attachment, Message


class AttachmentValidationError(ValueError):
    """Понятная пользователю причина, по которой файл не принят."""


@dataclass(frozen=True)
class AttachmentMetadata:
    """Проверенные данные, которые сохраняются вместе с файлом."""

    original_name: str
    content_type: str
    size: int
    kind: str


IMAGE_TYPES = {
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".png": ("PNG", "image/png"),
    ".webp": ("WEBP", "image/webp"),
}
TEXT_FILE_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".py": "text/x-python",
    ".js": "text/javascript",
}
PDF_CONTENT_TYPE = "application/pdf"


def _clean_filename(uploaded_file: UploadedFile) -> tuple[str, str]:
    filename = Path(uploaded_file.name or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or not suffix:
        raise AttachmentValidationError("У файла должно быть имя с расширением.")
    return filename[:255], suffix


def _validate_image(uploaded_file: UploadedFile, suffix: str) -> AttachmentMetadata:
    if uploaded_file.size > settings.ATTACHMENT_IMAGE_MAX_SIZE:
        raise AttachmentValidationError("Изображение не должно быть больше 5 МБ.")

    expected_format, content_type = IMAGE_TYPES[suffix]
    try:
        image = Image.open(uploaded_file)
        image.verify()
        if image.format != expected_format:
            raise AttachmentValidationError(
                "Содержимое изображения не совпадает с его расширением."
            )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise AttachmentValidationError("Файл не является корректным изображением.") from error
    finally:
        uploaded_file.seek(0)

    return AttachmentMetadata(
        original_name=Path(uploaded_file.name).name[:255],
        content_type=content_type,
        size=uploaded_file.size,
        kind=Attachment.Kind.IMAGE,
    )


def _validate_document(
    uploaded_file: UploadedFile,
    suffix: str,
) -> AttachmentMetadata:
    if uploaded_file.size > settings.ATTACHMENT_FILE_MAX_SIZE:
        raise AttachmentValidationError("Файл не должен быть больше 2 МБ.")

    content = uploaded_file.read()
    uploaded_file.seek(0)
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise AttachmentValidationError("Файл с расширением PDF не похож на PDF.")
        content_type = PDF_CONTENT_TYPE
    else:
        if b"\x00" in content:
            raise AttachmentValidationError("Текстовый файл содержит недопустимые данные.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AttachmentValidationError(
                "Текстовый файл должен быть в кодировке UTF-8."
            ) from error
        content_type = TEXT_FILE_TYPES[suffix]

    return AttachmentMetadata(
        original_name=Path(uploaded_file.name).name[:255],
        content_type=content_type,
        size=uploaded_file.size,
        kind=Attachment.Kind.FILE,
    )


def validate_attachment(uploaded_file: UploadedFile) -> AttachmentMetadata:
    """Проверяет расширение, размер и реальные данные до записи на диск."""
    original_name, suffix = _clean_filename(uploaded_file)
    if uploaded_file.size <= 0:
        raise AttachmentValidationError("Нельзя прикрепить пустой файл.")
    if suffix in IMAGE_TYPES:
        metadata = _validate_image(uploaded_file, suffix)
    elif suffix == ".pdf" or suffix in TEXT_FILE_TYPES:
        metadata = _validate_document(uploaded_file, suffix)
    else:
        raise AttachmentValidationError("Этот тип файла пока не поддерживается.")
    return AttachmentMetadata(
        original_name=original_name,
        content_type=metadata.content_type,
        size=metadata.size,
        kind=metadata.kind,
    )


def create_attachment(*, message: Message, uploaded_file: UploadedFile) -> Attachment:
    """Создаёт вложение только после полной проверки его содержимого."""
    return create_attachments(message=message, uploaded_files=[uploaded_file])[0]


def create_attachments(
    *,
    message: Message,
    uploaded_files: list[UploadedFile],
) -> list[Attachment]:
    """Проверяет весь набор до записи, чтобы не оставить частично принятый файл."""
    if not uploaded_files:
        raise AttachmentValidationError("Выберите хотя бы один файл.")
    if len(uploaded_files) + message.attachments.count() > settings.ATTACHMENT_MAX_COUNT:
        raise AttachmentValidationError("К сообщению можно добавить не больше трёх файлов.")

    checked_files = [
        (uploaded_file, validate_attachment(uploaded_file))
        for uploaded_file in uploaded_files
    ]
    with transaction.atomic():
        return [
            Attachment.objects.create(
                message=message,
                file=uploaded_file,
                original_name=metadata.original_name,
                content_type=metadata.content_type,
                size=metadata.size,
                kind=metadata.kind,
            )
            for uploaded_file, metadata in checked_files
        ]
