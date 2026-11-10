"""Public metadata stays in sync without reading account data or using services."""

from pathlib import Path
import tomllib
import unittest

from icloud_for_agents.metadata import (
    CHANGELOG_URL, DOCUMENTATION_URL, ISSUES_URL, REPOSITORY_URL,
    SERVER_DESCRIPTION, SERVER_NAME, WEBSITE_URL,
)
from icloud_for_agents.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def assert_server_metadata(info):
    """Check actual initialize/discover metadata independently of client branding."""
    assert info["name"] == SERVER_NAME
    assert info["title"] == SERVER_NAME
    assert info["version"] == __version__
    assert info["description"] == SERVER_DESCRIPTION
    assert info["websiteUrl"] == WEBSITE_URL


class MetadataTests(unittest.TestCase):
    def test_package_and_runtime_metadata_match(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["name"], "icloud-for-agents")
        self.assertEqual(project["version"], __version__)
        self.assertEqual(project["description"], SERVER_DESCRIPTION)
        self.assertEqual(project["urls"], {
            "Homepage": WEBSITE_URL,
            "Repository": REPOSITORY_URL,
            "Documentation": DOCUMENTATION_URL,
            "Issues": ISSUES_URL,
            "Changelog": CHANGELOG_URL,
        })

    def test_description_covers_all_five_services(self):
        for service in ("Mail", "Calendar", "Contacts", "Notes", "Reminders"):
            self.assertIn(service, SERVER_DESCRIPTION)
        self.assertIn("macOS", SERVER_DESCRIPTION)
        self.assertIn("explicit access controls", SERVER_DESCRIPTION)

    def test_public_homepage_and_source_are_distinct(self):
        self.assertEqual(WEBSITE_URL,
                         "https://jasonarend.com/news/icloud-for-agents-apple-ai-assistants/")
        self.assertEqual(REPOSITORY_URL, "https://github.com/jasonarend/icloud-for-agents")
