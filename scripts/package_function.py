"""Сборка ZIP, который Terraform передаёт в Cloud Functions."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main():
    function_dir = Path(__file__).resolve().parents[1] / "function"
    output = Path(os.environ["FUNCTION_ZIP"])
    output.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary, "w") as archive:
            for name in ("handler.py", "source.py"):
                archive.writestr(ZipInfo(name), (function_dir / name).read_bytes(), compress_type=ZIP_DEFLATED)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
