"""Validator test runner, to execute in the CKAN shell of the pod.

    echo 'exec(open("/tmp/runner_appa.py").read())' | ckan -c /app/production.ini shell

MODE = "dry" (default): NO writes. Reads the fake catalogue, builds every
package dict with the harvester mapping, runs the same checks as
import_stage (filters, check_package_dict, apply_real_formats, schema
validation without saving) and prints expected vs actual.

MODE = "run": real harvest in-process, gather -> fetch -> import, for the
source SOURCE_TITLE. It CREATES a harvest job and datasets, so it refuses to
start unless CONFIRM_RUN = True, the source has the right type and points at
the validator-test catalogue. Set the variables before exec:

    echo 'MODE="run"; CONFIRM_RUN=True; SOURCE_TITLE="VT validatori APPA"; exec(open("/tmp/runner_appa.py").read())' | ckan ... shell

Other variables: LOCAL_DIR (dry: read the validator-test files from a local
copy instead of GitHub, e.g. "/tmp/validator-test"), DRY_ORG (dry, Comuni:
organisation whose IPA code / contacts are used, default "tndigit"),
DRY_CONFIG (dry, Comuni: source config for the private filter, default {}).
Output lines start with "VTR ".
"""
import json
import os
import traceback
import uuid
from datetime import datetime

VT_TYPE = "pat"
RAW = "https://raw.githubusercontent.com/ahmadsaad-ds/comuni-test/refs/heads/main/validator-test"
BASE = RAW + "/" + VT_TYPE
CATALOGUE = BASE + "/" + ("index.json" if VT_TYPE == "ispat" else "catalogo.json")
HARVESTER_TYPE = {"appa": "opencity_appa", "comuni": "opencity_municipality",
                  "pat": "opencity_pat", "ispat": "tn_statweb_pro"}[VT_TYPE]

MODE = globals().get("MODE", "dry")
CONFIRM_RUN = globals().get("CONFIRM_RUN", False) is True
SOURCE_TITLE = globals().get("SOURCE_TITLE")
LOCAL_DIR = globals().get("LOCAL_DIR")
DRY_ORG = globals().get("DRY_ORG", "tndigit")
DRY_CONFIG = globals().get("DRY_CONFIG", {})

from ckan import model
import ckan.plugins.toolkit as tk


def out(*parts):
    for line in " ".join(str(p) for p in parts).splitlines() or [""]:
        print("VTR " + line)


def fetch(url, **kw):
    """The body of url; with LOCAL_DIR the validator-test files are read locally."""
    if LOCAL_DIR and url.startswith(RAW + "/"):
        path = os.path.join(LOCAL_DIR, url[len(RAW) + 1:].split("?")[0].split("#")[0])
        with open(path, encoding="utf-8") as f:   # missing file = 404
            return f.read()
    from ckanext.datitrentinoit.harvesters.http import fetch_url
    return fetch_url(url, auth=kw.get("auth"))


def harvester():
    from ckan.plugins import PluginImplementations
    from ckanext.harvest.interfaces import IHarvester
    for plugin in PluginImplementations(IHarvester):
        if plugin.info()["name"] == HARVESTER_TYPE:
            return plugin
    raise RuntimeError("harvester %s not loaded" % HARVESTER_TYPE)


# --- summary and comparison ----------------------------------------------------------------

def _value(pkg, key):
    if pkg.get(key) not in (None, ""):
        return pkg.get(key)
    for extra in pkg.get("extras") or []:
        if extra.get("key") == key:
            return extra.get("value")
    return None


def summary(pkg):
    resources = pkg.get("resources") or []
    return {
        "frequency": _value(pkg, "frequency"),
        "language": _value(pkg, "language"),
        "license_id": pkg.get("license_id") or None,
        "identifier": _value(pkg, "identifier"),
        "n_resources": len(resources),
        "formats": [r.get("format") for r in resources],
        "distribution_formats": [r.get("distribution_format") for r in resources],
        "license_types": sorted({r.get("license_type") for r in resources if r.get("license_type")}),
        "urls": [r.get("url") for r in resources],
    }


def compare(guid, exp, outcome, detail, ipa=None):
    problems = []
    if outcome != exp["outcome"]:
        problems.append("outcome %s, expected %s" % (outcome, exp["outcome"]))
    elif outcome == "imported":
        for key, want in (exp.get("checks") or {}).items():
            if isinstance(want, str):
                want = want.replace("{ipa}", str(ipa)).replace("{guid}", guid)
            got = detail.get(key) if isinstance(detail, dict) else None
            if got != want:
                problems.append("%s=%r, expected %r" % (key, got, want))
    verdict = "PASS" if not problems else "FAIL"
    out("%s %-40s expected %-8s got %-8s %s" % (verdict, exp["case"], exp["outcome"], outcome,
                                                "; ".join(problems)))
    out("      expected: " + exp["detail"])
    out("      actual:   " + (json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else str(detail)))
    return not problems


def report(expected, results, ipa=None):
    passed = sum(compare(g, expected[g], *results.get(g, ("missing", "no result")), ipa=ipa)
                 for g in expected)
    for guid in set(results) - set(expected):
        out("EXTRA result for unknown GUID", guid, results[guid])
    out("TOTAL %d/%d cases as expected" % (passed, len(expected)))


# --- dry run: same steps as the harvester, nothing saved -------------------------------------

class _Obj(object):
    def __init__(self, guid, content, source_url, config=""):
        self.guid, self.content, self.id = guid, content, "dry-" + guid
        self.source = type("S", (), {"url": source_url, "id": None, "config": config})()
        self.job = type("J", (), {"id": None})()
        self.extras = []

    def add(self):
        pass

    save = add


def _site_user():
    return tk.get_action("get_site_user")({"ignore_auth": True}, {})["name"]


def _validate(pkg, add_validators, base_schema=False):
    """Schema errors of create (as in create_dataset), without saving; None if valid."""
    import ckan.lib.plugins as lib_plugins
    from ckan import logic
    from ckanext.datitrentinoit.harvesters import opencity_harvest as oh, validation
    data = json.loads(json.dumps(pkg, default=str))
    data["id"] = str(uuid.uuid4())
    data["name"] = "vt-dry-" + uuid.uuid4().hex[:12]
    org = tk.get_action("organization_show")({"ignore_auth": True}, {"id": DRY_ORG, "include_datasets": False})
    data["owner_org"] = org["id"]
    context = {"model": model, "session": model.Session, "user": _site_user(), "ignore_auth": True,
               "api_version": "2"}
    plugin = lib_plugins.lookup_package_plugin()
    if base_schema:
        schema = logic.schema.default_create_package_schema()
        tags = logic.schema.default_tags_schema()
        tags["name"] = [tk.get_validator("not_empty")]
        schema["tags"] = tags
        schema["id"] = []
    else:
        name_validator = getattr(oh, "convert_to_string", None) or tk.get_validator("unicode_safe")
        if VT_TYPE == "comuni":
            name_validator = tk.get_validator("unicode_safe")
        schema = oh.add_harvest_rules(plugin.create_package_schema(), oh.tag_schema(name_validator),
                                      add_validators)
        schema["id"] = [name_validator]
    context["schema"] = schema
    try:
        _, errors = lib_plugins.plugin_validate(plugin, context, data, schema, "package_create")
    finally:
        model.Session.rollback()
    if errors:
        return validation.format_validation_error(tk.ValidationError(errors))
    return None


def _finish(pkg, pre_checks, add_validators, base_schema=False):
    from ckanext.datitrentinoit.harvesters import validation
    if not pkg.get("resources"):
        return "rejected", "No resource has a download URL."
    errors = pre_checks(pkg) if pre_checks else []
    if errors:
        return "rejected", validation.format_errors(errors)
    error = _validate(pkg, add_validators, base_schema)
    if error:
        return "rejected", "schema: " + error
    return "imported", summary(pkg)


def dry_opencity():
    from ckanext.datitrentinoit.harvesters import opencity_shared, opencity_harvest, validation
    if VT_TYPE == "appa":
        import ckanext.datitrentinoit.harvesters.opencity_appa.mapping as mapping
        import ckanext.datitrentinoit.harvesters.opencity_appa.opencity_appa as module
        keys = (("distribution", "resources"), ("contact_point", "contact_point"),
                ("publisher", "publisher"), ("creator", "creator"))
        accept = lambda obj: True
        pre = module._check_package
        rules = validation.add_harvest_validators
    elif VT_TYPE == "pat":
        import ckanext.datitrentinoit.harvesters.opencity_pat.mapping as mapping
        import ckanext.datitrentinoit.harvesters.opencity_pat.opencity_pat as module
        keys = (("contact_point", "contact_point"), ("publisher", "publisher"), ("creator", "creator"))
        accept = module._accept_record
        pre = None
        rules = module._add_pat_rules
    else:
        import ckanext.datitrentinoit.harvesters.opencity_municipality.mapping as mapping
        import ckanext.datitrentinoit.harvesters.opencity_municipality.opencity_municipality as module
        from ckanext.datitrentinoit.harvesters.org_info import OrgInfo
        keys = (("contact_point", "contact_point"), ("publisher", "publisher"), ("creator", "creator"))
        accept = lambda obj: module._accept_record(obj, DRY_CONFIG)
        pre = lambda pkg: validation.check_package_dict(pkg) + validation.apply_real_formats(pkg)
        rules = validation.add_harvest_validators
        org = tk.get_action("organization_show")({"ignore_auth": True}, {"id": DRY_ORG, "include_extras": True,
                                                                        "include_datasets": False})
        values = {e["key"]: e.get("value") for e in org.get("extras") or [] if e.get("key")}
        values.update({k: org.get(k) for k in ("identifier", "email", "telephone", "site") if org.get(k)})
        org_info = OrgInfo(title=org.get("title"), values=values)
        out("dry org", DRY_ORG, json.dumps(org_info.as_dict() if hasattr(org_info, "as_dict") else {}))

    results = {}
    catalogue = json.loads(fetch(CATALOGUE))
    original_fetch = opencity_shared.fetch_url
    original_org = getattr(mapping, "get_org_info", None)
    opencity_shared.fetch_url = fetch
    if VT_TYPE == "comuni":
        mapping.get_org_info = lambda harvest_object: org_info
    try:
        for item in catalogue["items"]:
            guid = item.get("id")
            if not accept(item):
                results[guid] = ("skipped", "filtered in gather_stage")
                continue
            try:
                content = {"package": item}
                for oc_key, key in keys:
                    content[key] = mapping.fetch_from_uri(item, oc_key)
                obj = _Obj(guid, json.dumps(content), CATALOGUE)
                pkg, extras = mapping.create_package_dict(obj, {})
                pkg = opencity_harvest.add_extras(pkg, extras)
                results[guid] = _finish(pkg, pre, rules)
            except Exception as e:
                results[guid] = ("error", "%s: %s" % (type(e).__name__, e))
                traceback.print_exc()
    finally:
        opencity_shared.fetch_url = original_fetch
        if original_org is not None:
            mapping.get_org_info = original_org
    return results, (org_info.ipa_code if VT_TYPE == "comuni" else None)


def dry_ispat():
    from ckanext.datitrentinoit.harvesters import validation
    from ckanext.datitrentinoit.harvesters.ispat import statwebbase
    from ckanext.datitrentinoit.harvesters.ispat.statweb_metadata import (
        StatWebProIndex, StatWebProEntry, StatWebMetadataPro)
    h = harvester()
    h.source_config = {}
    results = {}
    index = StatWebProIndex(fetch(CATALOGUE))
    pre = lambda pkg: validation.check_package_dict(pkg) + validation.apply_real_formats(pkg)
    for guid in sorted(index.keys()):
        entry = StatWebProEntry(txt=index.get_as_string(guid))
        try:
            metadata = StatWebMetadataPro(txt=fetch(entry.get_url()))
        except Exception as e:
            results[guid] = ("rejected", "fetch: metadata not downloadable (%s)" % e)
            continue
        entry.set_metadata(metadata.get_obj())
        try:
            pkg, metadata = h.create_package_dict(guid, entry.tostring())
            h.attach_resources(metadata, pkg, _Obj(guid, entry.tostring(), CATALOGUE))
            pkg["resources"] = statwebbase._resources_with_url(pkg)
            results[guid] = _finish(pkg, pre, None, base_schema=True)
        except Exception as e:
            results[guid] = ("error", "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()
    return results, None


# --- real run: gather -> fetch -> import in-process (writes!) ---------------------------------

def _refuse(message):
    out("REFUSED: " + message)
    return None


def real_run():
    from ckanext.harvest.model import (HarvestSource, HarvestJob, HarvestObject,
                                       HarvestObjectError, HarvestGatherError)
    if not CONFIRM_RUN:
        return _refuse("MODE='run' creates a harvest job and datasets: set CONFIRM_RUN=True to proceed.")
    if not SOURCE_TITLE:
        return _refuse("set SOURCE_TITLE to the title of the validator-test harvest source.")
    sources = model.Session.query(HarvestSource).filter_by(title=SOURCE_TITLE).all()
    if len(sources) != 1:
        return _refuse("%d sources titled %r" % (len(sources), SOURCE_TITLE))
    source = sources[0]
    if source.type != HARVESTER_TYPE:
        return _refuse("source type is %s, expected %s" % (source.type, HARVESTER_TYPE))
    if source.url.split("?")[0].rstrip("/") != CATALOGUE:
        return _refuse("source URL %s is not the validator-test catalogue %s" % (source.url, CATALOGUE))
    pending = model.Session.query(HarvestJob).filter(HarvestJob.source_id == source.id) \
        .filter(HarvestJob.status.in_(["New", "Running"])).count()
    if pending:
        return _refuse("the source has a New/Running job: abort it first.")

    h = harvester()
    job = HarvestJob()
    job.source = source
    job.status = "Running"
    job.gather_started = datetime.utcnow()
    job.save()
    out("job", job.id, "for source", source.id)
    ids = h.gather_stage(job) or []
    job = HarvestJob.get(job.id)
    job.gather_finished = datetime.utcnow()
    job.save()
    for error in model.Session.query(HarvestGatherError).filter_by(harvest_job_id=job.id):
        out("GATHER ERROR:", error.message)
    out("gathered", len(ids), "objects")

    try:
        from ckanext.harvest.queue import fetch_and_import_stages
    except ImportError:
        fetch_and_import_stages = None
    for object_id in ids:
        obj = HarvestObject.get(object_id)
        try:
            if fetch_and_import_stages:
                fetch_and_import_stages(h, obj)
            else:
                obj.state = "FETCH"
                obj.save()
                if h.fetch_stage(obj) is True:
                    obj = HarvestObject.get(object_id)
                    obj.state = "IMPORT"
                    obj.save()
                    ok = h.import_stage(obj)
                    obj = HarvestObject.get(object_id)
                    obj.state = "COMPLETE" if ok else "ERROR"
                else:
                    obj.state = "ERROR"
                obj.save()
        except Exception as e:
            model.Session.rollback()
            out("EXCEPTION on object", object_id, type(e).__name__, e)
            traceback.print_exc()

    job = HarvestJob.get(job.id)
    job.status = "Finished"
    job.finished = datetime.utcnow()
    job.save()

    results = {}
    expected_guids = set(json.loads(fetch(BASE + "/expected.json")))
    for guid in expected_guids:
        objs = model.Session.query(HarvestObject).filter_by(harvest_job_id=job.id, guid=guid).all()
        if not objs:
            results[guid] = ("skipped", "no harvest object in this job")
            continue
        obj = objs[-1]
        errors = model.Session.query(HarvestObjectError).filter_by(harvest_object_id=obj.id).all()
        if errors:
            results[guid] = ("rejected", " | ".join("[%s] %s" % (e.stage, e.message) for e in errors))
        elif obj.current and obj.package_id:
            pkg = tk.get_action("package_show")({"ignore_auth": True}, {"id": obj.package_id})
            results[guid] = ("imported", summary(pkg))
        else:
            results[guid] = ("error", "state %s, current %s, package %s" % (obj.state, obj.current, obj.package_id))
    ipa = None
    if VT_TYPE == "comuni":
        from ckanext.datitrentinoit.harvesters.org_info import get_org_info
        ipa = get_org_info(obj).ipa_code if ids else None
    out("job finished:", job.id, "- see the harvest report of the source in the UI")
    return results, ipa


def main():
    out("validator test", VT_TYPE, "mode", MODE, "catalogue", CATALOGUE, "local", LOCAL_DIR)
    if MODE == "run":
        done = real_run()
        if done is None:
            return
        results, ipa = done
    elif MODE == "dry":
        results, ipa = dry_ispat() if VT_TYPE == "ispat" else dry_opencity()
    else:
        out("unknown MODE", MODE)
        return
    report(json.loads(fetch(BASE + "/expected.json")), results, ipa)


main()
