"""Backend entrypoint - routers are registered in app/__init__.py"""

from app import app


def main() -> None:
    """Local smoke entrypoint for quick import/run checks."""
    print(f"Recursia backend app ready: {app.title}")


if __name__ == "__main__":
    main()
