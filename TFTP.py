import os
import re

TFTP_ROOT = r"Z:\Public\USER\bakhtiari\new"

def format_mac(mac):
    return mac.replace(":", "").replace("-", "").replace(".", "").upper()

def list_folders(path):
    return sorted([f for f in os.listdir(path) if os.path.isdir(os.path.join(path, f))])

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

def main():
    mac_input = input("Enter new MAC address: ")
    new_mac = format_mac(mac_input)

    if len(new_mac) != 12:
        print("⚠️  Warning: MAC address length is not 12 characters after cleaning. Proceeding anyway...")

    # انتخاب مدل
    models = list_folders(TFTP_ROOT)
    if not models:
        print("❌ No models found in the TFTP root.")
        return

    print("\nAvailable phone models:")
    for i, model in enumerate(models):
        print(f"{i + 1}. {model}")

    try:
        model_index = int(input("Select a model (number): ")) - 1
        model_folder = models[model_index]
    except:
        print("❌ Invalid model selection.")
        return

    model_path = os.path.join(TFTP_ROOT, model_folder)

    # پیدا کردن فایل نمونه فقط در مسیر مدل (نه نسخه‌ها)
    sample_file = find_sample_file(model_path)
    if not sample_file:
        print("❌ No SEP config file found in selected model folder.")
        return

    # ساخت فایل جدید
    old_mac = re.findall(r"SEP([0-9A-Fa-f]{12})", os.path.basename(sample_file))[0].upper()
    new_filename = os.path.basename(sample_file).replace(old_mac, new_mac)
    new_file_path = os.path.join(model_path, new_filename)

    replace_mac_in_file(sample_file, new_file_path, old_mac, new_mac)

    print(f"\n✅ New config file created:\n- {new_file_path}")

if __name__ == "__main__":
    main()
