import pytest


def pytest_collection_modifyitems(items):
    """项目内部接口说明。"""
    for item in items:
        if "/worker/" in str(item.fspath):
            item.add_marker(pytest.mark.xdist_group(name="process_spawning"))
