import rexecop


def test_package_import() -> None:
    assert rexecop.__version__


def test_version_is_pre_alpha() -> None:
    assert rexecop.__version__ == "0.11.0a0"
