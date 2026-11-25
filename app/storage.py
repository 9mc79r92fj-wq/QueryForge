import hashlib
import os
import codecs

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile, HTTPException

from app.config import get_settings


settings = get_settings()

ALLOWED_EXTENSIONS = {".pdf", ".txt"}

BUFFER_SIZE = 1024 * 1024


def save_upload(
    file: UploadFile
) -> tuple[str, str, str]:

    filename = (file.filename or "").replace("\\", "/")
    filename = filename.rsplit("/", 1)[-1]

    extension = Path(filename).suffix.lower()

    if not filename or len(filename) > 255:
        raise HTTPException(400, "Invalid filename")

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            415,
            "Only PDF and TXT files are supported"
        )

    upload_dir = settings.data_dir / "uploads"

    upload_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    max_size = settings.max_upload_mb * 1024 * 1024

    digest = hashlib.sha256()

    total_size = 0
    temp_path = None

    decoder = (
        codecs.getincrementaldecoder("utf-8-sig")()
        if extension == ".txt"
        else None
    )

    try:

        with NamedTemporaryFile(
            dir=upload_dir,
            delete=False
        ) as temp:

            temp_path = Path(temp.name)

            while chunk := file.file.read(BUFFER_SIZE):

                if total_size == 0 and extension == ".pdf":
                    if not chunk.startswith(b"%PDF-"):
                        raise HTTPException(
                            400,
                            "Invalid PDF header"
                        )

                total_size += len(chunk)

                if total_size > max_size:
                    raise HTTPException(
                        413,
                        "File exceeds upload size limit"
                    )

                if decoder is not None:
                    decoder.decode(chunk)

                    if b"\x00" in chunk:
                        raise HTTPException(
                            400,
                            "Invalid text file"
                        )

                digest.update(chunk)

                temp.write(chunk)

            if decoder is not None:
                decoder.decode(b"", final=True)

        if total_size == 0:
            raise HTTPException(
                400,
                "Empty file"
            )

        content_hash = digest.hexdigest()

        final_path = upload_dir / content_hash

        os.replace(temp_path, final_path)

        relative_path = final_path.relative_to(
            settings.data_dir
        ).as_posix()

        return filename, content_hash, relative_path

    except UnicodeDecodeError:

        raise HTTPException(
            400,
            "TXT file must use UTF-8 encoding"
        )

    finally:

        if temp_path is not None:
            temp_path.unlink(missing_ok=True)