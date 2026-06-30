from pathlib import Path

from boris_mvp.app import create_app


app = create_app(Path.cwd())
