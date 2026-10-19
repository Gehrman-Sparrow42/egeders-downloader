"""Responsive native interface; each browser operation stays on its worker thread."""

import queue
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import service
from .models import Account, AuthExpiredError, Course


class Application:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Ege Ders Downloader")
        self.root.geometry("1040x760")
        self.root.minsize(760, 580)
        self.settings = service.Settings()
        try:
            self.config = self.settings.load()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Settings", str(exc), parent=root)
            self.config = {}
        saved = self.config.get("account", "")
        username, _, domain = saved.partition("@")
        self.username = tk.StringVar(value=username)
        self.domain = tk.StringVar(value=domain or "ogrenci.ege.edu.tr")
        self.password = tk.StringVar()
        self.output = tk.StringVar(
            value=self.config.get("output") or str(service.DEFAULT_OUTPUT)
        )
        self.query = tk.StringVar()
        self.semester = tk.StringVar(value="All semesters")
        self.status = tk.StringVar(
            value="Sign in, discover courses, then select materials to sync."
        )
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closing = False
        self.courses: list[Course] = []
        self.loaded_account = ""
        self.selected: set[int] = set()
        self.controls: list[ttk.Widget] = []
        self._build()
        self.query.trace_add("write", lambda *_: self._filter())
        self.semester.trace_add("write", lambda *_: self._filter())
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)

    def _build(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=3)
        frame.rowconfigure(6, weight=2)
        login = ttk.LabelFrame(frame, text="University account", padding=8)
        login.grid(row=0, column=0, sticky="ew")
        ttk.Label(login, text="Username").grid(row=0, column=0, sticky="w")
        user_entry = ttk.Entry(login, textvariable=self.username, width=24)
        user_entry.grid(row=1, column=0, padx=(0, 8), sticky="ew")
        domain = ttk.Combobox(
            login,
            textvariable=self.domain,
            values=["ogrenci.ege.edu.tr", "ege.edu.tr", "mail.ege.edu.tr"],
            state="readonly",
            width=23,
        )
        domain.grid(row=1, column=1, padx=(0, 8))
        ttk.Label(login, text="Password (optional for manual login)").grid(
            row=0, column=2, sticky="w"
        )
        password_entry = ttk.Entry(
            login, textvariable=self.password, show="•", width=24
        )
        password_entry.grid(row=1, column=2, padx=(0, 8), sticky="ew")
        login.columnconfigure(0, weight=1)
        login.columnconfigure(2, weight=1)
        self.controls.extend([user_entry, domain, password_entry])
        buttons = ttk.Frame(login)
        buttons.grid(row=2, column=0, columnspan=3, pady=(8, 0), sticky="w")
        self._button(buttons, "Sign in with password", lambda: self._login(False)).pack(
            side="left"
        )
        self._button(buttons, "Manual browser login", lambda: self._login(True)).pack(
            side="left", padx=8
        )
        self._button(buttons, "Discover courses", self._discover).pack(side="left")
        output = ttk.Frame(frame)
        output.grid(row=1, column=0, sticky="ew", pady=10)
        ttk.Label(output, text="Output folder").pack(side="left", padx=(0, 8))
        output_entry = ttk.Entry(output, textvariable=self.output)
        output_entry.pack(side="left", fill="x", expand=True)
        self.controls.append(output_entry)
        self._button(output, "Browse…", self._browse).pack(side="left", padx=(8, 0))
        filters = ttk.Frame(frame)
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(filters, text="Filter").pack(side="left")
        ttk.Entry(filters, textvariable=self.query, width=30).pack(side="left", padx=8)
        self.semesters = ttk.Combobox(
            filters,
            textvariable=self.semester,
            state="readonly",
            values=["All semesters"],
            width=20,
        )
        self.semesters.pack(side="left")
        self._button(
            filters, "Select visible", lambda: self._select_visible(True)
        ).pack(side="left", padx=8)
        self._button(
            filters, "Clear visible", lambda: self._select_visible(False)
        ).pack(side="left")
        table = ttk.Frame(frame)
        table.grid(row=3, column=0, sticky="nsew")
        self.tree = ttk.Treeview(
            table,
            columns=("selected", "id", "semester", "title"),
            show="headings",
            selectmode="extended",
        )
        for name, heading, width in [
            ("selected", "Sync", 50),
            ("id", "ID", 80),
            ("semester", "Semester", 100),
            ("title", "Course", 600),
        ]:
            self.tree.heading(name, text=heading)
            self.tree.column(name, width=width, stretch=name == "title")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._space)
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="ew", pady=10)
        self._button(actions, "Save selection", self._save_clicked).pack(side="left")
        self._button(actions, "Sync selected courses", self._sync).pack(
            side="left", padx=8
        )
        self.cancel_button = ttk.Button(
            actions, text="Cancel sync", command=self._cancel, state="disabled"
        )
        self.cancel_button.pack(side="left")
        self.count = ttk.Label(actions, text="0 selected")
        self.count.pack(side="right")
        progress = ttk.Frame(frame)
        progress.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        self.progress = ttk.Progressbar(progress, mode="indeterminate", length=130)
        self.progress.pack(side="left", padx=(0, 12))
        ttk.Label(progress, textvariable=self.status, wraplength=750).pack(
            side="left", fill="x", expand=True
        )
        logs = ttk.Frame(frame)
        logs.grid(row=6, column=0, sticky="nsew")
        self.log = tk.Text(logs, height=9, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.controls.append(button)
        return button

    def _account(self) -> Account:
        return Account(self.username.get(), self.domain.get())

    def _save(self, account: Account):
        output = Path(self.output.get()).expanduser()
        if not output.is_absolute():
            raise ValueError("Choose an absolute output folder.")
        self.config.update(account=account.key, output=str(output))
        if self.loaded_account == account.key:
            self.config.setdefault("selected", {})[account.key] = sorted(self.selected)
        self.settings.save(self.config)

    def _save_clicked(self):
        try:
            self._save(self._account())
            self.status.set("Selection and output folder saved.")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Settings", str(exc), parent=self.root)

    def _browse(self):
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.output.get())
        if folder:
            self.output.set(folder)

    def _login(self, manual: bool):
        try:
            account = self._account()
            self._save(account)
            password = self.password.get() if not manual else None
            self.password.set("")
            if not manual and not password:
                raise ValueError("Enter a password, or use Manual browser login.")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Login", str(exc), parent=self.root)
            return
        self._start(
            "login",
            lambda log: service.login(
                account,
                self.settings.state_dir,
                password=password,
                manual=manual,
                log=log,
            ),
        )

    def _discover(self):
        try:
            account = self._account()
            self._save(account)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Courses", str(exc), parent=self.root)
            return
        self._start(
            "courses",
            lambda log: (
                account.key,
                service.discover(account, self.settings.state_dir, log=log),
            ),
        )

    def _sync(self):
        try:
            account = self._account()
            self._save(account)
            if account.key != self.loaded_account:
                raise ValueError("Discover courses for this account before syncing.")
            if not self.selected:
                raise ValueError("Check one or more courses in the Sync column.")
            output = Path(self.output.get()).expanduser()
            ids = sorted(self.selected)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Sync", str(exc), parent=self.root)
            return
        self._start(
            "sync",
            lambda log: service.sync(
                account,
                self.settings.state_dir,
                output,
                ids,
                log=log,
                cancel=self.cancel,
            ),
        )

    def _start(self, operation, function):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for control in self.controls:
            control.state(["disabled"])
        self.cancel_button.configure(
            state="normal" if operation == "sync" else "disabled"
        )
        self.progress.start(12)
        self.status.set(f"Running {operation}…")

        def worker():
            try:
                result = function(
                    lambda message: self.events.put(("log", str(message)))
                )
                self.events.put(("done", (operation, result)))
            except Exception as exc:  # noqa: BLE001 -- report worker failures through the UI queue
                self.events.put(("error", exc))

        threading.Thread(
            target=worker, name=f"egeders-{operation}", daemon=True
        ).start()

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._append(payload)
                    continue
                self.busy = False
                self.progress.stop()
                for control in self.controls:
                    control.state(["!disabled"])
                self.cancel_button.configure(state="disabled")
                if event == "error":
                    text = str(payload)
                    if isinstance(payload, AuthExpiredError):
                        text += "\nUse Sign in with password or Manual browser login, then retry."
                    self.status.set("Operation failed. See log.")
                    self._append(text)
                    if not self.closing:
                        messagebox.showerror("Ege Ders", text, parent=self.root)
                else:
                    operation, result = payload
                    if operation == "courses":
                        self.loaded_account, self.courses = result
                        self.selected = set(
                            self.config.get("selected", {}).get(self.loaded_account, [])
                        )
                        self.selected.intersection_update(
                            course.id for course in self.courses
                        )
                        self.semesters.configure(
                            values=["All semesters"]
                            + sorted(
                                {
                                    course.semester
                                    for course in self.courses
                                    if course.semester
                                },
                                reverse=True,
                            )
                        )
                        self.semester.set("All semesters")
                        self._filter()
                        self.status.set(
                            f"Found {len(self.courses)} courses. Check the courses to sync."
                        )
                    elif operation == "login":
                        self.status.set("Login saved. Discover courses next.")
                    else:
                        counts = asdict(result)
                        text = "; ".join(
                            f"{key}: {value}"
                            for key, value in counts.items()
                            if key != "errors"
                        )
                        self.status.set(text)
                        self._append(text)
                if self.closing:
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _filter(self):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        semester = self.semester.get()
        for course in self.courses:
            if query not in f"{course.id} {course.title} {course.semester}".casefold():
                continue
            if semester != "All semesters" and course.semester != semester:
                continue
            self.tree.insert(
                "",
                "end",
                iid=str(course.id),
                values=(
                    "✓" if course.id in self.selected else "",
                    course.id,
                    course.semester,
                    course.title,
                ),
            )
        self.count.configure(text=f"{len(self.selected)} selected")

    def _toggle(self, row):
        if self.busy or not row:
            return
        course_id = int(row)
        if course_id in self.selected:
            self.selected.remove(course_id)
        else:
            self.selected.add(course_id)
        self.tree.set(row, "selected", "✓" if course_id in self.selected else "")
        self.count.configure(text=f"{len(self.selected)} selected")

    def _click(self, event):
        if self.tree.identify_column(event.x) == "#1":
            self._toggle(self.tree.identify_row(event.y))
            return "break"
        return None

    def _space(self, _event):
        for row in self.tree.selection():
            self._toggle(row)
        return "break"

    def _select_visible(self, select):
        if self.busy:
            return
        ids = {int(row) for row in self.tree.get_children()}
        self.selected.update(ids) if select else self.selected.difference_update(ids)
        self._filter()

    def _cancel(self):
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.set(
            "Cancellation requested; waiting for the current request to finish."
        )

    def _close(self):
        self.password.set("")
        if self.busy:
            self.closing = True
            self.cancel.set()
            self.status.set(
                "Closing after the current browser operation finishes. Close its browser if needed."
            )
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == "__main__":
    main()
