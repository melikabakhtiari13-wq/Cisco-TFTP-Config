"""
Cisco IP Phone config generator (GUI + CLI)

Usage:
    python cisco_config_tool.py                  -> graphical interface
    python cisco_config_tool.py --cli            -> console interface
    python cisco_config_tool.py --cli --path D:\\tftp
"""

import argparse
import codecs
import os
import re
import sys

SEP_FILE_RE = re.compile(r"^SEP([0-9A-Fa-f]{12})\.cnf(\.xml)?$", re.IGNORECASE)
HEX12_RE = re.compile(r"^[0-9A-F]{12}$")


# ----------------------------------------------------------------- core logic
def format_mac(mac):
    """Strip separators/whitespace and uppercase. Raises ValueError if invalid."""
    cleaned = re.sub(r"[\s:.\-]", "", mac).upper()
    if not HEX12_RE.match(cleaned):
        raise ValueError(
            "Invalid MAC address. It must be exactly 12 hex digits "
            "(0-9, A-F), e.g. 00:11:22:AA:BB:CC"
        )
    return cleaned


def list_models(root):
    return sorted(
        f for f in os.listdir(root) if os.path.isdir(os.path.join(root, f))
    )


def find_sample_files(model_path):
    """All SEP*.cnf(.xml) files in the model folder, sorted."""
    return sorted(f for f in os.listdir(model_path) if SEP_FILE_RE.match(f))


def read_text(path):
    """Read a file keeping its encoding (BOM) and line endings intact."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8"), True
    return raw.decode("utf-8"), False


def write_text(path, text, bom):
    data = text.encode("utf-8")
    if bom:
        data = codecs.BOM_UTF8 + data
    with open(path, "wb") as f:
        f.write(data)


def generate_config(model_path, sample_name, new_mac):
    """
    Create SEP<new_mac>.cnf(.xml) from the sample file.
    Returns (new_path, replacements_count).
    """
    m = SEP_FILE_RE.match(sample_name)
    if not m:
        raise ValueError(f"'{sample_name}' is not a valid SEP config file name.")

    old_mac = m.group(1).upper()
    if old_mac == new_mac:
        raise ValueError("The new MAC address is the same as the sample file's MAC.")

    src = os.path.join(model_path, sample_name)
    new_name = re.sub(old_mac, new_mac, sample_name, flags=re.IGNORECASE)
    dest = os.path.join(model_path, new_name)

    content, bom = read_text(src)
    new_content, count = re.subn(old_mac, new_mac, content, flags=re.IGNORECASE)
    write_text(dest, new_content, bom)
    return dest, count


# ------------------------------------------------------------------------ CLI
def run_cli(root):
    if not root:
        root = input("Enter TFTP folder path: ").strip().strip('"')
    if not os.path.isdir(root):
        print(f"[X] Folder not found: {root}")
        return 1

    try:
        new_mac = format_mac(input("Enter new MAC address: "))
    except ValueError as e:
        print(f"[X] {e}")
        return 1

    models = list_models(root)
    if not models:
        print("[X] No model folders found in the TFTP root.")
        return 1

    print("\nAvailable phone models:")
    for i, m in enumerate(models, 1):
        print(f"{i}. {m}")
    try:
        model = models[int(input("Select a model (number): ")) - 1]
    except (ValueError, IndexError):
        print("[X] Invalid model selection.")
        return 1

    model_path = os.path.join(root, model)
    samples = find_sample_files(model_path)
    if not samples:
        print("[X] No SEP config file found in the selected model folder.")
        return 1

    sample = samples[0]
    if len(samples) > 1:
        print("\nMultiple sample files found:")
        for i, s in enumerate(samples, 1):
            print(f"{i}. {s}")
        try:
            sample = samples[int(input("Select sample file (number): ")) - 1]
        except (ValueError, IndexError):
            print("[X] Invalid selection.")
            return 1

    new_name = re.sub(SEP_FILE_RE.match(sample).group(1), new_mac, sample, flags=re.IGNORECASE)
    if os.path.exists(os.path.join(model_path, new_name)):
        if input(f"'{new_name}' already exists. Overwrite? [y/N]: ").strip().lower() != "y":
            print("Cancelled.")
            return 0

    try:
        dest, count = generate_config(model_path, sample, new_mac)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print(f"[X] Failed: {e}")
        return 1

    print(f"\n[OK] New config file created:\n- {dest}\n  ({count} MAC occurrence(s) replaced)")
    return 0


# ------------------------------------------------------------------------ GUI
def run_gui(root_path):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App:
        def __init__(self, win):
            self.win = win
            win.title("Cisco Config Generator")
            win.minsize(460, 420)
            self.models = []
            self.samples = []

            pad = {"padx": 10, "pady": (8, 0), "anchor": "w"}

            tk.Label(win, text="TFTP folder:").pack(**pad)
            row = tk.Frame(win)
            row.pack(fill="x", padx=10)
            self.path_var = tk.StringVar(value=root_path if root_path and os.path.isdir(root_path) else "")
            tk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=(6, 0))
            tk.Button(row, text="Reload", command=self.load_models).pack(side="left", padx=(6, 0))

            tk.Label(win, text="Phone model:").pack(**pad)
            self.listbox = tk.Listbox(win, height=8, exportselection=False)
            self.listbox.pack(fill="both", expand=True, padx=10)
            self.listbox.bind("<<ListboxSelect>>", self.on_model_select)

            tk.Label(win, text="Sample file:").pack(**pad)
            self.sample_var = tk.StringVar()
            self.sample_combo = ttk.Combobox(win, textvariable=self.sample_var, state="readonly")
            self.sample_combo.pack(fill="x", padx=10)

            tk.Label(win, text="New MAC address:").pack(**pad)
            self.mac_entry = tk.Entry(win)
            self.mac_entry.pack(fill="x", padx=10)

            tk.Button(win, text="Generate Config File", command=self.generate).pack(pady=12)

            if self.path_var.get():
                self.load_models()

        def browse(self):
            path = filedialog.askdirectory(initialdir=self.path_var.get() or None)
            if path:
                self.path_var.set(path)
                self.load_models()

        def load_models(self):
            self.listbox.delete(0, tk.END)
            self.sample_combo["values"] = ()
            self.sample_var.set("")
            path = self.path_var.get().strip()
            try:
                self.models = list_models(path)
            except OSError as e:
                messagebox.showerror("Error", f"Failed to load models:\n{e}")
                return
            for m in self.models:
                self.listbox.insert(tk.END, m)

        def on_model_select(self, _event=None):
            sel = self.listbox.curselection()
            if not sel:
                return
            model_path = os.path.join(self.path_var.get().strip(), self.models[sel[0]])
            try:
                self.samples = find_sample_files(model_path)
            except OSError as e:
                messagebox.showerror("Error", str(e))
                return
            self.sample_combo["values"] = self.samples
            self.sample_var.set(self.samples[0] if self.samples else "")

        def generate(self):
            root = self.path_var.get().strip()
            if not os.path.isdir(root):
                messagebox.showerror("Error", "Select a valid TFTP folder.")
                return

            sel = self.listbox.curselection()
            if not sel:
                messagebox.showerror("Error", "No model selected.")
                return
            sample = self.sample_var.get()
            if not sample:
                messagebox.showerror("Error", "No SEP config file found in the selected folder.")
                return

            try:
                new_mac = format_mac(self.mac_entry.get())
            except ValueError as e:
                messagebox.showerror("Invalid MAC", str(e))
                return

            model_path = os.path.join(root, self.models[sel[0]])
            new_name = re.sub(SEP_FILE_RE.match(sample).group(1), new_mac, sample, flags=re.IGNORECASE)
            if os.path.exists(os.path.join(model_path, new_name)):
                if not messagebox.askyesno("File exists", f"'{new_name}' already exists.\nOverwrite it?"):
                    return

            try:
                dest, count = generate_config(model_path, sample, new_mac)
            except (OSError, UnicodeDecodeError, ValueError) as e:
                messagebox.showerror("Error", f"Failed to generate config:\n{e}")
                return

            messagebox.showinfo(
                "Success",
                f"New config file created:\n{dest}\n\n{count} MAC occurrence(s) replaced.",
            )
            self.on_model_select()  # refresh sample list

    win = tk.Tk()
    App(win)
    win.mainloop()
    return 0


# ----------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Cisco IP Phone config generator")
    parser.add_argument("--cli", action="store_true", help="use the console interface")
    parser.add_argument("--path", default=None, help="TFTP root folder (optional)")
    args = parser.parse_args()
    sys.exit(run_cli(args.path) if args.cli else run_gui(args.path))


if __name__ == "__main__":
    main()
