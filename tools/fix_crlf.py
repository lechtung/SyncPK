#!/usr/bin/env python3
# Safe CRLF to LF converter for Windows/Linux Development
# This script operates purely on bytes, so it CANNOT corrupt UTF-8 accents or BOMs.

import os
import sys
from pathlib import Path

# Whitelist of safe text file extensions
EXTENSIONS = {
    ".html", ".css", ".py", ".js", ".json", 
    ".txt", ".sh", ".md", ".yml", ".yaml"
}

# Explicit filenames that might not have standard extensions
EXACT_FILES = {
    ".gitignore", ".gitattributes"
}

# Determine target directory: optional argument, or default to script's parent folder
script_dir = Path(__file__).resolve().parent
default_dir = script_dir.parent  # Carpeta padre de donde está el script

if len(sys.argv) > 1:
    target_dir = sys.argv[1]
else:
    target_dir = str(default_dir)

print(f"Starting safe CRLF to LF conversion in: {target_dir}")

count = 0
for root, dirs, files in os.walk(target_dir):
    # Skip standard heavy/hidden directories
    if ".git" in root or "venv" in root or "__pycache__" in root:
        continue

    for file in files:
        ext = os.path.splitext(file)[1].lower()
        
        # Check if the file matches our safe whitelist
        if ext in EXTENSIONS or file in EXACT_FILES or file.startswith(".env"):
            path = os.path.join(root, file)
            
            try:
                # Read as raw bytes to avoid any decoding/encoding issues (like Spanish accents)
                with open(path, "rb") as f:
                    content = f.read()
                
                # If CRLF (\r\n) is found, replace with LF (\n)
                if b"\r\n" in content:
                    content = content.replace(b"\r\n", b"\n")
                    
                    # Write back as raw bytes
                    with open(path, "wb") as f:
                        f.write(content)
                    count += 1
                    print(f"Fixed: {path}")
            except PermissionError:
                print(f"Skipped (permission denied): {path}")
            except Exception as e:
                print(f"Skipped ({path}): {e}")

print(f"Conversion completed successfully. {count} files fixed.")
print("Binaries and UTF-8 characters left intact.")
