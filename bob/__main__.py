from __future__ import annotations

import os
import threading


def _entry() -> str:
    # Default to CLI; allow forcing UI.
    return os.getenv("BOB_ENTRY", "cli").strip().lower()


def _bool_env(name: str, default: str = "1") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _launch_ui_background() -> None:
    from bob.ui.gradio_app import build_app

    app = build_app()
    app.launch(prevent_thread_lock=True)


if __name__ == "__main__":
    if _entry() == "cli":
        from bob.cli import main

        if _bool_env("BOB_UI", "1"):
            t = threading.Thread(target=_launch_ui_background, daemon=True)
            t.start()

        main()
    else:
        from bob.ui.gradio_app import main

        main()
