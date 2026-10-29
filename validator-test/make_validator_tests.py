"""Fake catalogues to test the harvester validators (one case per dataset).

Built on the recipe of add_comunes.py (ckanext-datitrentinoit/harvesters):
same RAW_BASE repo, same phases and same APPA item/resource shapes, extended
with one validator case per dataset and an expected.json per harvester type.

  PHASE 1  generate_files()   -> validator-test/<type>/ (catalogue, resources,
                                 expected.json, runner_<type>.py)
           push validator-test/ to GitHub, check one catalogue URL loads
  PHASE 2  update_sources()   -> create/repoint the harvest sources vt-<type>
  PHASE 2b clear_all()        -> wipe the vt-* sources (UI "Clear")
  PHASE 3  reharvest_all()    -> queue a job per vt-* source
           then compare with <type>/runner_<type>.py (in the ckan shell)

DRY_RUN = True by default: the CKAN phases only print what they would do.
The API token is read from the environment (CKAN_API_TOKEN), never written here.

Usage:  py -I make_validator_tests.py [output_dir]      (PHASE 1 only)
"""
import copy
import hashlib
import json
import os
import shutil
import sys
import time

# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────
CKAN_URL    = "https://cot.cloud-test-intra.tndigit.it"
API_TOKEN   = os.environ.get("CKAN_API_TOKEN", "")   # never hard-code it
OWNER_ORG   = "tndigit"            # see README: organisation options
RAW_BASE    = "https://raw.githubusercontent.com/ahmadsaad-ds/comuni-test/refs/heads/main"
FREQUENCY   = "MANUAL"             # the test sources must not run by themselves
DRY_RUN     = True                 # True = print only, create nothing
# ─────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
RAW = RAW_BASE + "/validator-test"

# One harvest source per type: name, title, source_type, catalogue file, config.
SOURCES = {
    "appa":   ("vt-appa",   "VT validatori APPA",   "opencity_appa",         "catalogo.json", ""),
    "comuni": ("vt-comuni", "VT validatori Comuni", "opencity_municipality", "catalogo.json", "{}"),
    "pat":    ("vt-pat",    "VT validatori PAT",    "opencity_pat",          "catalogo.json", ""),
    "ispat":  ("vt-ispat",  "VT validatori ISPAT",  "tn_statweb_pro",        "index.json",    ""),
}

# Links whose real behaviour was checked with eu_taxonomies.detect_format on the pod.
CSV_URL = "https://www.comune.mori.tn.it/exportas/csv/place"                       # CSV
CSV_URL_2 = "https://www.comune.mori.tn.it/exportas/csv/organization"              # CSV
JSON_URL = "https://dati.trentino.it/api/3/action/status_show"                     # JSON
MORI_JSON_URL = ("https://www.comune.mori.tn.it/opendata/api/content/search/"
                 "classes%20%5Bplace%5D")                                           # JSON
HTML_URL = "https://www.comune.mori.tn.it/"                                        # HTML (web page)
URL_404 = RAW + "/does-not-exist.csv"                                              # 404
JSON_404 = RAW + "/does-not-exist.json"                                            # 404
PRIVATE_SHEET = ("https://docs.google.com/spreadsheets/d/"
                 "1mjFUUw4-kR_Ms9cdL3wd5r3qmEnzk-t3NaCzP48ggcI/export?format=csv")  # 401
SHEET_EDIT = ("https://docs.google.com/spreadsheets/d/"
              "1m74kuWPyuGvBjA2qTe68TTJn5Mb-YiqrYAZhrupowzc/edit?usp=sharing")      # HTML
SHEET_WITH_RANGE = SHEET_EDIT + '";"Elenco!A1:H'                                   # OpenCity suffix
# Any file on raw.githubusercontent.com is served as text/plain, which is not
# mapped to an EU format: detect_format gives 'unknown'.
TEXT_PLAIN_URL = RAW + "/plain-text-sample.csv"
STATWEB_JSON = "https://statweb.provincia.tn.it/indicatoristrutturali/exp.aspx?fmt=json&idind=597&t=i"
STATWEB_CSV = "https://statweb.provincia.tn.it/indicatoristrutturali/exp.aspx?fmt=csv&idind=597&t=i"

# Licence labels: OpenCity must send the full dcatapit label, otherwise the
# resource licence (exact label match in the DB) is not recognised.
CC_BY = "Creative Commons Attribution 4.0 International (CC BY 4.0)"
CC0 = "Creative Commons CC0 1.0 Universal - Public Domain Dedication (CC0 1.0)"
CC_BY_URI = "https://w3id.org/italia/controlled-vocabulary/licences/A21_CCBY40"
# Licenza reale di un dataset di Pergine: riconoscibile, ma non ammessa.
CC_BY_1 = "Creative Commons Attribution 1.0 Generic (CC BY 1.0)"

# Real public PAT offices (no login), fetched in fetch_stage as in production.
PAT_PUBLISHER = [{"id": "structureP330", "uri": "https://www.provincia.tn.it/api/openapi/amministrazione/strutture-organizzative/structureP330#Umst-digitalizzazione-e-reti", "priority": 1}]
PAT_CONTACT = [{"id": "contactpointP330", "uri": "https://www.provincia.tn.it/api/openapi/media/classificazioni/punti-di-contatto/contactpointP330#Contatti-di-Umst-digitalizzazione-e-reti", "priority": 1}]

DATE = "2026-10-26"


def uid(text):
    return hashlib.sha1(text.encode()).hexdigest()[:32]


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def imported(detail, **checks):
    return {"outcome": "imported", "detail": detail, "checks": checks}


def rejected(detail, stage="import"):
    return {"outcome": "rejected", "detail": detail, "stage": stage}


def skipped(detail):
    return {"outcome": "skipped", "detail": detail}


def desc(case, exp):
    return f"Dataset di prova per i validatori dell'harvester. Caso {case}. Esito atteso: {exp['outcome']} - {exp['detail']}"


# --- APPA ----------------------------------------------------------------------
# List {"items": [...], "next": null}; each item's "distribution" URIs are
# fetched in fetch_stage and give the resources.

APPA_DATASET = {
    "is_public": True,
    "short_description": "",
    "resources": [],
    "download_file": [],
    "format": [],
    "license": [CC_BY],
    "language": ["Italiano"],
    "version_info": "",
    "standard": "",
    "topics": [],
    "theme": ["Governo e settore pubblico"],
    "rights_holder": "Provincia autonoma di Trento",
    # As in add_comunes.py: no office URIs to fetch.
    "publisher": None,
    "creator": [],
    "contact_point": None,
    "issued": DATE,
    "modified": DATE,
    "accrualperiodicity": ["Mensile"],
    "start_date": None,
    "end_date": None,
    "spatial_coverage": "https://www.geonames.org/3165241",
    "spatial": [],
    "conforms_to": [],
    "related_document": [],
    "download_url": None,
    "keyword": ["test", "validatori"],
    "transparency_section": [],
    "resource_name": "TypedDataset",
}
APPA_RESOURCE = {
    "is_public": True,
    "short_description": "",
    "description_oc": "",
    "download_url": None,
    "access_url": CSV_URL + "|File CSV",
    "topics": [],
    "modified": DATE + "T00:00:00",
    "license": [CC_BY],
    "language": ["Italiano"],
    "format": ["CSV"],
    "transparency_section": [],
    "keyword": [],
    "resource_name": "TypedDistribution",
}

OK = dict(frequency="MONTHLY", language="ITA", license_id="cc-by")

# (case, expected, dataset changes, list of resource changes; None = distribution URI that 404s)
APPA_CASES = [
    ("01-ok-csv", imported("baseline", formats=["CSV"], **OK), {}, [{}]),
    ("02-ok-json-cc0-deutsch-freq-code",
     imported("CC0, deutsch, frequency given as EU code", formats=["JSON"], frequency="MONTHLY", language="DEU", license_id="cc-zero"),
     {"license": [CC0], "language": ["deutsch"], "accrualperiodicity": ["MONTHLY"]},
     [{"access_url": JSON_URL + "|File JSON", "format": ["JSON"], "license": [CC0]}]),
    ("03-ok-english-update-cont",
     imported("english, 'In continuo aggiornamento'", formats=["CSV"], frequency="UPDATE_CONT", language="ENG", license_id="cc-by"),
     {"language": ["English"], "accrualperiodicity": ["In continuo aggiornamento"]}, [{}]),
    ("04-ok-frequency-mai", imported("'mai' = NEVER", formats=["CSV"], frequency="NEVER", language="ITA", license_id="cc-by"),
     {"accrualperiodicity": ["mai"]}, [{}]),
    ("05-ok-frequency-irregolare", imported("'Irregolare' = IRREG", formats=["CSV"], frequency="IRREG", language="ITA", license_id="cc-by"),
     {"accrualperiodicity": ["Irregolare"]}, [{}]),
    ("06-ok-format-corrected", imported("declared JSON, file is CSV: the declared format is kept (the file is not downloaded)", formats=["JSON"], **OK),
     {}, [{"format": ["JSON"]}]),
    ("07-ok-download-url-object", imported("link in download_url.uri instead of access_url", formats=["CSV"], **OK),
     {}, [{"access_url": None, "download_url": {"uri": CSV_URL}}]),
    ("08-ok-web-page-declared-csv", imported("web page where a CSV was declared: kept as CSV (the file is not downloaded)", formats=["CSV"], **OK),
     {}, [{"access_url": HTML_URL + "|Pagina"}]),
    ("09-ok-google-sheet-range-suffix", imported("OpenCity sheet link with '\";\"range' suffix: cleaned, declared CSV kept", formats=["CSV"], urls=[SHEET_EDIT], **OK),
     {}, [{"access_url": SHEET_WITH_RANGE + "|Elenco"}]),
    ("10-ok-one-resource-without-url-dropped", rejected("one resource without URL: the whole dataset is rejected (04/05)"),
     {}, [{}, {"access_url": ""}]),
    ("11-ok-language-missing", imported("no language: OP_DATPRO (not declared), accepted", formats=["CSV"], frequency="MONTHLY", language="OP_DATPRO", license_id="cc-by"),
     {"language": []}, [{}]),
    ("12-dataset-licence-wrong", rejected("dataset licence 'GPL 3.0' not accepted"), {"license": ["GPL 3.0"]}, [{}]),
    ("13-dataset-licence-missing", imported("dataset licence missing: default cc-by", formats=["CSV"], **OK), {"license": []}, [{}]),
    ("14-resource-licence-wrong", rejected("resource licence 'GPL 3.0' not accepted"), {}, [{"license": ["GPL 3.0"]}]),
    ("15-resource-licence-missing", imported("resource licence missing: default CC BY 4.0", formats=["CSV"], license_types=[CC_BY_URI], **OK),
     {}, [{"license": []}]),
    ("16-resource-licence-short-label", rejected("resource licence 'CC BY 4.0' (short label) not found among the dcatapit labels -> not accepted"),
     {}, [{"license": ["CC BY 4.0"]}]),
    ("17-language-wrong", rejected("language 'francais' not accepted"), {"language": ["francais"]}, [{}]),
    ("18-frequency-missing", rejected("frequency missing"), {"accrualperiodicity": []}, [{}]),
    ("19-frequency-wrong", rejected("frequency 'ogni tanto' not in the EU vocabulary"), {"accrualperiodicity": ["ogni tanto"]}, [{}]),
    ("20-frequency-pat-label", rejected("frequency 'Non viene aggiornato (mai)' not in the EU vocabulary"),
     {"accrualperiodicity": ["Non viene aggiornato (mai)"]}, [{}]),
    ("21-no-distribution", rejected("no resource has a download URL"), {}, []),
    ("22-resource-without-url", rejected("no resource has a download URL"), {}, [{"access_url": ""}]),
    ("23-distribution-uri-404", rejected("resource JSON not found in fetch_stage -> no resource has a download URL"), {}, [None]),
    ("24-link-404", imported("404 link: imported (the file is not downloaded)", formats=["CSV"], **OK), {}, [{"access_url": URL_404 + "|File"}]),
    ("25-private-google-sheet", imported("private sheet (401): imported (the file is not downloaded)", formats=["CSV"], **OK),
     {}, [{"access_url": PRIVATE_SHEET + "|Foglio"}]),
    ("26-format-unknown-text-plain", imported("file served as text/plain: imported, declared CSV kept", formats=["CSV"], **OK),
     {}, [{"access_url": TEXT_PLAIN_URL + "|File"}]),
    ("27-one-good-one-broken-resource", imported("second link 404: imported (the files are not downloaded)", formats=["CSV", "CSV"], n_resources=2, **OK),
     {}, [{}, {"access_url": URL_404 + "|File rotto"}]),
    ("28-identifier-missing", rejected("identifier missing (dcatapit mandatory field, schema check)"), {"identifier": ""}, [{}]),
    # Nuovi casi (regole del 28/10)
    ("29-resource-licence-cc-by-1", rejected("resource licence 'CC BY 1.0' (real Pergine label) not accepted"),
     {}, [{"license": [CC_BY_1]}]),
    ("30-dataset-licence-cc-by-1", rejected("dataset licence 'CC BY 1.0' not accepted"), {"license": [CC_BY_1]}, [{}]),
    ("31-format-lowercase", imported("format 'json' in lower case: normalised to JSON", formats=["JSON"], **OK),
     {}, [{"access_url": JSON_URL + "|File JSON", "format": ["json"]}]),
    ("32-format-excel", rejected("format 'EXCEL' not in the EU file type vocabulary"), {}, [{"format": ["EXCEL"]}]),
    ("33-format-missing", rejected("format missing"), {}, [{"format": []}]),
    ("34-frequency-eu-code", imported("frequency given as EU code 'ANNUAL'", formats=["CSV"], frequency="ANNUAL", language="ITA", license_id="cc-by"),
     {"accrualperiodicity": ["ANNUAL"]}, [{}]),
    ("35-language-inglese", imported("language 'Inglese' (as OpenCity writes it): ENG", formats=["CSV"], frequency="MONTHLY", language="ENG", license_id="cc-by"),
     {"language": ["Inglese"]}, [{}]),
    ("36-licence-cc0", imported("CC0 on dataset and resource", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-zero"),
     {"license": [CC0]}, [{"license": [CC0]}]),
]


def make_appa(out):
    base = RAW + "/appa"
    items, expected = [], {}
    for case, exp, changes, resources in APPA_CASES:
        guid = uid("vt-appa-" + case)
        item = copy.deepcopy(APPA_DATASET)
        item.update({
            "id": guid,
            "uri": f"{base}/catalogo.json#{case}",
            "published_at": DATE + "T09:00:00+02:00",
            "modified_at": DATE + "T09:00:00+02:00",
            "title": f"VT APPA {case}",
            "identifier": f"vt-appa-{case}",
            "short_description": f"Caso di prova {case}",
            "description": f"<p>{desc(case, exp)}</p>",
            "distribution": [],
        })
        item.update(changes)
        for n, res_changes in enumerate(resources, 1):
            name = f"{case}-{n}"
            if res_changes is None:
                # Points to a resource JSON that is not published.
                item["distribution"].append({"id": uid(name), "uri": f"{base}/resources/missing-{name}.json", "priority": n})
                continue
            resource = copy.deepcopy(APPA_RESOURCE)
            resource.update({
                "id": uid(name),
                "uri": f"{base}/resources/{name}.json",
                "published_at": DATE + "T09:00:00+02:00",
                "modified_at": DATE + "T09:00:00+02:00",
                "title": f"Risorsa {n} di {case}",
                "description": "<p>Risorsa di prova</p>",
            })
            resource.update(res_changes)
            write_json(os.path.join(out, "resources", name + ".json"), resource)
            item["distribution"].append({"id": uid(name), "uri": f"{base}/resources/{name}.json", "priority": n})
        items.append(item)
        expected[guid] = {"case": case, **exp}
    write_json(os.path.join(out, "catalogo.json"), {"items": items, "self": None, "prev": None, "next": None, "count": len(items)})
    write_json(os.path.join(out, "expected.json"), expected)
    return base + "/catalogo.json", len(items)


# --- Comuni (opencity_municipality) ----------------------------------------------
# Same listing as APPA, but resources are inside the record ("download_url"
# "url|name" + "resources"[].url_download). contact_point / publisher / creator
# are empty, as on Mori: in production they are URIs readable only with login.

COMUNI_RECORD = {
    "is_public": True,
    "theme": ["Governo e settore pubblico"],
    "topics": [],
    "format": ["JSON"],
    "license": [CC_BY],
    "resources": [
        {"index": 0, "title": "Luoghi in formato JSON", "url_download": MORI_JSON_URL, "format": "JSON"},
        {"index": 1, "title": "Luoghi in formato CSV", "url_download": CSV_URL, "format": "CSV"},
    ],
    "download_url": None,
    "download_file": [],
    "csv_resource": None,
    "modified": DATE,
    "rights_holder": [],
    "accrualperiodicity": ["Mensile"],
    "spatial": [],
    "issued": DATE,
    "temporal": [],
    "creator": [],
    "publisher": [],
    "conforms_to": [],
    "language": ["Italiano"],
    "version_info": "",
    "contact_point": [],
    "keyword": "",
    "start_date": None,
    "end_date": None,
}
COK = dict(formats=["JSON", "CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by")
ONE_RES = {"resources": [], "format": ["CSV"]}


def _res(url, fmt="CSV", title="File"):
    return {"resources": [{"index": 0, "title": title, "url_download": url, "format": fmt}], "format": [fmt]}


COMUNI_CASES = [
    ("01-ok-json-csv", imported("baseline, like the real Mori records (JSON + CSV)", **COK), {}),
    ("02-ok-cc0-deutsch-freq-code",
     imported("CC0, deutsch, frequency given as EU code", formats=["JSON", "CSV"], frequency="MONTHLY", language="DEU", license_id="cc-zero"),
     {"license": [CC0], "language": ["Deutsch"], "accrualperiodicity": ["MONTHLY"]}),
    ("03-ok-english-update-cont",
     imported("english, 'In continuo aggiornamento'", formats=["JSON", "CSV"], frequency="UPDATE_CONT", language="ENG", license_id="cc-by"),
     {"language": ["English"], "accrualperiodicity": ["In continuo aggiornamento"]}),
    ("04-ok-download-url-only", imported("single resource from download_url 'url|name'", formats=["CSV"], n_resources=1, frequency="MONTHLY", language="ITA", license_id="cc-by"),
     {**ONE_RES, "download_url": CSV_URL + "|Elenco luoghi"}),
    ("05-ok-frequency-mai", imported("'mai' = NEVER", formats=["JSON", "CSV"], frequency="NEVER", language="ITA", license_id="cc-by"),
     {"accrualperiodicity": ["mai"]}),
    ("06-ok-formats-swapped", imported("declared formats swapped (as on a real Mori record): declared formats kept (files not downloaded)",
                                       formats=["CSV", "JSON"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     {"resources": [
         {"index": 0, "title": "Luoghi in formato JSON", "url_download": MORI_JSON_URL, "format": "CSV"},
         {"index": 1, "title": "Luoghi in formato CSV", "url_download": CSV_URL, "format": "JSON"}]}),
    ("07-ok-web-page", imported("web page link declared CSV: kept as CSV (file not downloaded)", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     {**ONE_RES, "download_url": HTML_URL + "|Pagina"}),
    ("08-ok-google-sheet-range-suffix", imported("OpenCity sheet link with '\";\"range' suffix: cleaned, declared CSV kept", formats=["CSV"], urls=[SHEET_EDIT], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     {**ONE_RES, "download_url": SHEET_WITH_RANGE + "|Elenco"}),
    ("09-ok-domain-without-scheme", imported("link 'www.comune...' without https://: scheme added", formats=["CSV"], urls=[CSV_URL], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     _res(CSV_URL.replace("https://", ""))),
    ("10-ok-identifier-empty", imported("no identifier: built as <IPA of the source organisation>_<guid>", identifier="{ipa}_{guid}", **COK),
     {"identifier": ""}),
    ("11-ok-language-missing", imported("no language: OP_DATPRO (not declared), accepted", formats=["JSON", "CSV"], frequency="MONTHLY", language="OP_DATPRO", license_id="cc-by"),
     {"language": []}),
    ("12-ok-catalogo-od-true", imported("catalogo_od true (baseline has no catalogo_od, like Mori)", **COK), {"catalogo_od": True}),
    ("13-skip-catalogo-od-false", skipped("catalogo_od false: not queued in gather_stage"), {"catalogo_od": False}),
    ("14-skip-not-public", skipped("is_public false: not queued (imported only with \"include_private\": true in the source config)"),
     {"is_public": False}),
    ("15-licence-wrong", rejected("licence 'GPL 3.0' not accepted (dataset and resources)"), {"license": ["GPL 3.0"]}),
    ("16-licence-missing", rejected("licence missing (dataset and resources)"), {"license": []}),
    ("17-licence-short-label", rejected("'CC BY 4.0': dataset licence recognised, resource licence not (no exact dcatapit label)"),
     {"license": ["CC BY 4.0"]}),
    ("18-language-wrong", rejected("language 'francais' not accepted"), {"language": ["francais"]}),
    ("19-frequency-missing", rejected("frequency missing"), {"accrualperiodicity": []}),
    ("20-frequency-wrong", rejected("frequency 'ogni tanto' not in the EU vocabulary"), {"accrualperiodicity": ["ogni tanto"]}),
    ("21-frequency-pat-label", rejected("frequency 'Non viene aggiornato (mai)' not in the EU vocabulary"),
     {"accrualperiodicity": ["Non viene aggiornato (mai)"]}),
    ("22-no-resources", rejected("no resource has a download URL"), {"resources": [], "download_url": None}),
    ("23-resource-without-url", rejected("no resource has a download URL"), _res("")),
    ("24-relative-link", imported("relative link joined to the source site (raw.githubusercontent.com here): imported, file not downloaded",
                                  formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     _res("/exportas/csv/place")),
    ("25-link-404", imported("404 link: imported (file not downloaded)", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     _res(URL_404)),
    ("26-private-google-sheet", imported("private sheet (401): imported (file not downloaded)", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     _res(PRIVATE_SHEET)),
    ("27-format-unknown-text-plain", imported("file served as text/plain: declared CSV kept", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     _res(TEXT_PLAIN_URL)),
    ("28-one-good-one-broken-resource", imported("second link 404: imported (files not downloaded)", formats=["CSV", "CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     {"resources": [
         {"index": 0, "title": "Buona", "url_download": CSV_URL, "format": "CSV"},
         {"index": 1, "title": "Rotta", "url_download": URL_404, "format": "CSV"}]}),
    # Nuovi casi (regole del 28/10)
    ("29-licence-cc-by-1", rejected("licence 'CC BY 1.0' (real Pergine label) not accepted"), {"license": [CC_BY_1]}),
    ("30-format-excel", rejected("format 'EXCEL' not in the EU file type vocabulary"), _res(CSV_URL, fmt="EXCEL")),
    ("31-format-lowercase", imported("format 'csv' in lower case: normalised to CSV", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by"),
     _res(CSV_URL, fmt="csv")),
    ("32-format-missing", rejected("format missing"),
     {"resources": [{"index": 0, "title": "File", "url_download": CSV_URL, "format": ""}], "format": []}),
]


def make_comuni(out):
    base = RAW + "/comuni"
    items, expected = [], {}
    for case, exp, changes in COMUNI_CASES:
        guid = uid("vt-comuni-" + case)
        item = copy.deepcopy(COMUNI_RECORD)
        item.update({
            "id": guid,
            "uri": f"{base}/catalogo.json#{case}",
            "published_at": DATE + "T09:00:00+02:00",
            "modified_at": DATE + "T09:00:00+02:00",
            "title": f"VT Comuni {case}",
            "abstract": f"Caso di prova {case}",
            "identifier": f"vt-comuni-{case}",
            "description": f"<p>{desc(case, exp)}</p>",
        })
        item.update(changes)
        items.append(item)
        expected[guid] = {"case": case, **exp}
    write_json(os.path.join(out, "catalogo.json"), {"items": items, "self": None, "prev": None, "next": None, "count": len(items)})
    write_json(os.path.join(out, "expected.json"), expected)
    return base + "/catalogo.json", len(items)


# --- PAT (opencity_pat) ---------------------------------------------------------------
# Only catalogo_od && is_public records are imported. Checked: language and EU
# frequency. NOT checked: licence (unknown -> license_id empty, resources keep
# the dcatapit default CC BY 4.0), format (declared, or OP_DATPRO).

PAT_RECORD = {
    "is_public": True,
    "description_od": "",
    "conforms_to": [],
    "language": ["Italiano"],
    "version_info": "",
    "issued": DATE,
    "accrualperiodicity": ["Mensile"],
    "modified": DATE,
    "start_date": None,
    "end_date": None,
    "publisher": PAT_PUBLISHER,
    "rights_holder": "Provincia autonoma di Trento",
    "creator": [],
    "has_spatial_coverage": "Provincia autonoma di Trento",
    "contact_point": PAT_CONTACT,
    "topics": [],
    "theme": ["Economia e finanze"],
    "related_document": [],
    "keyword": [],
    "related_dataset": [],
    "transparency_section": [],
    "catalogo_od": True,
    "show_fullwidth": True,
    "csv_resource": None,
    "download_file": [],
    "download_url": CSV_URL + "|Elenco luoghi",
    "format": ["CSV"],
    "resources": [],
    "license": [CC_BY],
    "temporal": [],
    "resource_name": "TypedDataset",
}
POK = dict(frequency="MONTHLY", language="ITA", license_id="cc-by")

PAT_CASES = [
    ("01-ok-csv", imported("baseline", formats=["CSV"], distribution_formats=["CSV"], **POK), {}),
    ("02-ok-cc0-deutsch-freq-code",
     imported("CC0, deutsch, frequency given as EU code (parse_frequency)", formats=["CSV"], frequency="MONTHLY", language="DEU", license_id="cc-zero"),
     {"license": [CC0], "language": ["Deutsch"], "accrualperiodicity": ["MONTHLY"]}),
    ("03-ok-english-update-cont",
     imported("english, 'In continuo aggiornamento'", formats=["CSV"], frequency="UPDATE_CONT", language="ENG", license_id="cc-by"),
     {"language": ["English"], "accrualperiodicity": ["In continuo aggiornamento"]}),
    ("04-ok-frequency-mai", imported("'mai' = NEVER", formats=["CSV"], frequency="NEVER", language="ITA", license_id="cc-by"),
     {"accrualperiodicity": ["mai"]}),
    ("05-ok-google-sheet-range-suffix", imported("sheet link with '\";\"range' suffix (real PAT shape): cleaned; format = declared CSV", formats=["CSV"], urls=[SHEET_EDIT], **POK),
     {"download_url": SHEET_WITH_RANGE + "|Elenco"}),
    ("06-ok-language-missing", imported("no language: OP_DATPRO (not declared), accepted", formats=["CSV"], frequency="MONTHLY", language="OP_DATPRO", license_id="cc-by"),
     {"language": []}),
    ("07-exempt-licence-wrong", rejected("licence 'GPL 3.0' not accepted (dataset and resources)"),
     {"license": ["GPL 3.0"]}),
    ("08-exempt-licence-missing", imported("licence missing: license_id default cc-by, resources default CC BY 4.0", formats=["CSV"], frequency="MONTHLY", language="ITA", license_id="cc-by", license_types=[CC_BY_URI]),
     {"license": []}),
    ("09-exempt-format-wrong", rejected("format 'EXCEL' not in the EU file type vocabulary"),
     {"format": ["EXCEL"]}),
    ("10-exempt-format-missing", rejected("format missing"),
     {"format": []}),
    ("11-exempt-link-404", imported("file NOT downloaded: a 404 link is imported", formats=["CSV"], **POK),
     {"download_url": URL_404 + "|File"}),
    ("12-exempt-web-page-declared-csv", imported("file NOT downloaded: web page declared CSV keeps format CSV", formats=["CSV"], **POK),
     {"download_url": HTML_URL + "|Pagina"}),
    ("13-language-wrong", rejected("language 'francais' not accepted"), {"language": ["francais"]}),
    ("14-frequency-missing", rejected("frequency missing (new EU frequency rule)"), {"accrualperiodicity": []}),
    ("15-frequency-wrong", rejected("frequency 'ogni tanto' not in the EU vocabulary"), {"accrualperiodicity": ["ogni tanto"]}),
    ("16-frequency-pat-label", rejected("frequency 'Non viene aggiornato (mai)' (used by a real PAT dataset) not in the EU vocabulary"),
     {"accrualperiodicity": ["Non viene aggiornato (mai)"]}),
    ("17-skip-catalogo-od-false", skipped("catalogo_od false: not queued"), {"catalogo_od": False}),
    ("18-skip-not-public", skipped("is_public false: not queued"), {"is_public": False}),
    ("19-skip-catalogo-od-missing", skipped("no catalogo_od: not queued (PAT needs it true)"), {"catalogo_od": None}),
    ("20-no-resources", rejected("no resource has a download URL"), {"download_url": None, "resources": []}),
    ("21-resource-without-url", rejected("no resource has a download URL"),
     {"download_url": None, "resources": [{"index": 0, "title": "Senza link", "url_download": "", "format": "CSV"}]}),
    # Nuovi casi (regole del 28/10)
    ("22-licence-cc-by-1", rejected("licence 'CC BY 1.0' (real Pergine label) not accepted"), {"license": [CC_BY_1]}),
    ("23-licence-short-label", rejected("'CC BY 4.0': dataset licence recognised, resource licence not (no exact dcatapit label)"),
     {"license": ["CC BY 4.0"]}),
    ("24-format-lowercase", imported("format 'csv' in lower case: normalised to CSV", formats=["CSV"], distribution_formats=["CSV"], **POK),
     {"format": ["csv"]}),
    ("25-format-xyz", imported("format 'XYZ': not a common format, but an EU code -> accepted", formats=["XYZ"], **POK),
     {"format": ["XYZ"]}),
    ("26-frequency-spaces", imported("frequency ' Mensile ' with spaces: MONTHLY", formats=["CSV"], **POK),
     {"accrualperiodicity": [" Mensile "]}),
    ("27-frequency-sconosciuto", imported("frequency 'Sconosciuto' (used by a real PAT dataset): UNKNOWN", formats=["CSV"], frequency="UNKNOWN", language="ITA", license_id="cc-by"),
     {"accrualperiodicity": ["Sconosciuto"]}),
    ("28-language-inglese", imported("language 'Inglese' (as OpenCity writes it): ENG", formats=["CSV"], frequency="MONTHLY", language="ENG", license_id="cc-by"),
     {"language": ["Inglese"]}),
    ("29-language-tedesco", imported("language 'Tedesco': DEU", formats=["CSV"], frequency="MONTHLY", language="DEU", license_id="cc-by"),
     {"language": ["Tedesco"]}),
    ("30-two-languages", imported("languages ['Francese', 'Italiano']: the first recognised one (ITA) is kept", formats=["CSV"], **POK),
     {"language": ["Francese", "Italiano"]}),
    ("31-resources-only", imported("no download_url, files only in 'resources': no empty main resource", formats=["CSV"], n_resources=1, **POK),
     {"download_url": None, "resources": [{"index": 0, "title": "Elenco", "url_download": CSV_URL, "format": "CSV"}]}),
    ("32-resource-format-excel", rejected("second resource with format 'EXCEL' not in the EU vocabulary"),
     {"resources": [{"index": 0, "title": "Foglio", "url_download": CSV_URL_2, "format": "EXCEL"}]}),
]


def make_pat(out):
    base = RAW + "/pat"
    items, expected = [], {}
    for case, exp, changes in PAT_CASES:
        guid = uid("vt-pat-" + case)
        item = copy.deepcopy(PAT_RECORD)
        item.update({
            "id": guid,
            "uri": f"{base}/catalogo.json#{case}",
            "published_at": DATE + "T09:00:00+02:00",
            "modified_at": DATE + "T09:00:00+02:00",
            "title": f"VT PAT {case}",
            "abstract": f"Caso di prova {case}",
            "description": f"<p>{desc(case, exp)}</p>",
            "identifier": f"vt-pat-{case}",
        })
        item.update(changes)
        if item.get("catalogo_od") is None:
            item.pop("catalogo_od", None)
        items.append(item)
        expected[guid] = {"case": case, **exp}
    write_json(os.path.join(out, "catalogo.json"), {"items": items, "self": None, "prev": None, "next": None, "count": len(items)})
    write_json(os.path.join(out, "expected.json"), expected)
    return base + "/catalogo.json", len(items)


# --- ISPAT (tn_statweb_pro) -------------------------------------------------------------
# index.json (the source URL) lists {"id", "Descrizione", "URL"}; fetch_stage
# downloads "URL" (the indicator metadata), import_stage downloads the JSON in
# "Indicatore" (its first key becomes the resource name) and takes the CSV twin
# from "IndicatoreCSV". All URLs are read from the data, none is derived from
# the source URL, so static files work. Resource links must NOT be on GitHub
# (served as text/plain -> format unknown).

ISPAT_METADATA = {
    "Area": "",
    "Settore": "Famiglia e comportamenti sociali",
    "Algoritmo": "Indicatore di prova per i validatori dell'harvester",
    "AnnoInizio": "2011",
    "AnnoFine": "2025",
    "FreqAggiornamento": "Annuale",
    "UltimoAggiornamento": "26/10/2026",
    "Fenomeno": "Stock",
    "LivelloGeografico": "Provincia",
    "ConfrontiTerritoriali": "Trentino. Italia",
    "Note": "",
    "Licenza": "CC BY 4.0",
    "Tema": "GOVE",
    "NsoggDiffonPro": "ISPAT",
    "NsoggDiffonNaz": "",
    "NsoggDiffonInt": "",
    "Indicatore": JSON_URL,
    "IndicatoreCSV": CSV_URL,
}
IOK = dict(frequency="ANNUAL", language="ITA", license_id="cc-by")

ISPAT_CASES = [
    ("01-ok-json-csv", imported("baseline: JSON + CSV twin", formats=["JSON", "CSV"], **IOK), {}),
    ("02-ok-cc0-mensile", imported("CC0 1.0, 'Mensile'", formats=["JSON", "CSV"], frequency="MONTHLY", language="ITA", license_id="cc-zero"),
     {"Licenza": "CC0 1.0", "FreqAggiornamento": "Mensile"}),
    ("03-ok-frequency-code", imported("frequency given as EU code", formats=["JSON", "CSV"], **IOK), {"FreqAggiornamento": "ANNUAL"}),
    ("04-ok-json-only", imported("no CSV twin: one resource", formats=["JSON"], n_resources=1, **IOK), {"IndicatoreCSV": None}),
    ("05-ok-numeratore", imported("Indicatore + TabNumeratore, each with CSV twin: 4 resources", formats=["JSON", "CSV", "JSON", "CSV"], n_resources=4, **IOK),
     {"TabNumeratore": JSON_URL, "TabNumeratoreCSV": CSV_URL_2}),
    ("06-licence-wrong", rejected("licence 'IODL 2.0' not accepted"), {"Licenza": "IODL 2.0"}),
    ("07-licence-missing", rejected("licence missing"), {"Licenza": ""}),
    ("08-licence-long-label", rejected("full label 'Creative Commons Attribution 4.0 International (CC BY 4.0)': dataset licence recognised, resource licence not (ISPAT matches only the short label)"),
     {"Licenza": CC_BY}),
    ("09-frequency-missing", rejected("frequency missing"), {"FreqAggiornamento": ""}),
    ("10-frequency-wrong", rejected("frequency 'ogni tanto' not in the EU vocabulary"), {"FreqAggiornamento": "ogni tanto"}),
    ("11-indicator-json-404", rejected("JSON resource not downloadable: it and its CSV twin are skipped -> no resource has a download URL"),
     {"Indicatore": JSON_404}),
    ("12-csv-404", imported("CSV twin 404: imported (the CSV is not downloaded)", formats=["JSON", "CSV"], **IOK), {"IndicatoreCSV": URL_404}),
    ("13-csv-unknown-text-plain", imported("CSV twin served as text/plain: imported, fixed format CSV", formats=["JSON", "CSV"], **IOK), {"IndicatoreCSV": TEXT_PLAIN_URL}),
    ("14-real-statweb-links", imported("REAL statweb links of indicator 597: imported with fixed formats JSON + CSV, as the real ISPAT datasets", formats=["JSON", "CSV"], **IOK),
     {"Indicatore": STATWEB_JSON, "IndicatoreCSV": STATWEB_CSV}),
    ("15-metadata-404", rejected("indicator metadata URL not found", stage="fetch"), None),
]


def make_ispat(out):
    base = RAW + "/ispat"
    entries, expected = [], {}
    for n, (case, exp, changes) in enumerate(ISPAT_CASES, 1):
        ind_id = f"vt{n:02d}"
        title = f"VT ISPAT {case}"
        url = f"{base}/metadata/{ind_id}.json"
        if changes is None:
            url = f"{base}/metadata/missing-{ind_id}.json"
        else:
            md = {"Descrizione": title, **copy.deepcopy(ISPAT_METADATA)}
            md["Note"] = desc(case, exp)
            md.update(changes)
            md = {k: v for k, v in md.items() if v is not None}
            write_json(os.path.join(out, "metadata", ind_id + ".json"), {"IndicatoriStrutturali": [md]})
        entries.append({"id": ind_id, "Descrizione": title, "URL": url})
        expected["statistica:" + ind_id] = {"case": case, **exp}
    write_json(os.path.join(out, "index.json"), {"IndicatoriStrutturali": entries})
    write_json(os.path.join(out, "expected.json"), expected)
    return base + "/index.json", len(entries)


# --- PHASE 1: files ---------------------------------------------------------------------

def copy_runner(out, vt_type):
    with open(os.path.join(HERE, "runner.py"), encoding="utf-8") as f:
        text = f.read().replace('VT_TYPE = "__VT_TYPE__"', f'VT_TYPE = "{vt_type}"', 1)
    with open(os.path.join(out, f"runner_{vt_type}.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


MAKERS = {"appa": make_appa, "comuni": make_comuni, "pat": make_pat, "ispat": make_ispat}


def generate_files():
    with open(os.path.join(OUT, "plain-text-sample.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("nome;valore\nprova;1\n")
    for vt_type, make in MAKERS.items():
        out = os.path.join(OUT, vt_type)
        if os.path.isdir(out):
            shutil.rmtree(out)
        os.makedirs(out)
        url, n = make(out)
        copy_runner(out, vt_type)
        print(f"wrote {vt_type}/ ({n} cases), source URL {url}")


# --- PHASE 2 / 3: harvest sources (as in add_comunes.py, DRY_RUN by default) -----------------

def _ckan():
    import ckanapi
    if not API_TOKEN:
        raise SystemExit("Set the CKAN_API_TOKEN environment variable first.")
    return ckanapi.RemoteCKAN(CKAN_URL, apikey=API_TOKEN)


def source_url(vt_type):
    return f"{RAW}/{vt_type}/{SOURCES[vt_type][3]}"


def update_sources(types=tuple(SOURCES)):
    """Create the vt-<type> sources, or repoint them at the validator-test files."""
    ckan = None if DRY_RUN else _ckan()
    for vt_type in types:
        name, title, source_type, _, config = SOURCES[vt_type]
        fields = dict(name=name, title=title, url=source_url(vt_type), source_type=source_type,
                      owner_org=OWNER_ORG, frequency=FREQUENCY, config=config)
        if DRY_RUN:
            print("[DRY RUN] would create/update", fields)
            continue
        import ckanapi
        try:
            src = ckan.action.harvest_source_show(id=name)
            ckan.action.harvest_source_update(id=src["id"], **fields)
            print("updated:", name)
        except ckanapi.NotFound:
            ckan.action.harvest_source_create(**fields)
            print("created:", name)


def abort_pending(ckan, src, name):
    """Abort the last job if still New/Running (harvest_job_create refuses otherwise)."""
    last = (src.get("status") or {}).get("last_job") or {}
    if last.get("status") not in ("New", "Running"):
        return False
    ckan.action.harvest_job_abort(id=last["id"])
    print("  aborted pending job:", last["id"])
    return True


def _each_source(action, types, delay):
    ckan = None if DRY_RUN else _ckan()
    for vt_type in types:
        name = SOURCES[vt_type][0]
        if DRY_RUN:
            print(f"[DRY RUN] would {action}", name)
            continue
        src = ckan.action.harvest_source_show(id=name)
        # Never touch a source that is not a validator-test one.
        if not src["url"].startswith(RAW + "/"):
            print("REFUSED:", name, "does not point at validator-test:", src["url"])
            continue
        abort_pending(ckan, src, name)
        if action == "clear":
            ckan.action.harvest_source_clear(id=src["id"])
        else:
            ckan.action.harvest_job_create(source_id=src["id"], run=True)
        print(f"{action}:", name)
        if delay:
            time.sleep(delay)


def clear_all(types=tuple(SOURCES), delay=0.5):
    """Wipe the vt-* sources' datasets and history (same as "Clear" in the UI)."""
    _each_source("clear", types, delay)


def reharvest_all(types=tuple(SOURCES), delay=0.5):
    """Queue a harvest job for every vt-* source."""
    _each_source("reharvest", types, delay)


if __name__ == "__main__":
    # ── PHASE 1: generate the files ──
    generate_files()
    print("\n>>> Push validator-test/ to GitHub, open one catalogue URL, THEN PHASE 2.\n")
    # ── PHASE 2 (after pushing; DRY_RUN = False and CKAN_API_TOKEN set) ──
    #update_sources()
    # ── PHASE 2b ──
    #clear_all()
    # ── PHASE 3 ──
    #reharvest_all()
