"""Entry point: python -m minas_webdav [probe|proxy|<cli-command>] ..."""
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "probe":
        from .probe import run

        return run(argv[1:])
    if argv and argv[0] == "proxy":
        from .proxy import run

        return run(argv[1:])
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
