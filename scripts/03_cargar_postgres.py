import glob, json, hashlib, re, unicodedata
from collections import Counter
from decimal import Decimal
import psycopg
from psycopg.types.json import Jsonb

CONN = "host=127.0.0.1 port=5433 dbname=licitaciones_san_antonio user=san_antonio_etl"

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace('"', " ")
    return re.sub(r"\s+", " ", s).strip()

def clasifica(skey, name):
    num = skey.replace("PE-RUC-", "")
    es_ruc = bool(re.fullmatch(r"\d{11}", num))
    n = norm(name)
    if n.startswith("CONSORCIO"):
        return (num if es_ruc else None), "SI", "Consorcio", "nombre"
    if not es_ruc:
        return None, "NO DISPONIBLE", "No disponible", None
    t = n.replace(".", "")
    t = re.sub(r"[^A-Z0-9& ]", " ", t)
    for pat, lab in [
        (r"\bEIRL\b|EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA", "E.I.R.L."),
        (r"\bSCRL\b|SOCIEDAD COMERCIAL DE RESPONSABILIDAD LIMITADA", "S.C.R.L."),
        (r"\bSRL\b|SOCIEDAD DE RESPONSABILIDAD LIMITADA", "S.R.L."),
        (r"\bSAC\b|SOCIEDAD ANONIMA CERRADA", "S.A.C."),
        (r"\bSAA\b|SOCIEDAD ANONIMA ABIERTA", "S.A.A."),
        (r"\bSA\b|SOCIEDAD ANONIMA", "S.A."),
    ]:
        if re.search(pat, t):
            return num, "NO", lab, "nombre"
    if num.startswith("10"):
        return num, "NO", "Persona natural", "ruc_prefijo_10"
    return num, "NO", "No disponible", None

def same(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    nums = (int, float, Decimal)
    if isinstance(a, nums) and isinstance(b, nums) and not isinstance(a, bool) and not isinstance(b, bool):
        return Decimal(str(a)) == Decimal(str(b))
    return str(a) == str(b)

def statuses(items):
    s = sorted({i.get("statusDetails") for i in (items or []) if i.get("statusDetails")})
    return "/".join(s) or None

def load_rows():
    for fp in sorted(glob.glob("data/evidence/by_month/*.jsonl")):
        for line in open(fp, encoding="utf-8"):
            yield json.loads(line)

stats = Counter()
with psycopg.connect(CONN) as conn, conn.cursor() as cur:
    for row in load_rows():
        rec = row["record"]; cr = rec["compiledRelease"]; ocid = cr["ocid"]
        t = cr.get("tender") or {}
        buyer = cr.get("buyer") or {}
        text = json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        tp = t.get("tenderPeriod") or {}
        val = t.get("value") or {}
        new = {
            "title": t.get("title"),
            "procurement_method_details": t.get("procurementMethodDetails"),
            "main_category": t.get("mainProcurementCategory"),
            "value_amount": val.get("amount"),
            "tender_end": tp.get("endDate"),
            "number_of_tenderers": t.get("numberOfTenderers"),
            "n_participaciones": len(t.get("tenderers") or []),
            "n_adjudicaciones": len(cr.get("awards") or []),
            "n_contratos": len(cr.get("contracts") or []),
        }
        cur.execute("""select p.title, p.procurement_method_details, p.main_category, p.value_amount,
                       p.tender_end, p.number_of_tenderers,
                       (select count(*) from core.participaciones where ocid=p.ocid),
                       (select count(*) from core.adjudicaciones where ocid=p.ocid),
                       (select count(*) from core.contratos where ocid=p.ocid)
                       from core.procesos p where p.ocid=%s""", (ocid,))
        old = cur.fetchone()
        if old:
            for k, ov in zip(new.keys(), old):
                if not same(ov, new[k]):
                    if k in ("tender_end",):
                        continue  # comparacion de timestamps se hace en otra etapa
                    cur.execute("insert into audit.cambios(ocid,campo,valor_anterior,valor_nuevo) values (%s,%s,%s,%s)",
                                (ocid, k, str(ov), str(new[k])))
                    stats["cambios"] += 1

        cur.execute("""insert into raw.ocds_procesos(ocid,source_month,record_hash,source_url,record_json)
                       values (%s,%s,%s,%s,%s)
                       on conflict (ocid) do update set source_month=excluded.source_month,
                       record_hash=excluded.record_hash, source_url=excluded.source_url,
                       record_json=excluded.record_json, retrieved_at=now()""",
                    (ocid, row["month"], h, row.get("source_url"), Jsonb(rec)))
        cur.execute("""insert into core.procesos(ocid,entity_id,entity_name,title,procurement_method,
                       procurement_method_details,main_category,release_date,published_at,tender_start,
                       tender_end,value_amount,value_currency,number_of_tenderers,source_month,source_url)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       on conflict (ocid) do update set title=excluded.title,
                       procurement_method=excluded.procurement_method,
                       procurement_method_details=excluded.procurement_method_details,
                       main_category=excluded.main_category, release_date=excluded.release_date,
                       published_at=excluded.published_at, tender_start=excluded.tender_start,
                       tender_end=excluded.tender_end, value_amount=excluded.value_amount,
                       value_currency=excluded.value_currency,
                       number_of_tenderers=excluded.number_of_tenderers,
                       source_month=excluded.source_month, source_url=excluded.source_url,
                       last_seen_at=now()""",
                    (ocid, str(buyer.get("id", "")).replace("PE-CONSUCODE-", ""), buyer.get("name"),
                     t.get("title"), t.get("procurementMethod"), t.get("procurementMethodDetails"),
                     t.get("mainProcurementCategory"), cr.get("date"), t.get("datePublished"),
                     tp.get("startDate"), tp.get("endDate"), val.get("amount"), val.get("currency"),
                     t.get("numberOfTenderers"), row["month"], row.get("source_url")))
        stats["procesos"] += 1

        names = {}
        for p in cr.get("parties") or []:
            if "buyer" in (p.get("roles") or []): continue
            names[p["id"]] = p.get("name") or ""
        for x in t.get("tenderers") or []:
            names.setdefault(x["id"], x.get("name") or "")
        for a in cr.get("awards") or []:
            for s in a.get("suppliers") or []:
                names.setdefault(s["id"], s.get("name") or "")
        for sk, nm in names.items():
            ruc, cons, tipo, fuente = clasifica(sk, nm)
            cur.execute("""insert into core.proveedores(supplier_key,ruc,name_original,name_normalized,
                           tipo_empresa,tipo_empresa_fuente,es_consorcio) values (%s,%s,%s,%s,%s,%s,%s)
                           on conflict (supplier_key) do update set ruc=excluded.ruc,
                           name_original=excluded.name_original, name_normalized=excluded.name_normalized,
                           tipo_empresa=excluded.tipo_empresa, tipo_empresa_fuente=excluded.tipo_empresa_fuente,
                           es_consorcio=excluded.es_consorcio""",
                        (sk, ruc, nm, norm(nm), tipo, fuente, cons))

        cur.execute("delete from core.participaciones where ocid=%s", (ocid,))
        cur.execute("delete from core.adjudicaciones where ocid=%s", (ocid,))
        cur.execute("delete from core.contratos where ocid=%s", (ocid,))

        for x in t.get("tenderers") or []:
            cur.execute("insert into core.participaciones(ocid,supplier_key) values (%s,%s) on conflict do nothing",
                        (ocid, x["id"]))
            stats["participaciones"] += cur.rowcount
            if cur.rowcount == 0: stats["participaciones_duplicadas"] += 1

        award_sup = {}
        for a in cr.get("awards") or []:
            sups = a.get("suppliers") or []
            if len(sups) > 1: stats["awards_multi_proveedor"] += 1
            sk = sups[0]["id"] if sups else None
            award_sup[a.get("id")] = sk
            av = a.get("value") or {}
            cur.execute("""insert into core.adjudicaciones(ocid,award_id,supplier_key,award_date,amount,currency,status_detail)
                           values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                        (ocid, a["id"], sk, a.get("date"), av.get("amount"), av.get("currency"), statuses(a.get("items"))))
            stats["adjudicaciones"] += cur.rowcount

        for c in cr.get("contracts") or []:
            cv = c.get("value") or {}; per = c.get("period") or {}
            docs = [d.get("url") for d in (c.get("documents") or []) if d.get("url")]
            cur.execute("""insert into core.contratos(ocid,contract_id,award_id,supplier_key,signed_date,start_date,
                           end_date,amount,currency,status_detail,document_url)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                        (ocid, c["id"], c.get("awardID"), award_sup.get(c.get("awardID")), c.get("dateSigned"),
                         per.get("startDate"), per.get("endDate"), cv.get("amount"), cv.get("currency"),
                         statuses(c.get("items")), docs[0] if docs else None))
            stats["contratos"] += cur.rowcount
    conn.commit()
print(dict(stats))
