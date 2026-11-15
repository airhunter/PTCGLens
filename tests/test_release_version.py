import unittest

from release_version import ReleaseVersion


class ReleaseVersionTest(unittest.TestCase):
    def test_alpha_is_consistent_across_public_and_numeric_versions(self):
        version = ReleaseVersion.parse("0.1.0a1")
        self.assertEqual(version.public, "0.1.0-alpha.1")
        self.assertEqual(version.tag, "v0.1.0-alpha.1")
        self.assertEqual(version.base, "0.1.0")
        self.assertEqual(version.windows, (0, 1, 0, 1001))
        self.assertEqual(str(version.build_number), "1001")

    def test_native_version_order_preserves_release_stage_and_sequence(self):
        versions = [ReleaseVersion.parse(v) for v in ("0.1.0a1", "0.1.0a2", "0.1.0b1", "0.1.0rc1", "0.1.0")]
        self.assertEqual([v.build_number for v in versions], [1001, 1002, 2001, 3001, 4000])
        self.assertEqual(versions[-1].public, "0.1.0")

    def test_ambiguous_or_unsupported_versions_fail_before_packaging(self):
        for value in ("v0.1.0", "0.1.0-alpha.1", "0.1", "01.1.0", "0.1.0a0", "0.1.0a1000",
                      "0.1.0.post1", "0.1.0+local", "0.1.0dev1", "70000.1.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ReleaseVersion.parse(value)
