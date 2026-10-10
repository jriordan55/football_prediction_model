"""Build the current NFL slate the prop desk reads."""
from __future__ import annotations

from nfl_open_prop.build_cfb import build


if __name__ == "__main__":
    build("nfl")
