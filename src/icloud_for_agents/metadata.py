"""Public project identity shared by MCP and package metadata checks.

Keep these values in sync with [project] and [project.urls] in pyproject.toml.
The package is usable away from the checkout; never read pyproject.toml at runtime.
"""

SERVER_NAME = "iCloud for Agents"
SERVER_DESCRIPTION = (
    "Connect AI assistants to iCloud Mail, Calendar and Contacts, plus Apple Notes "
    "and Reminders on macOS, with explicit access controls."
)
WEBSITE_URL = "https://jasonarend.com/news/icloud-for-agents-apple-ai-assistants/"
REPOSITORY_URL = "https://github.com/jasonarend/icloud-for-agents"
DOCUMENTATION_URL = REPOSITORY_URL + "#readme"
ISSUES_URL = REPOSITORY_URL + "/issues"
CHANGELOG_URL = REPOSITORY_URL + "/blob/main/CHANGELOG.md"
