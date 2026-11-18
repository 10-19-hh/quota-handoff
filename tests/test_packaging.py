"""T-009C: distribution layout and README command sections."""

import json
import re
import unittest

from quota_handoff.handoff import load_task

from tests import ROOT


def readme_section(title):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^### " + re.escape(title) + r".*?(?=^##)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"README section not found: {title}")
    return match.group(0)


class ManifestTests(unittest.TestCase):
    def test_sdist_includes_examples_docs_tests(self):
        lines = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        for required in ("recursive-include docs *.md", "recursive-include examples *.json",
                         "recursive-include tests *.py", "include LICENSE README.md pyproject.toml"):
            self.assertIn(required, lines)


class ReadmeSectionTests(unittest.TestCase):
    def test_install_only_commands_do_not_need_checkout_files(self):
        section = readme_section("After installing")
        commands = [line.strip() for line in section.splitlines() if line.strip().startswith("quota-handoff ")]
        self.assertGreaterEqual(len(commands), 3)
        for command in commands:
            self.assertNotIn("examples/", command)
        self.assertIn("quota-handoff status --demo", commands)

    def test_install_only_task_snippet_is_valid(self):
        snippet = re.search(r"```json\n(.*?)```", readme_section("After installing"), re.S).group(1)
        task = load_task(json.loads(snippet))
        self.assertTrue(task.objective)

    def test_checkout_section_uses_existing_examples(self):
        section = readme_section("From a repository checkout")
        for path in re.findall(r"(examples/[\w.-]+\.json)", section):
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
