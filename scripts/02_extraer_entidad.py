import json, re, shutil, sys, time, urllib.request, zipfile
from pathlib import Path
import ijson

BUYER_ID = "PE-CONSUCODE-205010"
BUYER_RUC = "20608197037"
UA = {"User-Agent": "san-antonio-monitor/1.0"}
CAT = Path("config/oece_files_catalog.json")
INC = Path("data/incoming/oece")
OUT = Path("data/evidence/by_month")
INC.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

def is_target(cr):
    if (cr.get("buyer") or {}).get("id") == BUYER_ID:
        return True
    for p in cr.get("parties") or []:
        if "buyer" not in (p.get("roles") or []):
            continue
        if p.get("id") == BUYER_ID:
            return True
        for a in p.get("additionalIdentifiers") or []:
            if a.get("scheme") == "PE-RUC" and a.get("id") == BUYER_RUC:
                return True
    return False

def alt_candidate(cr):
    for p in cr.get("parties") or []:
        if "buyer" in (p.get("roles") or []):
            nm = (p.get("name") or "").upper()
            dep = ((p.get("address") or {}).get("department") or "").upper()
            if "SAN ANTONIO" in nm and dep == "MOQUEGUA" and p.get("id") != BUYER_ID:
                return (p.get("id"), p.get("name"))
    return None

def download(url, dest):
    tmp = dest.with_suffix(".part")
    for i in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            if not zipfile.is_zipfile(tmp):
                raise RuntimeError("No es ZIP valido")
            tmp.rename(dest)
            return
        except Exception as e:
            print(f"  reintento {i}/3: {e}")
            time.sleep(5)
    raise RuntimeError(f"Fallo descarga: {url}")

def months_from_catalog():
    res = {}
    for it in json.loads(CAT.read_text(encoding="utf-8")):
        u = (it.get("files") or {}).get("json", "")
        m = re.search(r"/json/(\d{4})/(\d{2})/", u); u = u.replace("osce.gob.pe", "oece.gob.pe")
        if m and 2020 <= int(m.group(1)) <= 2026:
            res[f"{m.group(1)}-{m.group(2)}"] = u
    return dict(sorted(res.items()))

def process(ym, url):
    final = OUT / f"{ym}.jsonl"
    if final.exists():
        print(f"{ym}: ya procesado, se omite")
        return 0, 0
    zp = INC / f"{ym}_seace_v3_json.zip"
    if not zp.exists():
        download(url.replace("osce.gob.pe", "oece.gob.pe"), zp)
    tmp = final.with_suffix(".tmp")
    scanned = hits = 0
    alts = set()
    with zipfile.ZipFile(zp) as z, z.open(z.namelist()[0]) as f, tmp.open("w", encoding="utf-8") as out:
        for rec in ijson.items(f, "records.item", use_float=True):
            scanned += 1
            cr = rec.get("compiledRelease") or {}
            a = alt_candidate(cr)
            if a:
                alts.add(a)
            if is_target(cr):
                out.write(json.dumps({"month": ym, "source_url": url, "record": rec}, ensure_ascii=False) + "\n")
                hits += 1
    tmp.rename(final)
    zp.unlink()
    print(f"{ym}: registros={scanned} entidad={hits}" + (f" ALERTA_OTRO_ID={sorted(alts)}" if alts else ""))
    return scanned, hits

if __name__ == "__main__":
    print("ijson backend:", ijson.backend)
    cat = months_from_catalog()
    sel = sys.argv[1:] or list(cat)
    tot_s = tot_h = 0
    for ym in sel:
        s, h = process(ym, cat[ym])
        tot_s += s; tot_h += h
    print(f"TOTAL registros={tot_s} entidad={tot_h}")
