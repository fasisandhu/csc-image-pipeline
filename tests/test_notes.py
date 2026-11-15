from studio.notes import plain_warning

CATALOGUE = "Some catalogue details for this style are still being confirmed with the factory."


def test_plain_warnings():
    assert plain_warning("catalogue pullover-oversized: unconfirmed rib width") == CATALOGUE
    assert plain_warning("catalogue zip: zip_type missing") == CATALOGUE
    assert plain_warning("cast Maya: no approved reference sheet yet, face consistency relies on the description") == (
        "Maya has no approved reference photo yet, so the face may vary between images.")
    assert plain_warning("paragraph prompt is 260 words, outside 120-220") == (
        "The prompt is a little long; the result may ignore a detail.")
    assert plain_warning("set style_block differs from brand/brand.json") is None
    assert plain_warning("reference brand/designs/x_ref.png not found (not yet approved?), skipped") == (
        "reference x_ref.png not found (not yet approved?), skipped")
    assert plain_warning("left chest: max size not set in catalogue, size unchecked") == (
        "left chest: max size not set in catalogue, size unchecked")
