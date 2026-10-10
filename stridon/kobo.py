"""Pick a book from a Kobo e-reader or the Kobo desktop app.

Only DRM-free books can be translated; protected books are reported and
skipped (Stridon does not remove DRM).
"""

import os
import platform
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


class KoboError(Exception):
    pass


@dataclass
class KoboBook:
    title: str
    author: str
    path: Path | None
    protected: bool


def _desktop_library() -> Path | None:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Kobo" / "Kobo Desktop Edition"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Kobo" / "Kobo Desktop Edition"
    else:
        base = Path.home() / ".local" / "share" / "Kobo" / "Kobo Desktop Edition"
    return base if (base / "Kobo.sqlite").is_file() else None


def _locate(mount: str | None) -> tuple[Path, Path, Path | None]:
    """Returns (database, kepub folder, device mount point or None)."""
    if mount:
        root = Path(mount)
        database = root / ".kobo" / "KoboReader.sqlite"
        if not database.is_file():
            raise KoboError(f"no Kobo library found in {root} (missing .kobo/KoboReader.sqlite)")
        return database, root / ".kobo" / "kepub", root
    library = _desktop_library()
    if library is None:
        raise KoboError("Kobo desktop app library not found; connect the e-reader and pass its folder to --kobo")
    return library / "Kobo.sqlite", library / "kepub", None


def list_books(mount: str | None) -> list[KoboBook]:
    database, kepub_dir, root = _locate(mount)
    # Work on a copy: the e-reader or the app may hold the database open
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "kobo.sqlite"
        shutil.copyfile(database, copy)
        connection = sqlite3.connect(copy)
        try:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            protected_ids = set()
            if "content_keys" in tables:
                protected_ids = {r[0] for r in connection.execute("SELECT DISTINCT volumeid FROM content_keys")}
            rows = connection.execute(
                "SELECT ContentID, Title, Attribution FROM content WHERE ContentType = 6 AND Title IS NOT NULL"
            ).fetchall()
        finally:
            connection.close()

    books = []
    for content_id, title, author in rows:
        path = None
        if content_id.startswith("file:///mnt/onboard/") and root is not None:
            path = root / content_id[len("file:///mnt/onboard/"):]
        elif (kepub_dir / content_id).is_file():
            path = kepub_dir / content_id
        if path is None or not path.is_file():
            continue
        books.append(KoboBook(title, author or "", path, content_id in protected_ids))
    return sorted(books, key=lambda b: b.title.lower())


def choose_book(mount: str | None, destination: Path = Path.cwd()) -> Path:
    """Show the library, ask for a number, and copy the chosen book."""
    books = list_books(mount)
    if not books:
        raise KoboError("no book found in the Kobo library")
    for number, book in enumerate(books, start=1):
        lock = "  [protected]" if book.protected else ""
        author = f" — {book.author}" if book.author else ""
        print(f"{number:3}: {book.title}{author}{lock}")
    answer = input("Book number to translate: ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(books):
        raise KoboError("invalid choice")
    book = books[int(answer) - 1]
    if book.protected:
        raise KoboError(f"'{book.title}' is protected by DRM and cannot be translated")
    target = destination / (re.sub(r"[^\w\s-]", "_", book.title).strip() + ".epub")
    shutil.copyfile(book.path, target)
    print(f"Book copied to {target}")
    return target
