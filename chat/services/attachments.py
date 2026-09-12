from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from chat.models import Attachment, Message


class AttachmentValidationError(ValueError):
    """Понятная пользователю причина, по которой файл не принят."""


class AttachmentInspectionUnavailable(RuntimeError):
    """Сервер не смог безопасно проверить содержимое аудиофайла."""


@dataclass(frozen=True)
class AttachmentMetadata:
    """Проверенные данные, которые сохраняются вместе с файлом."""

    original_name: str
    content_type: str
    size: int
    kind: str
    duration_ms: int | None = None


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
AUDIO_TYPES = {
    ".webm": "audio/webm",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}


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


def _validate_audio(uploaded_file: UploadedFile, suffix: str) -> AttachmentMetadata:
    if uploaded_file.size > settings.AUDIO_MESSAGE_MAX_SIZE:
        raise AttachmentValidationError("Аудиосообщение не должно быть больше 10 МБ.")

    header = uploaded_file.read(16)
    uploaded_file.seek(0)
    valid = (
        suffix == ".webm" and header.startswith(b"\x1aE\xdf\xa3")
        or suffix in {".mp4", ".m4a"} and header[4:8] == b"ftyp"
        or suffix == ".ogg" and header.startswith(b"OggS")
    )
    if not valid:
        raise AttachmentValidationError(
            "Содержимое аудиозаписи не совпадает с её форматом."
        )

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary_file:
            for chunk in uploaded_file.chunks():
                temporary_file.write(chunk)
            temporary_file.flush()
            result = subprocess.run(
                [
                    settings.FFPROBE_BINARY,
                    "-v", "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,codec_name,duration:packet=pts_time,duration_time",
                    "-of", "json",
                    temporary_file.name,
                ],
                capture_output=True,
                text=True,
                timeout=settings.FFPROBE_TIMEOUT_SECONDS,
                check=False,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        uploaded_file.seek(0)
        raise AttachmentInspectionUnavailable(
            "Сервис проверки аудиозаписей временно недоступен."
        ) from error
    finally:
        uploaded_file.seek(0)

    if result.returncode != 0:
        raise AttachmentValidationError("Не удалось прочитать аудиозапись.")
    try:
        probe = json.loads(result.stdout)
        streams = probe.get("streams", [])
    except (TypeError, json.JSONDecodeError) as error:
        raise AttachmentValidationError("Не удалось прочитать данные аудиозаписи.") from error

    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio_streams or any(stream.get("codec_type") == "video" for stream in streams):
        raise AttachmentValidationError("Файл должен содержать только аудиозапись.")
    allowed_codecs = {"aac", "opus", "vorbis"}
    if any(stream.get("codec_name") not in allowed_codecs for stream in audio_streams):
        raise AttachmentValidationError("Аудиокодек этой записи не поддерживается.")

    duration_candidates = [probe.get("format", {}).get("duration")]
    duration_candidates.extend(stream.get("duration") for stream in audio_streams)
    parsed_durations = []
    for candidate in duration_candidates:
        try:
            parsed_durations.append(float(candidate))
        except (TypeError, ValueError):
            continue
    if not parsed_durations:
        for packet in probe.get("packets", []):
            try:
                packet_end = float(packet["pts_time"]) + float(
                    packet.get("duration_time") or 0
                )
            except (KeyError, TypeError, ValueError):
                continue
            parsed_durations.append(packet_end)
    if not parsed_durations:
        raise AttachmentValidationError("Не удалось определить длительность аудиозаписи.")

    duration_seconds = max(parsed_durations)
    duration_ms = round(duration_seconds * 1000)
    if not 0 < duration_ms <= settings.AUDIO_MESSAGE_MAX_DURATION_SECONDS * 1000:
        raise AttachmentValidationError("Запись должна быть не длиннее трёх минут.")

    return AttachmentMetadata(
        original_name=Path(uploaded_file.name).name[:255],
        content_type=AUDIO_TYPES[suffix],
        size=uploaded_file.size,
        kind=Attachment.Kind.AUDIO,
        duration_ms=duration_ms,
    )


def validate_attachment(uploaded_file: UploadedFile) -> AttachmentMetadata:
    """Проверяет расширение, размер и реальные данные до записи на диск."""
    original_name, suffix = _clean_filename(uploaded_file)
    if uploaded_file.size <= 0:
        raise AttachmentValidationError("Нельзя прикрепить пустой файл.")
    if suffix in IMAGE_TYPES:
        metadata = _validate_image(uploaded_file, suffix)
    elif suffix in AUDIO_TYPES:
        metadata = _validate_audio(uploaded_file, suffix)
    elif suffix == ".pdf" or suffix in TEXT_FILE_TYPES:
        metadata = _validate_document(uploaded_file, suffix)
    else:
        raise AttachmentValidationError("Этот тип файла пока не поддерживается.")
    return AttachmentMetadata(
        original_name=original_name,
        content_type=metadata.content_type,
        size=metadata.size,
        kind=metadata.kind,
        duration_ms=metadata.duration_ms,
    )


def create_attachment(
    *,
    message: Message,
    uploaded_file: UploadedFile,
    metadata: AttachmentMetadata | None = None,
) -> Attachment:
    """Создаёт вложение только после полной проверки его содержимого."""
    if message.attachments.count() >= settings.ATTACHMENT_MAX_COUNT:
        raise AttachmentValidationError("К сообщению можно добавить не больше трёх файлов.")
    if metadata is None:
        metadata = validate_attachment(uploaded_file)
    return Attachment.objects.create(
        message=message,
        file=uploaded_file,
        original_name=metadata.original_name,
        content_type=metadata.content_type,
        size=metadata.size,
        kind=metadata.kind,
        duration_ms=metadata.duration_ms,
    )


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
                duration_ms=metadata.duration_ms,
            )
            for uploaded_file, metadata in checked_files
        ]
