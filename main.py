from pathlib import Path
import os


def load_env(path: str = ".env") -> None:
    env_path = Path(__file__).with_name(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


if __name__ == "__main__":
    load_env()
    from bot import main

    main()