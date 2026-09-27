"""Tests for packaging config (pyproject.toml).

Regression coverage for the 2026-09-26 production incident:
persistence/postgres_migrations/*.sql was silently excluded from `pip
install .`'s installed package (Railway's real, non-editable build
command) because that directory has no __init__.py and setuptools'
package auto-discovery only finds real packages — so
postgres_migrate.MIGRATIONS_DIR.glob("*.sql") found nothing at all on
Railway, with no error. Fixed via [tool.setuptools.package-data] in
pyproject.toml; see that file's own comment for the full story.

These are lightweight config-content checks, not a real build — this
suite runs with no network access and no wheel/build tooling installed
(the project's own venv deliberately doesn't depend on `build`/`wheel`
for anything other than pip's own build-isolation). The real proof that
0004/0005 are discoverable through Railway's exact runtime path is a
manual clean-venv `pip install .` reproduction (see the 2026-09-26
CHANGELOG entry) — these tests exist to catch a future regression of the
same *kind* (a migration file the packaging config silently stops
covering) between those manual checks.
"""

import re

from content_automation.config import REPO_ROOT

PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MIGRATIONS_DIR = REPO_ROOT / "src" / "content_automation" / "persistence" / "postgres_migrations"


def test_pyproject_declares_package_data_for_postgres_migrations():
    """Fails loudly if this declaration is ever removed or narrowed --
    exactly the class of silent change that broke production before."""
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = re.search(
        r'\[tool\.setuptools\.package-data\]\s*\n'
        r'"content_automation\.persistence"\s*=\s*\[([^\]]*)\]',
        text,
    )
    assert match, (
        "pyproject.toml is missing [tool.setuptools.package-data] for "
        "content_automation.persistence -- postgres_migrations/*.sql would "
        "silently disappear from `pip install .` again."
    )
    assert "postgres_migrations/*.sql" in match.group(1)


def test_every_existing_migration_file_matches_the_declared_pattern():
    """The declared pattern ("postgres_migrations/*.sql") is a single
    path segment -- if a future migration ever lands in a nested
    subdirectory instead, the declared pattern would silently stop
    covering it, reproducing the same failure mode this fix addresses."""
    all_sql_files = sorted(MIGRATIONS_DIR.rglob("*.sql"))
    assert all_sql_files, "expected at least one committed migration file"
    for path in all_sql_files:
        assert path.parent == MIGRATIONS_DIR, (
            f"{path} is not directly under postgres_migrations/ -- the declared "
            f"package-data pattern 'postgres_migrations/*.sql' would not include it"
        )
