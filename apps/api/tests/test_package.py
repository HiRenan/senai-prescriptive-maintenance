"""Tests for the installable package metadata."""

from importlib.metadata import metadata, version

import prescriptive_maintenance

DISTRIBUTION_NAME = "prescriptive-maintenance-api"


def test_package_import_and_public_metadata() -> None:
    package_metadata = metadata(DISTRIBUTION_NAME)

    assert prescriptive_maintenance.__name__ == "prescriptive_maintenance"
    assert prescriptive_maintenance.__version__ == version(DISTRIBUTION_NAME)
    assert package_metadata["Name"] == DISTRIBUTION_NAME
    assert package_metadata["Version"] == "0.1.0"
    assert package_metadata["Author-email"] == (
        "Renan Mocelin <renanryuakame@gmail.com>"
    )
