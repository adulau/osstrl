import unittest
from osstrl import CriterionResult, compute_osstrl, parse_repo, semantic_version


class TestOSSTRL(unittest.TestCase):
    def test_parse_repo(self):
        self.assertEqual(parse_repo("MISP/MISP"), ("MISP", "MISP"))
        self.assertEqual(parse_repo("https://github.com/MISP/MISP"), ("MISP", "MISP"))

    def test_semver(self):
        self.assertEqual(semantic_version("v2.5.42"), (2, 5, 42))
        self.assertIsNone(semantic_version("release-latest"))

    def test_gates_cap_high_score(self):
        criteria = [CriterionResult("x", "Development", "x", 100, 1.0, ["fixture"])]
        aux = {
            "license": True,
            "readme": True,
            "commits_365d": 100,
            "pushed_days": 5,
            "ci": True,
            "tests": True,
            "contributors": 3,
            "release_stats": {"stable_releases": 10, "recent_24m": 10, "latest_release_days": 5},
            "age_years": 6,
            "security_policy": True,
            "governance": True,
            "code_of_conduct": True,
        }
        result = compute_osstrl(criteria, aux)
        self.assertEqual(result["raw_osstrl_from_score"], 9)
        self.assertLessEqual(result["osstrl"], 5)


if __name__ == "__main__":
    unittest.main()
