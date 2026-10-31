import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox

def format_mac(mac):
    return mac.replace(":", "").replace("-", "").replace(".", "").upper()

def find_sample_file(folder_path):
    for file in os.listdir(folder_path):
        if re.match(r"^SEP[0-9A-Fa-f]{12}\.cnf(\.xml)?$", file):
            return os.path.join(folder_path, file)
    return None

def replace_mac_in_file(src_path, dest_path, old_mac, new_mac):
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace(old_mac, new_mac)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(content)

class CiscoConfigApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Cisco Config Generator")
        self.tftp_path = ""
        self.models = []

        # UI Elements
        self.path_label = tk.Label(root, text="Select TFTP folder:")
        self.path_label.pack()

        self.path_button = tk.Button(root, text="Browse Folder", command=self.browse_folder)
        self.path_button.pack()

        self.model_listbox = tk.Listbox(root, height=8, width=50)
        self.model_listbox.pack()

        self.mac_label = tk.Label(root, text="Enter new MAC address:")
        self.mac_label.pack()

        self.mac_entry = tk.Entry(root)
        self.mac_entry.pack()

        self.generate_button = tk.Button(root, text="Generate Config File", command=self.generate_config)
        self.generate_button.pack()

    def browse_folder(self):
        path = filedialog.askdirectory()
        if not path:
            return
        self.tftp_path = path
        self.load_models()

    def load_models(self):
        self.model_listbox.delete(0, tk.END)
        try:
            self.models = [f for f in os.listdir(self.tftp_path) if os.path.isdir(os.path.join(self.tftp_path, f))]
            for model in self.models:
                self.model_listbox.insert(tk.END, model)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load models: {e}")

    def generate_config(self):
        if not self.tftp_path:
            messagebox.showerror("Error", "No TFTP path selected.")
            return

        mac_input = self.mac_entry.get().strip()
        new_mac = format_mac(mac_input)
        if len(new_mac) != 12:
            messagebox.showwarning("Warning", "MAC format seems unusual, proceeding anyway...")

        try:
            selection = self.model_listbox.curselection()
            if not selection:
                messagebox.showerror("Error", "No model selected.")
                return

            selected_model = self.models[selection[0]]
            model_path = os.path.join(self.tftp_path, selected_model)
            sample_file = find_sample_file(model_path)
            if not sample_file:
                messagebox.showerror("Error", "No SEP config file found in selected folder.")
                return

            old_mac = re.findall(r"SEP([0-9A-Fa-f]{12})", os.path.basename(sample_file))[0].upper()
            new_filename = os.path.basename(sample_file).replace(old_mac, new_mac)
            new_file_path = os.path.join(model_path, new_filename)

            replace_mac_in_file(sample_file, new_file_path, old_mac, new_mac)

            messagebox.showinfo("Success", f"New config file created:\n{new_file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate config: {e}")

# اجرای برنامه
if __name__ == "__main__":
    root = tk.Tk()
    app = CiscoConfigApp(root)
    root.mainloop()
