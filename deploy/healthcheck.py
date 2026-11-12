"""Check gateway readiness; channel delivery still needs end-to-end validation."""
import json
import urllib.request


def main() -> None:
    with urllib.request.urlopen('http://127.0.0.1:18790/health', timeout=3) as response:
        payload = json.load(response)
    if payload.get('ready') is not True:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
