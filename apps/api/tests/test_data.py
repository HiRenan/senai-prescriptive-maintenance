"""Import tests for data dependencies and the package boundary."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module_name",
    (
        "pandas",
        "pandera",
        "pyarrow",
        "prescriptive_maintenance.data",
    ),
)
def test_data_module_import(module_name: str) -> None:
    module = import_module(module_name)

    assert module.__name__ == module_name
