"""Logo / welcome / installer images exist, have the right sizes and stay small."""
import unittest
from pathlib import Path

from PIL import Image

from vx7khata import paths

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "packaging" / "installer"


class TestBrandingAssets(unittest.TestCase):
    def test_logo_is_square_transparent_and_small(self):
        p = paths.asset_path("logo.png")
        im = Image.open(p)
        self.assertEqual(im.size[0], im.size[1])
        self.assertEqual(im.mode, "RGBA")
        self.assertEqual(im.getpixel((0, 0))[3], 0)        # clean transparent corner, no dark halo
        self.assertLess(p.stat().st_size, 600_000)

    def test_welcome_background_is_16_9_and_optimised(self):
        p = paths.asset_path("welcome_bg.jpg")
        w, h = Image.open(p).size
        self.assertAlmostEqual(w / h, 16 / 9, places=2)
        self.assertLess(p.stat().st_size, 300_000)

    def test_installer_images(self):
        for name, size in (("wizard_164.bmp", (164, 314)), ("wizard_246.bmp", (246, 472)),
                           ("wizard_328.bmp", (328, 628)), ("small_55.bmp", (55, 55)), ("small_110.bmp", (110, 110))):
            self.assertEqual(Image.open(INSTALLER / name).size, size, name)
        ico = Image.open(INSTALLER / "app.ico")
        self.assertIn((256, 256), ico.info.get("sizes", {(256, 256)}))

    def test_installer_script_uses_them(self):
        iss = (ROOT / "packaging" / "installer.iss").read_text()
        for needle in ("SetupIconFile", "WizardImageFile", "WizardSmallImageFile"):
            self.assertIn(needle, iss)


if __name__ == "__main__":
    unittest.main()
