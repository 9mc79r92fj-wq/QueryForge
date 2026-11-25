import os
import unicodedata

from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid5

from pydantic import Field, TypeAdapter

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter
)

from app.config import get_settings
from app.schemas import Citation


settings = get_settings()

MAX_PAGES = 500
MAX_CHARACTERS = 2_000_000


class ChunkRecord(Citation):

    text: str = Field(min_length=1)


chunk_adapter = TypeAdapter(list[ChunkRecord])


def clean_text(text: str) -> str:

    text = unicodedata.normalize("NFC", text)

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace("\x00", "")

    return text.strip()


def parse_document(
    path: Path,
    filename: str
) -> list[tuple[int | None, str]]:

    extension = Path(filename).suffix.lower()

    pages = []

    if extension == ".txt":

        content = clean_text(
            path.read_text(encoding="utf-8-sig")
        )

        if content:
            pages.append((None, content))

    elif extension == ".pdf":

        try:

            with path.open("rb") as file:

                reader = PdfReader(file)

                if reader.is_encrypted:
                    raise ValueError("Encrypted PDF")

                if len(reader.pages) > MAX_PAGES:
                    raise ValueError("Too many PDF pages")

                total = 0

                for number, page in enumerate(
                    reader.pages,
                    start=1
                ):

                    content = clean_text(
                        page.extract_text() or ""
                    )

                    total += len(content)

                    if total > MAX_CHARACTERS:
                        raise ValueError("Document too large")

                    if content:
                        pages.append((number, content))

        except PdfReadError as exc:

            raise ValueError(
                "Invalid PDF document"
            ) from exc

    else:

        raise ValueError("Unsupported document type")

    if not pages:

        raise ValueError("No extractable text")

    if sum(len(text) for _, text in pages) > MAX_CHARACTERS:

        raise ValueError("Document too large")

    return pages


def split_pages(
    pages: list[tuple[int | None, str]],
    document_id: str,
    version_id: str,
    filename: str
) -> list[ChunkRecord]:

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=[
            "\n\n",
            "\n",
            "。",
            "！",
            "？",
            "；",
            "，",
            " ",
            ""
        ],
        length_function=len
    )

    document_uuid = UUID(document_id)
    version_uuid = UUID(version_id)

    chunks = []

    for page_number, content in pages:

        for piece in splitter.split_text(content):

            piece = piece.strip()

            if not piece:
                continue

            index = len(chunks)

            chunk_id = uuid5(
                version_uuid,
                f"{index}:{piece}"
            )

            chunks.append(
                ChunkRecord(
                    document_id=document_uuid,
                    version_id=version_uuid,
                    chunk_id=chunk_id,
                    filename=filename,
                    page=page_number,
                    chunk_index=index,
                    text=piece
                )
            )

    if not chunks:

        raise ValueError("No valid document chunks")

    return chunks


def save_chunks(
    data_dir: Path,
    version_id: str,
    chunks: list[ChunkRecord]
) -> Path:

    directory = data_dir / "chunks"

    directory.mkdir(
        parents=True,
        exist_ok=True
    )

    version_uuid = UUID(version_id)

    target = directory / f"{version_uuid}.json"

    temp_path = None

    try:

        with NamedTemporaryFile(
            dir=directory,
            delete=False
        ) as temp:

            temp_path = Path(temp.name)

            temp.write(
                chunk_adapter.dump_json(
                    chunks,
                    indent=2
                )
            )

        os.replace(temp_path, target)

        return target

    finally:

        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def load_chunks(
    data_dir: Path,
    version_id: str
) -> list[ChunkRecord]:

    version_uuid = UUID(version_id)

    path = (
        data_dir
        / "chunks"
        / f"{version_uuid}.json"
    )

    return chunk_adapter.validate_json(
        path.read_bytes()
    )