from photostow.gui import make_command


def test_make_command_preserves_setting_values() -> None:
    assert make_command(
        "missing",
        {
            "OXYGEN_HOST": "oxygen",
            "PHOTOS_LIBRARY": "/Users/test/Photos Library.photoslibrary",
        },
    ) == [
        "make",
        "missing",
        "OXYGEN_HOST=oxygen",
        "PHOTOS_LIBRARY=/Users/test/Photos Library.photoslibrary",
    ]
