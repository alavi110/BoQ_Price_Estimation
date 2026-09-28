"""
Upload validation (T121).

The rules live here rather than in the route so the same checks can be applied
by a background task, a CLI importer, or a test, and so the route stays a
handler. Nothing in this module touches the file's bytes: it reads metadata and
returns a decision, because a validator that consumed the stream would leave the
parser with nothing and report the consequence as an empty workbook.

Every refusal is a :class:`UploadRejected` carrying the HTTP status the route
should use. Distinguishing 400 from 413 matters - one tells the client its file
is malformed, the other tells it the file is the right kind of file and too big,
and a well-behaved client can only act correctly on one of those.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

#: Longest name the filesystem will accept, in bytes. 255 is the ext4 and NTFS
#: limit; the name is truncated on a byte boundary so a multi-byte Persian
#: character is never cut in half.
MAX_FILENAME_BYTES = 255

#: Characters a name may not contain, because they are either path separators on
#: some platform or a header-injection primitive on every platform.
_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f/\\]")

#: Windows refuses to create these regardless of extension.
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: Used when a name reduces to nothing usable. Shared by every such name on
#: purpose: giving them each a distinct name would mean a caller who uploads
#: ``..`` twice gets two files, which is not what happened.
_FALLBACK_NAME = "upload.xlsx"


class UploadRejected(Exception):
    """A file that will not be accepted, with the status to answer with."""

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedUpload:
    """What the route is allowed to act on."""

    #: The name reduced to a bare, safe basename. Never ``..``-bearing.
    filename: str
    #: Lower-case, no dot.
    extension: str
    #: Bytes, or ``None`` when the client did not say.
    size: Optional[int]
    #: The declared content type. Recorded, not trusted - a browser's guess is
    #: not evidence of anything, and the extension check is the one that matters.
    content_type: Optional[str] = None

    @property
    def size_bytes(self) -> int:
        return self.size or 0


def safe_filename(raw: Optional[str]) -> str:
    """
    Reduce a client-supplied filename to something safe to join onto a path.

    Four steps, each closing a different gap:

    1. strip any directory component, in both POSIX and Windows form - the
       storage path is built by joining, so ``../../etc/cron.d/x`` would escape
       it;
    2. remove control characters, which are a header-injection primitive if the
       name is ever echoed into a ``Content-Disposition``, as it is on the export
       download;
    3. drop leading dots, so a name that is only dots cannot name a file at all;
    4. truncate to the filesystem's byte limit, and refuse a Windows device name.

    Non-ASCII is preserved deliberately. These are Iranian BoQ files and the name
    is frequently Persian; mangling it would be a regression presented as a
    security measure. The path is never used as a shell argument or an identifier,
    so the character set is not what makes it safe.
    """
    if not raw:
        return _FALLBACK_NAME

    # Split on separators *before* removing control characters, so a Windows path
    # is reduced by the same code as a POSIX one. Removing them first would join
    # the components into ``etcpasswd.xlsx`` - still outside the storage
    # directory's naming scheme, and no longer the name the caller recognises.
    name = re.split(r"[\\/]", str(raw).strip())[-1]

    name = _FORBIDDEN.sub("", name).strip()

    # Leading dots would create a hidden file, and a name of only dots is not a
    # name.
    name = name.lstrip(".")

    if not name:
        return _FALLBACK_NAME

    name = _truncate_preserving_extension(name)

    stem, dot, extension = name.partition(".")
    if stem.upper() in _RESERVED_STEMS:
        # ``CON.xlsx`` cannot be created on Windows: the write fails with an OS
        # error after validation has already passed, and the 500 blames the
        # server for a name the caller chose.
        name = f"file_{name}"

    return name or _FALLBACK_NAME


def _truncate_preserving_extension(name: str) -> str:
    """
    Shorten to the filesystem's byte limit without losing the extension.

    Without the special case, ``"x" * 400 + ".xlsx"`` truncates to 255 bytes of
    ``x`` with no extension at all - and then the extension check rejects a file
    the caller did nothing wrong with, for a name the caller cannot see. The
    budget is therefore split: the extension is kept whole and the stem absorbs
    the cut.

    The cut is made on a character boundary, because ``bytes.decode`` raises on
    a truncated multi-byte sequence and these names are frequently Persian.
    """
    if len(name.encode("utf-8")) <= MAX_FILENAME_BYTES:
        return name

    stem, dot, extension = name.rpartition(".")
    if not dot or len(extension) + 1 >= MAX_FILENAME_BYTES:
        # No usable extension to protect - a name that long with no dot is
        # truncated whole, and the extension check will refuse it afterwards.
        return _cut_on_character_boundary(name, MAX_FILENAME_BYTES)

    keep = MAX_FILENAME_BYTES - len(extension.encode("utf-8")) - 1
    stem = _cut_on_character_boundary(stem, keep)
    if not stem:
        return f"upload.{extension}"
    return f"{stem}.{extension}"


def _cut_on_character_boundary(name: str, limit: int) -> str:
    """The longest prefix of ``name`` that is at most ``limit`` UTF-8 bytes."""
    encoded = name.encode("utf-8")
    if len(encoded) <= limit:
        return name
    head = encoded[:limit]
    # Drop trailing continuation bytes (0b10xxxxxx) so the prefix is whole
    # characters rather than a valid prefix of one.
    while head and (head[-1] & 0xC0) == 0x80:
        head = head[:-1]
    return head.decode("utf-8", errors="ignore").strip()


def validate_upload(file: Any) -> ValidatedUpload:
    """
    Decide whether ``file`` will be accepted.

    Order matters only for the message a caller sees, not for the outcome: an
    oversized ``.txt`` is reported as a bad format, because a caller who renames
    it is not going to be satisfied by being told it is too big.
    """
    raw_name = getattr(file, "filename", None)
    if not raw_name:
        raise UploadRejected(
            "The upload has no filename. Send the workbook as a multipart "
            "field named 'file'."
        )

    name = str(raw_name).strip()
    lowered = name.lower()
    allowed = settings.ALLOWED_EXTENSIONS
    if not any(lowered.endswith(extension.lower()) for extension in allowed):
        raise UploadRejected(
            f"Invalid file format: {raw_name}. Only "
            f"{' and '.join(allowed)} files are accepted."
        )

    # ``size`` is absent when the client omits Content-Length for the part, which
    # some clients do. Treated as unknown rather than zero: reading it as zero
    # would refuse every upload from a client that leaves it out.
    size = getattr(file, "size", None)
    if size is None:
        declared = None
    else:
        try:
            declared = int(size)
        except (TypeError, ValueError):
            declared = None

    if declared == 0:
        raise UploadRejected(
            f"The uploaded file ({raw_name}) is empty. A BoQ workbook with no "
            f"content cannot be parsed."
        )

    limit = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if declared is not None and declared > limit:
        raise UploadRejected(
            f"The uploaded file is {declared // (1024 * 1024)}MB, which is over "
            f"the {settings.MAX_FILE_SIZE_MB}MB limit. Split the bill of "
            f"quantities into smaller files, or reduce the row range.",
            status_code=413,
        )

    safe_name = safe_filename(raw_name)
    extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""

    logger.info(
        "upload_validated",
        original=raw_name,
        stored_as=safe_name,
        size=declared,
        extension=extension,
    )

    return ValidatedUpload(
        filename=safe_name,
        extension=extension,
        size=declared,
        content_type=getattr(file, "content_type", None),
    )


__all__ = [
    "MAX_FILENAME_BYTES",
    "UploadRejected",
    "ValidatedUpload",
    "safe_filename",
    "validate_upload",
]
