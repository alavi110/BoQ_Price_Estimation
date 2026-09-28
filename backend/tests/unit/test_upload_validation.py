"""
Upload validation (T121).

Every rule here is one the server can be made to skip by a crafted filename, so
each is tested against the *hostile* case rather than the happy one. The
particular failure being guarded against is a 500: an unhandled type error on the
upload path means a stack trace in the log and a 500 to the caller, when the
right answer is a 400 naming the actual problem.
"""
import io

import pytest

from src.validators.upload import (
    UploadRejected,
    safe_filename,
    validate_upload,
)


class FakeUpload:
    """
    An ``UploadFile``-alike: only what the validator reads.

    Attributes are set on the instance, not the class, so a test can delete one
    of them to model a client that omitted it - which is the case the
    ``size``-is-absent rule exists for.
    """

    def __init__(self, filename="boq.xlsx", content=b"PK\x03\x04payload"):
        self.filename = filename
        self.size = len(content)
        self.content_type = "application/vnd.ms-excel"
        self._content = content


def make_file(name="boq.xlsx", content=b"PK\x03\x04payload"):
    return FakeUpload(filename=name, content=content)


# --------------------------------------------------------------------------- #
# Extension
# --------------------------------------------------------------------------- #
class TestExtension:
    @pytest.mark.parametrize("name", ["boq.xlsx", "boq.xls", "BOQ.XLSX", "a.b.xlsx"])
    def test_a_workbook_extension_is_accepted(self, name):
        assert validate_upload(make_file(name)).extension == name.split(".")[-1].lower()

    @pytest.mark.parametrize(
        "name",
        [
            "boq.xlsx.exe",   # a double extension
            "boq.exe",
            "boq.csv",
            "boq.xlsm",       # macro-enabled, not in the allow-list
            "boq",
            "boq.",
            "boq.txt",
        ],
    )
    def test_anything_else_is_refused(self, name):
        """
        ``boq.xlsx.exe`` is the one worth pausing on: ``endswith(".xlsx")`` says
        no, and a check written as ``".xlsx" in name`` would say yes and hand a
        Windows machine an executable.
        """
        with pytest.raises(UploadRejected):
            validate_upload(make_file(name))

    def test_a_refusal_names_the_offending_file(self):
        with pytest.raises(UploadRejected) as caught:
            validate_upload(make_file("secret.xls.exe"))

        assert "secret.xls.exe" in str(caught.value)

    def test_a_refusal_lists_what_is_accepted(self):
        with pytest.raises(UploadRejected) as caught:
            validate_upload(make_file("boq.csv"))

        assert ".xlsx" in str(caught.value)
        assert ".xls" in str(caught.value)

    def test_a_missing_filename_is_refused_rather_than_crashing(self):
        """
        ``file.filename`` is client-supplied and FastAPI does not require it to
        exist. ``None.endswith(...)`` is an AttributeError, which is a 500.
        """
        with pytest.raises(UploadRejected):
            validate_upload(make_file(name=None))

    def test_an_empty_filename_is_refused(self):
        with pytest.raises(UploadRejected):
            validate_upload(make_file(name=""))


# --------------------------------------------------------------------------- #
# Size
# --------------------------------------------------------------------------- #
class TestSize:
    def test_a_file_at_the_limit_is_accepted(self):
        from src.core.config import settings

        upload = make_file(content=b"x" * 10)
        upload.size = settings.MAX_FILE_SIZE_MB * 1024 * 1024

        assert validate_upload(upload).size == upload.size

    def test_one_byte_over_the_limit_is_refused(self):
        from src.core.config import settings

        upload = make_file(content=b"x" * 10)
        upload.size = settings.MAX_FILE_SIZE_MB * 1024 * 1024 + 1

        with pytest.raises(UploadRejected) as caught:
            validate_upload(upload)

        assert str(settings.MAX_FILE_SIZE_MB) in str(caught.value)

    def test_an_oversized_upload_is_a_413_not_a_400(self):
        """
        A 413 is the status that tells a client the request was well-formed and
        the *size* is what to change, so a well-behaved client can act on it.
        Collapsing it into 400 makes the file look malformed.
        """
        from src.core.config import settings

        upload = make_file()
        upload.size = settings.MAX_FILE_SIZE_MB * 1024 * 1024 + 1

        with pytest.raises(UploadRejected) as caught:
            validate_upload(upload)

        assert caught.value.status_code == 413

    def test_a_refusal_says_what_the_size_actually_was(self):
        upload = make_file()
        upload.size = 999 * 1024 * 1024

        with pytest.raises(UploadRejected) as caught:
            validate_upload(upload)

        assert "999" in str(caught.value)

    def test_an_empty_file_is_refused(self):
        """
        A zero-byte upload is not a workbook, and ``openpyxl`` on one raises a
        zip error that would surface as a 500 from three frames down.
        """
        upload = make_file(content=b"")
        upload.size = 0

        with pytest.raises(UploadRejected) as caught:
            validate_upload(upload)

        assert "empty" in str(caught.value).lower()

    def test_a_missing_size_is_treated_as_unknown_not_as_zero(self):
        """
        ``UploadFile.size`` is optional in the multipart form. Reading it as
        ``0`` would refuse every upload from a client that omits it, which is
        exactly what ``curl -F file=@x`` produces on some versions.
        """
        upload = make_file()
        del upload.size

        assert validate_upload(upload).size is None


# --------------------------------------------------------------------------- #
# Filename safety
# --------------------------------------------------------------------------- #
class TestFilenameSafety:
    @pytest.mark.parametrize(
        "supplied,expected",
        [
            ("boq.xlsx", "boq.xlsx"),
            ("  boq.xlsx  ", "boq.xlsx"),
            ("boq (copy 2).xlsx", "boq (copy 2).xlsx"),
        ],
    )
    def test_an_ordinary_name_survives(self, supplied, expected):
        assert safe_filename(supplied) == expected

    def test_a_path_is_reduced_to_its_basename(self):
        """
        The name is used to build a path under the storage directory. Taken
        literally, ``../../etc/cron.d/x.xlsx`` writes outside it - and the
        upload directory is the one place a caller gets to choose a filename.
        """
        assert safe_filename("../../etc/passwd.xlsx") == "passwd.xlsx"
        assert safe_filename("/etc/passwd.xlsx") == "passwd.xlsx"

    def test_a_windows_path_is_reduced_too(self):
        assert safe_filename(r"..\..\windows\system32\evil.xlsx") == "evil.xlsx"
        assert safe_filename(r"C:\Users\admin\secret.xlsx") == "secret.xlsx"

    def test_a_traversal_that_survives_reduction_still_loses_its_dots(self):
        """
        Belt and braces. Even if the separator handling is bypassed, a name that
        is only dots cannot name a file.
        """
        for hostile in ("..", ".", "../", "..\\", "...", "...."):
            result = safe_filename(hostile)
            assert ".." not in result
            assert result not in (".", "")

    def test_a_name_that_reduces_to_nothing_gets_a_default(self):
        """
        Two uploads of ``..`` would otherwise collide on the same storage path,
        and the second would silently overwrite the first.
        """
        assert safe_filename("..") == safe_filename("..")
        assert safe_filename("..")

    def test_a_control_character_in_a_name_is_removed(self):
        """
        A newline in a filename is a header-injection primitive if the name is
        ever echoed into a ``Content-Disposition``, which it is on the export
        download.
        """
        result = safe_filename("boq\r\nSet-Cookie: x.xlsx")

        assert "\r" not in result
        assert "\n" not in result

    def test_a_very_long_name_is_truncated(self):
        """
        Over 255 bytes the write fails on ext4, and the 500 blames the server
        for a name the caller chose.
        """
        result = safe_filename("x" * 400 + ".xlsx")

        assert len(result.encode("utf-8")) <= 255
        assert result.endswith(".xlsx")

    def test_a_persian_name_is_preserved(self):
        """
        These are Iranian BoQ files; the name is often Persian. Stripping
        non-ASCII would be a regression, not a security measure.
        """
        assert safe_filename("فهرست_بها.xlsx") == "فهرست_بها.xlsx"

    def test_a_reserved_windows_device_name_is_not_produced(self):
        """
        ``CON.xlsx`` cannot be created on Windows, so the upload would fail with
        an OS error after validation passed.
        """
        result = safe_filename("CON.xlsx")

        assert not result.upper().startswith(("CON", "PRN", "AUX", "NUL"))


# --------------------------------------------------------------------------- #
# The validated result
# --------------------------------------------------------------------------- #
class TestResult:
    def test_the_result_carries_the_reduced_name(self):
        """
        The caller must not be able to read the original name back out and use
        it, or the reduction is documentation rather than enforcement.
        """
        result = validate_upload(make_file("../../evil.xlsx"))

        assert result.filename == "evil.xlsx"
        assert ".." not in result.filename

    def test_the_result_reports_the_extension_without_the_dot(self):
        assert validate_upload(make_file("boq.xlsx")).extension == "xlsx"

    def test_the_result_reports_size_in_bytes(self):
        upload = make_file(content=b"12345")
        upload.size = 5

        assert validate_upload(upload).size == 5


def test_the_stream_is_untouched_by_validation():
    """
    Validation reads metadata only. A validator that consumed the stream would
    leave the parser with nothing, and the failure would surface as a confusing
    empty-workbook error rather than as a validation bug.
    """
    upload = make_file(content=b"payload")

    validate_upload(upload)

    assert upload._content == b"payload"
