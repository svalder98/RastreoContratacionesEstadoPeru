import json
import time
import urllib.request
from pathlib import Path

BASE = "https://contratacionesabiertas.oece.gob.pe/api/v1/files"
OUT = Path("config/oece_files_catalog.json")

all_items = []
page = 1
while True:
    url = f"{BASE}?page={page}&paginateBy=10&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": "san-antonio-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    all_items.extend(data.get("results", []))
    if not (data.get("pagination") or {}).get("has_next"):
        break
    page += 1
    time.sleep(0.2)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Paginas leidas: {page}")
print(f"Registros de catalogo: {len(all_items)}")
print(f"Archivo: {OUT}")
