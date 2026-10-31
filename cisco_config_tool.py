"""
Cisco IP Phone config generator  (GUI + CLI)

Creates SEP<MAC>.cnf / SEP<MAC>.cnf.xml files for new phones by copying a
sample file from the TFTP folder and replacing the MAC address in both the
file name and the file content.

Supported TFTP layouts (detected automatically, mixed layouts are fine):
    tftp/<model>/SEP*.cnf.xml
    tftp/<model>/<firmware version>/SEP*.cnf.xml

Usage:
    python cisco_config_tool.py                         GUI
    python cisco_config_tool.py --cli                   interactive console
    python cisco_config_tool.py --cli --path D:\\tftp
    python cisco_config_tool.py --path D:\\tftp --export structure.json
    python cisco_config_tool.py --show-log
    python cisco_config_tool.py --check-tftp 192.168.1.10
"""

import argparse
import codecs
import getpass
import json
import os
import random
import re
import socket
import struct
import sys
from datetime import datetime

SEP_FILE_RE = re.compile(r"^SEP([0-9A-Fa-f]{12})\.cnf(\.xml)?$", re.IGNORECASE)
HEX12_RE = re.compile(r"^[0-9A-F]{12}$")

DEFAULT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config_activity.log"
)
ROOT_LOCATION = "."  # marker for "SEP files directly inside the model folder"


# ============================================================ MAC handling
def format_mac(mac):
    """Strip separators/whitespace and uppercase. Raises ValueError if invalid."""
    cleaned = re.sub(r"[\s:.\-]", "", mac).upper()
    if not HEX12_RE.match(cleaned):
        raise ValueError(
            "Invalid MAC address. It must be exactly 12 hex digits "
            "(0-9, A-F), e.g. 00:11:22:AA:BB:CC"
        )
    return cleaned


def random_mac():
    """Random unicast, locally-administered MAC (never clashes with real vendor MACs)."""
    first = (random.randint(0, 255) & 0xFC) | 0x02
    octets = [first] + [random.randint(0, 255) for _ in range(5)]
    return "".join("{:02X}".format(b) for b in octets)


def pretty_mac(mac):
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2))


# ============================================================ checks
def check_path(path):
    """Check that a local/network folder exists and is readable and writable."""
    if not path:
        return False, "No folder selected."
    if not os.path.exists(path):
        return False, "Path does not exist: " + path
    if not os.path.isdir(path):
        return False, "Path is not a folder: " + path
    if not os.access(path, os.R_OK):
        return False, "No read permission on: " + path
    if not os.access(path, os.W_OK):
        return False, "No write permission on: " + path
    return True, "Folder is readable and writable."


def check_tftp(host, port=69, timeout=3.0):
    """
    Ask a TFTP server for a random, non-existent file. Any valid TFTP reply
    (ERROR 'file not found' or DATA) proves the server is up and reachable.
    """
    host = (host or "").strip()
    if not host:
        return False, "No server address given."
    filename = "probe_{:08x}.tmp".format(random.getrandbits(32)).encode()
    packet = b"\x00\x01" + filename + b"\x00octet\x00"
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, (host, port))
        data, _addr = sock.recvfrom(1024)
        if len(data) >= 2:
            opcode = struct.unpack("!H", data[:2])[0]
            if opcode in (3, 5):  # DATA or ERROR
                return True, "TFTP server {}:{} is responding.".format(host, port)
        return False, "Got a reply, but it is not a valid TFTP packet."
    except socket.timeout:
        return False, "No reply (timeout). Server down, wrong address, or UDP/{} blocked.".format(port)
    except socket.gaierror:
        return False, "Cannot resolve host: " + host
    except OSError as e:
        return False, "Network error: " + str(e)
    finally:
        if sock is not None:
            sock.close()


# ============================================================ folder scanning
def _natural_key(text):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def list_models(root):
    return sorted(
        (f for f in os.listdir(root) if os.path.isdir(os.path.join(root, f))),
        key=_natural_key,
    )


def find_sample_files(folder):
    """All SEP*.cnf(.xml) files directly inside folder."""
    return sorted(f for f in os.listdir(folder) if SEP_FILE_RE.match(f))


def find_locations(model_path):
    """
    Folders (relative to the model folder) that contain SEP sample files:
    ROOT_LOCATION for the model folder itself, plus every sub-folder
    (firmware version) that has at least one SEP file.
    """
    locations = []
    if find_sample_files(model_path):
        locations.append(ROOT_LOCATION)
    subs = sorted(
        (f for f in os.listdir(model_path) if os.path.isdir(os.path.join(model_path, f))),
        key=_natural_key,
    )
    for sub in subs:
        if find_sample_files(os.path.join(model_path, sub)):
            locations.append(sub)
    return locations


def location_folder(model_path, location):
    return model_path if location == ROOT_LOCATION else os.path.join(model_path, location)


def location_label(location):
    return "(model folder)" if location == ROOT_LOCATION else location


def get_structure(root):
    """{model: {location_label: [sample files]}} for the whole TFTP tree."""
    structure = {}
    for model in list_models(root):
        model_path = os.path.join(root, model)
        try:
            locations = find_locations(model_path)
        except OSError:
            continue
        structure[model] = {
            location_label(loc): find_sample_files(location_folder(model_path, loc))
            for loc in locations
        }
    return structure


def export_structure(root, output_file):
    structure = get_structure(root)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)
    return structure


# ============================================================ generation
def _read_text(path):
    """Read as strict UTF-8, remembering a BOM. Line endings are preserved."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8"), True
    return raw.decode("utf-8"), False


def _write_text_atomic(path, text, bom):
    data = text.encode("utf-8")
    if bom:
        data = codecs.BOM_UTF8 + data
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def target_name(sample_name, new_mac):
    m = SEP_FILE_RE.match(sample_name)
    if not m:
        raise ValueError("'{}' is not a valid SEP config file name.".format(sample_name))
    return re.sub(m.group(1), new_mac, sample_name, flags=re.IGNORECASE)


def generate_config(folder, sample_name, new_mac, overwrite=False):
    """
    Create SEP<new_mac>.cnf(.xml) next to the sample file.
    Returns (new_path, replacements_count).
    Raises FileExistsError if the target exists and overwrite is False.
    """
    m = SEP_FILE_RE.match(sample_name)
    if not m:
        raise ValueError("'{}' is not a valid SEP config file name.".format(sample_name))
    old_mac = m.group(1).upper()
    if old_mac == new_mac:
        raise ValueError("The new MAC address is the same as the sample file's MAC.")

    src = os.path.join(folder, sample_name)
    dest = os.path.join(folder, target_name(sample_name, new_mac))
    if os.path.exists(dest) and not overwrite:
        raise FileExistsError(dest)

    content, bom = _read_text(src)
    new_content, count = re.subn(old_mac, new_mac, content, flags=re.IGNORECASE)
    _write_text_atomic(dest, new_content, bom)
    return dest, count


# ============================================================ activity log
class ActivityLog(object):
    """Append-only text log. Logging problems never break the main task."""

    def __init__(self, path=DEFAULT_LOG):
        self.path = path

    def log(self, action, mac, details, status="SUCCESS"):
        clean = " ".join(str(details).split())
        line = "[{}] {} | User: {} | MAC: {} | Action: {} | Details: {}\n".format(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status, getpass.getuser() if _has_user() else "?", mac, action, clean,
        )
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            return True
        except OSError:
            return False

    def tail(self, limit=200):
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return []
        return [l.rstrip("\n") for l in lines[-limit:]]


def _has_user():
    try:
        getpass.getuser()
        return True
    except Exception:
        return False


def do_generate(logger, folder, label, sample, new_mac, overwrite=False):
    """generate_config + logging. FileExistsError is passed up un-logged."""
    try:
        dest, count = generate_config(folder, sample, new_mac, overwrite)
    except FileExistsError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as e:
        logger.log("CREATE_CONFIG", new_mac, "{} / {}: {}".format(label, sample, e), "FAILED")
        raise
    logger.log("CREATE_CONFIG", new_mac,
               "{} / {} -> {} ({} replacements{})".format(
                   label, sample, os.path.basename(dest), count,
                   ", overwritten" if overwrite else ""))
    return dest, count


# ============================================================ CLI
def _ask_choice(title, items, labeler=str):
    """Let the user pick from items; auto-selects when there is only one."""
    if len(items) == 1:
        print("{}: {}".format(title, labeler(items[0])))
        return items[0]
    print("\n{}:".format(title))
    for i, item in enumerate(items, 1):
        print("{}. {}".format(i, labeler(item)))
    try:
        idx = int(input("Select (number): ")) - 1
        if idx < 0:
            raise IndexError
        return items[idx]
    except (ValueError, IndexError):
        return None


def run_cli(args):
    logger = ActivityLog(args.log)
    root = args.path or input("Enter TFTP folder path: ").strip().strip('"')
    ok, msg = check_path(root)
    if not ok:
        print("[X] " + msg)
        return 1

    host = input("TFTP server address to test (Enter to skip): ").strip()
    if host:
        ok, msg = check_tftp(host)
        print(("[OK] " if ok else "[!] ") + msg)

    raw = input("Enter new MAC address (Enter = random): ").strip()
    new_mac = random_mac() if not raw else None
    if new_mac:
        print("Random MAC: " + pretty_mac(new_mac))
    else:
        try:
            new_mac = format_mac(raw)
        except ValueError as e:
            print("[X] " + str(e))
            return 1

    models = list_models(root)
    if not models:
        print("[X] No model folders found in the TFTP root.")
        return 1
    model = _ask_choice("Available phone models", models)
    if model is None:
        print("[X] Invalid selection.")
        return 1
    model_path = os.path.join(root, model)

    locations = find_locations(model_path)
    if not locations:
        print("[X] No SEP config file found in this model folder or its sub-folders.")
        return 1
    location = _ask_choice("Firmware version / location", locations, location_label)
    if location is None:
        print("[X] Invalid selection.")
        return 1
    folder = location_folder(model_path, location)

    sample = _ask_choice("Sample files", find_sample_files(folder))
    if sample is None:
        print("[X] Invalid selection.")
        return 1

    label = "{}/{}".format(model, location_label(location))
    try:
        try:
            dest, count = do_generate(logger, folder, label, sample, new_mac)
        except FileExistsError:
            ans = input("'{}' already exists. Overwrite? [y/N]: ".format(
                target_name(sample, new_mac))).strip().lower()
            if ans != "y":
                print("Cancelled.")
                return 0
            dest, count = do_generate(logger, folder, label, sample, new_mac, overwrite=True)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print("[X] Failed: {}".format(e))
        return 1

    print("\n[OK] New config file created:\n- {}\n  ({} MAC occurrence(s) replaced)".format(dest, count))
    return 0


# ============================================================ GUI
def run_gui(initial_path, log_path):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk, scrolledtext

    logger = ActivityLog(log_path)

    class App(object):
        def __init__(self, win):
            self.win = win
            win.title("Cisco Config Generator")
            win.minsize(540, 600)
            self.models = []
            self.locations = []
            self.samples = []

            pad = {"padx": 10, "pady": (8, 0), "anchor": "w"}

            tk.Label(win, text="TFTP folder:").pack(**pad)
            row = tk.Frame(win)
            row.pack(fill="x", padx=10)
            self.path_var = tk.StringVar(
                value=initial_path if initial_path and os.path.isdir(initial_path) else "")
            tk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=(6, 0))
            tk.Button(row, text="Reload", command=self.load_models).pack(side="left", padx=(6, 0))

            tk.Label(win, text="TFTP server address (optional, for connection test):").pack(**pad)
            row = tk.Frame(win)
            row.pack(fill="x", padx=10)
            self.host_var = tk.StringVar()
            tk.Entry(row, textvariable=self.host_var).pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Test", command=self.test_tftp).pack(side="left", padx=(6, 0))

            tk.Label(win, text="Phone model:").pack(**pad)
            self.listbox = tk.Listbox(win, height=7, exportselection=False)
            self.listbox.pack(fill="both", expand=True, padx=10)
            self.listbox.bind("<<ListboxSelect>>", self.on_model_select)

            tk.Label(win, text="Firmware version / location:").pack(**pad)
            self.loc_var = tk.StringVar()
            self.loc_combo = ttk.Combobox(win, textvariable=self.loc_var, state="readonly")
            self.loc_combo.pack(fill="x", padx=10)
            self.loc_combo.bind("<<ComboboxSelected>>", self.on_location_select)

            tk.Label(win, text="Sample file:").pack(**pad)
            self.sample_var = tk.StringVar()
            self.sample_combo = ttk.Combobox(win, textvariable=self.sample_var, state="readonly")
            self.sample_combo.pack(fill="x", padx=10)

            tk.Label(win, text="New MAC address:").pack(**pad)
            row = tk.Frame(win)
            row.pack(fill="x", padx=10)
            self.mac_entry = tk.Entry(row)
            self.mac_entry.pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Random", command=self.fill_random).pack(side="left", padx=(6, 0))

            row = tk.Frame(win)
            row.pack(pady=12)
            tk.Button(row, text="Generate Config File", command=self.generate).pack(side="left", padx=4)
            tk.Button(row, text="Show Log", command=self.show_log).pack(side="left", padx=4)
            tk.Button(row, text="Export Structure (JSON)", command=self.export_json).pack(side="left", padx=4)

            self.status = tk.StringVar(value="Select the TFTP folder to begin.")
            tk.Label(win, textvariable=self.status, anchor="w", justify="left",
                     wraplength=500, relief="sunken").pack(fill="x", side="bottom")

            if self.path_var.get():
                self.load_models()

        # ---- helpers
        def set_status(self, text):
            self.status.set(text)
            self.win.update_idletasks()

        def root_dir(self):
            return self.path_var.get().strip().strip('"')

        def selected_model_path(self):
            sel = self.listbox.curselection()
            if not sel:
                return None, None
            model = self.models[sel[0]]
            return model, os.path.join(self.root_dir(), model)

        def selected_location(self):
            label = self.loc_var.get()
            for loc in self.locations:
                if location_label(loc) == label:
                    return loc
            return None

        # ---- actions
        def browse(self):
            path = filedialog.askdirectory(initialdir=self.root_dir() or None)
            if path:
                self.path_var.set(path)
                self.load_models()

        def load_models(self):
            self.listbox.delete(0, tk.END)
            for combo, var in ((self.loc_combo, self.loc_var), (self.sample_combo, self.sample_var)):
                combo["values"] = ()
                var.set("")
            ok, msg = check_path(self.root_dir())
            if not ok:
                self.models = []
                self.set_status("[!] " + msg)
                return
            try:
                self.models = list_models(self.root_dir())
            except OSError as e:
                messagebox.showerror("Error", "Failed to load models:\n{}".format(e))
                return
            for m in self.models:
                self.listbox.insert(tk.END, m)
            self.set_status("{} model folder(s) found. {}".format(len(self.models), msg))

        def on_model_select(self, _event=None):
            model, model_path = self.selected_model_path()
            if not model:
                return
            try:
                self.locations = find_locations(model_path)
            except OSError as e:
                messagebox.showerror("Error", str(e))
                return
            labels = [location_label(l) for l in self.locations]
            self.loc_combo["values"] = labels
            self.loc_var.set(labels[0] if labels else "")
            if not labels:
                self.set_status("[!] No SEP file found in '{}' or its sub-folders.".format(model))
            self.on_location_select()

        def on_location_select(self, _event=None):
            _model, model_path = self.selected_model_path()
            loc = self.selected_location()
            if not model_path or loc is None:
                self.sample_combo["values"] = ()
                self.sample_var.set("")
                return
            try:
                self.samples = find_sample_files(location_folder(model_path, loc))
            except OSError as e:
                messagebox.showerror("Error", str(e))
                return
            self.sample_combo["values"] = self.samples
            self.sample_var.set(self.samples[0] if self.samples else "")

        def fill_random(self):
            self.mac_entry.delete(0, tk.END)
            self.mac_entry.insert(0, pretty_mac(random_mac()))

        def test_tftp(self):
            self.set_status("Testing TFTP server...")
            ok, msg = check_tftp(self.host_var.get())
            self.set_status(("[OK] " if ok else "[!] ") + msg)
            (messagebox.showinfo if ok else messagebox.showwarning)("TFTP test", msg)

        def generate(self):
            ok, msg = check_path(self.root_dir())
            if not ok:
                messagebox.showerror("Error", msg)
                return
            model, model_path = self.selected_model_path()
            if not model:
                messagebox.showerror("Error", "No model selected.")
                return
            loc = self.selected_location()
            sample = self.sample_var.get()
            if loc is None or not sample:
                messagebox.showerror("Error", "No SEP config file found in the selected location.")
                return
            try:
                new_mac = format_mac(self.mac_entry.get())
            except ValueError as e:
                messagebox.showerror("Invalid MAC", str(e))
                return

            folder = location_folder(model_path, loc)
            label = "{}/{}".format(model, location_label(loc))
            try:
                try:
                    dest, count = do_generate(logger, folder, label, sample, new_mac)
                except FileExistsError:
                    if not messagebox.askyesno(
                            "File exists",
                            "'{}' already exists.\nOverwrite it?".format(target_name(sample, new_mac))):
                        self.set_status("Cancelled.")
                        return
                    dest, count = do_generate(logger, folder, label, sample, new_mac, overwrite=True)
            except (OSError, UnicodeDecodeError, ValueError) as e:
                messagebox.showerror("Error", "Failed to generate config:\n{}".format(e))
                return

            self.set_status("[OK] Created: " + dest)
            messagebox.showinfo(
                "Success",
                "New config file created:\n{}\n\n{} MAC occurrence(s) replaced.".format(dest, count))
            self.on_location_select()  # refresh sample list

        def show_log(self):
            win = tk.Toplevel(self.win)
            win.title("Activity log - " + logger.path)
            win.geometry("900x420")
            box = scrolledtext.ScrolledText(win, wrap="none")
            box.pack(fill="both", expand=True)
            lines = logger.tail(300)
            box.insert("1.0", "\n".join(lines) if lines else "(log is empty)")
            box.see(tk.END)
            box.configure(state="disabled")

        def export_json(self):
            ok, msg = check_path(self.root_dir())
            if not ok:
                messagebox.showerror("Error", msg)
                return
            out = filedialog.asksaveasfilename(
                defaultextension=".json", initialfile="tftp_structure.json",
                filetypes=[("JSON", "*.json")])
            if not out:
                return
            try:
                structure = export_structure(self.root_dir(), out)
            except OSError as e:
                messagebox.showerror("Error", str(e))
                return
            logger.log("EXPORT_STRUCTURE", "-", "{} models -> {}".format(len(structure), out))
            self.set_status("[OK] Structure exported: " + out)

    win = tk.Tk()
    App(win)
    win.mainloop()
    return 0


# ============================================================ main
def main():
    p = argparse.ArgumentParser(description="Cisco IP Phone config generator")
    p.add_argument("--cli", action="store_true", help="use the console interface")
    p.add_argument("--path", default=None, help="TFTP root folder")
    p.add_argument("--log", default=DEFAULT_LOG, help="activity log file (default: next to this script)")
    p.add_argument("--export", metavar="FILE", help="export TFTP structure to a JSON file and exit")
    p.add_argument("--show-log", action="store_true", help="print the last log entries and exit")
    p.add_argument("--check-tftp", metavar="HOST", help="test a TFTP server and exit")
    args = p.parse_args()

    if args.show_log:
        lines = ActivityLog(args.log).tail(50)
        print("\n".join(lines) if lines else "(log is empty or not found)")
        return 0
    if args.check_tftp:
        ok, msg = check_tftp(args.check_tftp)
        print(("[OK] " if ok else "[X] ") + msg)
        return 0 if ok else 1
    if args.export:
        ok, msg = check_path(args.path)
        if not ok:
            print("[X] " + msg + " (use --path)")
            return 1
        structure = export_structure(args.path, args.export)
        print("[OK] {} model(s) exported to {}".format(len(structure), args.export))
        return 0
    return run_cli(args) if args.cli else run_gui(args.path, args.log)


if __name__ == "__main__":
    sys.exit(main())
