import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_diagrams_embedded_in_the_design_match_their_sources(self):
        design = (ROOT / "HLD" / "PRODUCTION_HLD.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```mermaid\n(.*?)\n```", design, re.S)
        self.assertGreaterEqual(len(blocks), 3)
        for name in ("production-hld.mmd", "request-sequence.mmd", "learning-loop.mmd"):
            source = (ROOT / "HLD" / name).read_text(encoding="utf-8").rstrip("\n")
            with self.subTest(diagram=name):
                self.assertIn(source, blocks)

    def test_relative_markdown_links_resolve(self):
        for doc in [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "HLD").glob("*.md"))]:
            text = doc.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                with self.subTest(doc=doc.name, target=target):
                    self.assertTrue((doc.parent / target).exists())


if __name__ == "__main__":
    unittest.main()
