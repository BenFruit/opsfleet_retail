import re
import unittest

from support.paths import REPO_ROOT


class DocumentationTests(unittest.TestCase):
    def test_diagrams_embedded_in_the_design_match_their_sources(self):
        design = (REPO_ROOT / "HLD" / "PRODUCTION_HLD.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```mermaid\n(.*?)\n```", design, re.S)
        self.assertGreaterEqual(len(blocks), 3)
        for name in ("production-hld.mmd", "request-sequence.mmd", "learning-loop.mmd"):
            source = (REPO_ROOT / "HLD" / name).read_text(encoding="utf-8").rstrip("\n")
            with self.subTest(diagram=name):
                self.assertIn(source, blocks)

    def test_relative_markdown_links_resolve(self):
        # Published documentation only; local working notes are not in the repository.
        for doc in [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "HLD").glob("*.md"))]:
            text = doc.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                with self.subTest(doc=doc.name, target=target):
                    self.assertTrue((doc.parent / target).exists())
