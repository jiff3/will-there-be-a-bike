"""One-off release migration with an optional separate owner credential."""

import os


def main() -> None:
    owner = os.environ.pop("MIGRATION_DATABASE_URL", None)
    if owner:
        os.environ["DATABASE_URL"] = owner
    # Settings/engine must be imported only AFTER selecting the release credential.
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    main()
