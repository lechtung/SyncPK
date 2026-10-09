#!/usr/bin/env python3
# Safe CRLF to LF converter for Windows/Linux Development
# This script operates purely on bytes, so it CANNOT corrupt UTF-8 accents or BOMs.

import os

# Whitelist of safe text file extensions
EXTENSIONS = {
    ".html", ".css", ".py", ".js", ".json", 
    ".txt", ".sh", ".md", ".yml", ".yaml"
}

# Explicit filenames that might not have standard extensions
EXACT_FILES = {
    ".gitignore", ".gitattributes"
}

print("Starting safe CRLF to LF conversion...")

count = 0
for root, dirs, files in os.walk("."):
    # Skip standard heavy/hidden directories
    if ".git" in root or "venv" in root or "__pycache__" in root:
        continue

    for file in files:
        ext = os.path.splitext(file)[1].lower()
        
        # Check if the file matches our safe whitelist
        if ext in EXTENSIONS or file in EXACT_FILES or file.startswith(".env"):
            path = os.path.join(root, file)
            
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

print(f"Conversion completed successfully. {count} files fixed.")
print("Binaries and UTF-8 characters left intact.")
