from PIL import Image, ImageOps


AVATAR_SIZE = (300, 300)


def resize_avatar(image):
    """Подгоняет изображение под размер аватара."""

    image = Image.open(image)

    image = ImageOps.exif_transpose(image)

    if image.mode != "RGB":
        image = image.convert("RGB")

    return ImageOps.fit(
        image,
        AVATAR_SIZE,
        method=Image.Resampling.LANCZOS,
    )
