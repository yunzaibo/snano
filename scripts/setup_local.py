"""Create private local configuration from public templates without overwriting."""
from pathlib import Path
import os


def main():
    root = Path(__file__).resolve().parents[1]
    for template, target in (
        (".env.example", ".env.internal"),
        ("configs/sources.shared.example.yaml", "configs/sources.internal.yaml"),
    ):
        destination = root / target
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(f"保留已有配置：{target}")
            continue
        with os.fdopen(fd, "wb") as stream:
            stream.write((root / template).read_bytes())
        print(f"已从模板创建：{target}")


if __name__ == "__main__":
    main()
