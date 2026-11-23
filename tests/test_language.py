import unittest

from arf.language import detect, detect_declared, html_lang


def page(lang):
    return f'<!doctype html>\n<html dir="ltr" lang="{lang}"><head><meta charset="utf-8"></head><body lang="xx"></body></html>'


class DetectTest(unittest.TestCase):
    def test_en_us_on_israeli_host_is_en(self):
        self.assertEqual(detect(page("en-US"), None, "www.haaretz.co.il"), ("en", "html lang=en-US", "en-US"))

    def test_he_il_is_he(self):
        self.assertEqual(detect(page("he-IL"), None, "example.com"), ("he", "html lang=he-IL", "he-IL"))

    def test_missing_lang_on_co_il_is_he(self):
        self.assertEqual(detect("<html><body></body></html>", None, "www.lehamim.co.il"), ("he", "host www.lehamim.co.il (.il)", None))

    def test_missing_lang_on_com_is_en(self):
        self.assertEqual(detect("<html></html>", None, "example.com"), ("en", "default (no declared language)", None))
        self.assertEqual(detect(None, None, ""), ("en", "default (no declared language)", None))

    def test_other_declared_language_falls_back_to_en(self):
        self.assertEqual(detect(page("ru"), None, "www.9tv.co.il"), ("en", "unsupported:ru", "ru"))
        self.assertEqual(detect(page("ar-IL"), None, "example.co.il"), ("en", "unsupported:ar", "ar-IL"))

    def test_precedence_html_then_header_then_host(self):
        self.assertEqual(detect(page("en"), "he-IL", "x.co.il")[0], "en")
        self.assertEqual(detect("<html>", "he-IL, en", "example.com"), ("he", "content-language=he-IL", "he-IL"))
        self.assertEqual(detect("<html>", "fr", "x.co.il"), ("en", "unsupported:fr", "fr"))
        self.assertEqual(detect("<html>", "", "x.org.il")[0], "he")

    def test_html_lang_parsing(self):
        self.assertEqual(html_lang("<HTML LANG=en_GB class='a'>"), "en_GB")
        self.assertEqual(html_lang("<html xml:lang='he'>"), "he")
        self.assertIsNone(html_lang('<html lang="">'))
        self.assertIsNone(html_lang('<html data-lang="he">'))
        self.assertIsNone(html_lang('<html lang="not a tag">'))
        self.assertIsNone(html_lang('<body lang="he">'))
        self.assertEqual(html_lang('<html lang="iw">'), "iw")
        self.assertEqual(detect('<html lang="iw">', None, "example.com")[0], "he")

    def test_detect_declared_uses_extracted_value(self):
        self.assertEqual(detect_declared("en-US", None, "www.timesofisrael.com"), ("en", "html lang=en-US", "en-US"))
        self.assertEqual(detect_declared(None, None, "www.elal.co.il")[0], "he")


if __name__ == "__main__":
    unittest.main()
