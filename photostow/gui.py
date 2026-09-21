from __future__ import annotations

import queue
import subprocess
import threading
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_command(target: str, values: dict[str, str]) -> list[str]:
    return ["make", target, *(f"{key}={value}" for key, value in values.items())]


class PhotostowGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.running = False
        self.buttons: list[ttk.Button] = []

        root.title("photostow")
        root.minsize(720, 560)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.host = tk.StringVar(value="oxygen")
        self.archive = tk.StringVar(value="/var/services/photo")
        self.library = tk.StringVar(
            value=str(Path.home() / "Pictures" / "Photos Library.photoslibrary")
        )
        self.review = tk.StringVar(value="review")
        self.status = tk.StringVar(value="Ready")
        self.summary = tk.StringVar()

        settings = ttk.LabelFrame(root, text="Settings", padding=10)
        settings.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        settings.columnconfigure(1, weight=1)
        fields = (
            ("Oxygen host", self.host),
            ("Archive root", self.archive),
            ("Photos library", self.library),
            ("Review directory", self.review),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(settings, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 8)
            )
            ttk.Entry(settings, textvariable=variable).grid(
                row=row, column=1, sticky="ew", pady=2
            )

        actions = ttk.LabelFrame(root, text="Workflow", padding=10)
        actions.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        actions.columnconfigure((0, 1, 2), weight=1)
        action_specs = (
            ("Refresh ledger (new paths)", self.update_ledger),
            ("Find missing (refresh first)", self.find_missing),
            ("Build review", self.stage_review),
            ("Open review in Finder", self.open_review),
            ("Archive reviewed files", self.archive_reviewed),
        )
        for index, (label, command) in enumerate(action_specs):
            button = ttk.Button(actions, text=label, command=command)
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
            self.buttons.append(button)

        ttk.Label(root, textvariable=self.summary).grid(
            row=2, column=0, sticky="w", padx=12, pady=3
        )

        output_frame = ttk.LabelFrame(root, text="Output", padding=5)
        output_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.output = tk.Text(output_frame, wrap="word", height=16, state="disabled")
        scrollbar = ttk.Scrollbar(
            output_frame, orient="vertical", command=self.output.yview
        )
        self.output.configure(yscrollcommand=scrollbar.set)
        self.output.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        ttk.Label(root, textvariable=self.status, anchor="w").grid(
            row=4, column=0, sticky="ew", padx=12, pady=(3, 10)
        )
        self.refresh_summary()
        self.root.after(100, self.poll_events)

    def values(self) -> dict[str, str]:
        values = {
            "OXYGEN_HOST": self.host.get().strip(),
            "OXYGEN_DIR": self.archive.get().strip(),
            "PHOTOS_LIBRARY": self.library.get().strip(),
            "REVIEW_DIR": self.review.get().strip(),
        }
        if any(not value for value in values.values()):
            raise ValueError("settings must not be empty")
        return values

    def review_path(self) -> Path:
        path = Path(self.review.get()).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    def start(self, label: str, targets: list[str]) -> None:
        if self.running:
            return
        if not (PROJECT_ROOT / "Makefile").is_file():
            messagebox.showerror("photostow", f"Makefile not found in {PROJECT_ROOT}")
            return
        try:
            values = self.values()
        except ValueError as error:
            messagebox.showerror("photostow", str(error))
            return
        self.running = True
        self.status.set(label)
        for button in self.buttons:
            button.state(["disabled"])
        threading.Thread(
            target=self.run_targets, args=(targets, values), daemon=True
        ).start()

    def run_targets(self, targets: list[str], values: dict[str, str]) -> None:
        try:
            for target in targets:
                command = make_command(target, values)
                self.events.put(("log", f"$ {' '.join(command)}\n"))
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    self.events.put(("log", line))
                returncode = process.wait()
                if returncode:
                    self.events.put(
                        ("failed", f"{target} failed with exit {returncode}")
                    )
                    return
        except OSError as error:
            self.events.put(("failed", str(error)))
            return
        self.events.put(("done", "Completed"))

    def poll_events(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self.append_output(text)
                else:
                    self.running = False
                    for button in self.buttons:
                        button.state(["!disabled"])
                    self.status.set(text)
                    self.refresh_summary()
                    if kind == "failed":
                        messagebox.showerror("photostow", text)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def append_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def refresh_summary(self) -> None:
        parts = []
        missing = PROJECT_ROOT / "missing.tsv"
        if missing.is_file():
            with missing.open(encoding="utf-8") as stream:
                parts.append(f"Missing: {max(sum(1 for _ in stream) - 1, 0)}")
        review = self.review_path()
        if review.is_dir():
            parts.append(
                f"In review: {sum(path.is_file() for path in review.rglob('*'))}"
            )
        ledger = PROJECT_ROOT / "photos-oxygen-sha"
        if ledger.is_file():
            modified = (
                datetime.fromtimestamp(ledger.stat().st_mtime, timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M")
            )
            parts.append(f"Local ledger modified: {modified}")
        self.summary.set("   |   ".join(parts) or "No generated files yet")

    def update_ledger(self) -> None:
        self.start("Refreshing ledger…", ["update-oxygen-ledger"])

    def find_missing(self) -> None:
        self.start(
            "Refreshing ledger and finding missing photos…",
            ["update-oxygen-ledger", "missing"],
        )

    def stage_review(self) -> None:
        review = self.review_path()
        if (
            review.is_dir()
            and any(review.iterdir())
            and not messagebox.askokcancel(
                "Rebuild review?",
                "This removes the current review tree and rebuilds it from missing.tsv.",
            )
        ):
            return
        self.start("Building review…", ["stage-review"])

    def open_review(self) -> None:
        review = self.review_path()
        if not review.is_dir():
            messagebox.showerror("photostow", "Build the review tree first.")
            return
        try:
            subprocess.run(["open", str(review)], check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            messagebox.showerror("photostow", str(error))

    def archive_reviewed(self) -> None:
        if not messagebox.askokcancel(
            "Archive reviewed files?",
            "Copy the remaining review files to Oxygen, then update and install the ledger?",
        ):
            return
        self.start("Archiving reviewed files…", ["archive-reviewed"])

    def close(self) -> None:
        if self.running:
            messagebox.showinfo(
                "photostow", "Wait for the current operation to finish."
            )
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    PhotostowGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
