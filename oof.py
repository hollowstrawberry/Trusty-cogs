import os
for folder in [f.path for f in os.scandir('.') if f.is_dir()][3:]:
    with open(os.path.join(folder, "info.json"), 'r') as f:
        contents = f.read()
    contents = contents.replace('"3.5.0"', '"3.5.0.dev363"')
    with open(os.path.join(folder, "info.json"), 'w') as f:
        f.write(contents)
    print(os.path.join(folder, "info.json"))