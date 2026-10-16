#!/usr/bin/env python3
"""
SyncPK i18n Localization Auditor
Scans HTML and JS files for translation keys, ensures they exist in all 8 locales,
and verifies 100% key parity across languages.
Can be executed from any directory.
"""

import json
import re
import glob
import os
import sys
from pathlib import Path

# Resolve project root regardless of where the script is invoked from
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = PROJECT_ROOT / 'server' / 'static' / 'locales'

if not LOCALES_DIR.exists():
    print(f"[ERROR] Could not find locales directory at: {LOCALES_DIR}")
    sys.exit(1)

# Load all locale JSON files
locales = {}
for path in sorted(glob.glob(str(LOCALES_DIR / '*.json'))):
    lang = Path(path).stem
    with open(path, 'r', encoding='utf-8') as f:
        locales[lang] = json.load(f)

print(f"=== SyncPK i18n Audit ({len(locales)} languages: {', '.join(sorted(locales.keys()))}) ===")

has_errors = False

# 1. Audit data-i18n attributes in HTML templates
html_files = [
    PROJECT_ROOT / 'server' / 'static' / 'dashboard.html',
    PROJECT_ROOT / 'server' / 'static' / 'setup.html',
    PROJECT_ROOT / 'server' / 'static' / 'import.html'
]

html_keys = {}
for html_path in html_files:
    if html_path.exists():
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        matches = re.findall(r'data-i18n=["\']([a-zA-Z0-9_]+)["\']', content)
        matches += re.findall(r'data-i18n-placeholder=["\']([a-zA-Z0-9_]+)["\']', content)
        matches += re.findall(r'data-i18n-title=["\']([a-zA-Z0-9_]+)["\']', content)
        rel_name = html_path.name
        for m in matches:
            html_keys.setdefault(m, set()).add(rel_name)

missing_html = []
for k in sorted(html_keys.keys()):
    missing_in = [lang for lang in sorted(locales.keys()) if k not in locales[lang]]
    if missing_in:
        origins = ", ".join(sorted(html_keys[k]))
        missing_html.append(f'  - Key "{k}" (in {origins}) missing in: {missing_in}')

if missing_html:
    has_errors = True
    print("\n[!] Missing HTML data-i18n keys:")
    print("\n".join(missing_html))
else:
    print(f"\n[OK] All {len(html_keys)} HTML data-i18n keys are present in all locales.")

# 2. Audit dynamic JS keys
js_files = [
    PROJECT_ROOT / 'server' / 'static' / 'app.js',
    PROJECT_ROOT / 'server' / 'static' / 'setup.js',
    PROJECT_ROOT / 'server' / 'static' / 'import.js'
]

js_keys = {}
for js_path in js_files:
    if js_path.exists():
        with open(js_path, 'r', encoding='utf-8') as f:
            content = f.read()
        rel_name = js_path.name
        # Match t("key"), currentLangData.key, currentLangData["key"], getTranslation("key")
        matches = re.findall(r'\bcurrentLangData\.([a-zA-Z0-9_]+)\b', content)
        matches += re.findall(r'\bcurrentLangData\[[\'\"]([a-zA-Z0-9_]+)[\'\"]\]', content)
        matches += re.findall(r'\bt\([\'"]([a-zA-Z0-9_]+)[\'"]\)', content)
        matches += re.findall(r'\bgetTranslation\([\'"]([a-zA-Z0-9_]+)[\'"]\)', content)
        for m in set(matches):
            js_keys.setdefault(m, set()).add(rel_name)

missing_js = []
for k in sorted(js_keys.keys()):
    missing_in = [lang for lang in sorted(locales.keys()) if k not in locales[lang]]
    if missing_in:
        origins = ", ".join(sorted(js_keys[k]))
        missing_js.append(f'  - Key "{k}" (in {origins}) missing in: {missing_in}')

if missing_js:
    has_errors = True
    print("\n[!] Missing JS dynamic i18n keys:")
    print("\n".join(missing_js))
else:
    print(f"[OK] All {len(js_keys)} JS dynamic i18n keys are present in all locales.")

# 3. Audit parity across all locales against en.json
en_keys = set(locales.get('en', {}).keys())
parity_errors = []
for lang in sorted(locales.keys()):
    if lang == 'en':
        continue
    missing_keys = en_keys - set(locales[lang].keys())
    extra_keys = set(locales[lang].keys()) - en_keys
    if missing_keys:
        parity_errors.append(f'  - {lang}: missing {len(missing_keys)} keys from en.json: {sorted(list(missing_keys))}')
    if extra_keys:
        parity_errors.append(f'  - {lang}: has {len(extra_keys)} extra keys not in en.json: {sorted(list(extra_keys))}')

if parity_errors:
    has_errors = True
    print("\n[!] Locale key parity discrepancies:")
    print("\n".join(parity_errors))
else:
    print(f"[OK] 100% key parity across all locales ({len(en_keys)} total keys in each file).")

print("\n" + ("=" * 55))
if has_errors:
    print("RESULT: FAIL - Missing translation keys detected.")
    sys.exit(1)
else:
    print("RESULT: SUCCESS - All translations are complete and in sync!")
    sys.exit(0)
