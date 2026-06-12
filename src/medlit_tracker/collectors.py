from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .http import archive_raw, request_bytes, request_json
from .scoring import matches_topic, normalize_text, score_record


NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MEDRXIV_API = "https://api.biorxiv.org/details/medrxiv"
CLINICAL_TRIALS_API = "https://clinicaltrials.gov/api/v2/studies"


def _node_text(node: ET.Element | None) -> str:
    return normalize_text("".join(node.itertext())) if node is not None else ""


def _first_text(node: ET.Element, path: str) -> str:
    return _node_text(node.find(path))


def _parse_pubmed_date(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    medline = _first_text(node, "MedlineDate")
    if medline:
        return medline
    year = _first_text(node, "Year")
    month = _first_text(node, "Month") or "01"
    day = _first_text(node, "Day") or "01"
    if not year:
        return None
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    month = months.get(month[:3].lower(), month.zfill(2) if month.isdigit() else "01")
    return f"{year}-{month}-{day.zfill(2)}"


def _with_score(record: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
    score, reasons = score_record(record, topic)
    record["score"] = score
    record["match_reasons"] = reasons
    return record


def _source_limit(topic: dict[str, Any], source: str) -> int:
    return int(
        topic.get("source_limits", {}).get(
            source, topic.get("max_results_per_source", 100)
        )
    )


def _window_start(topic: dict[str, Any]) -> date:
    configured = topic.get("_since_date")
    if configured:
        return date.fromisoformat(str(configured)[:10])
    return date.today() - timedelta(days=int(topic.get("lookback_days", 14)))


def collect_pubmed(
    topic: dict[str, Any], raw_root: Path, run_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    common = {
        "tool": "medlit_tracker",
        "email": os.getenv("NCBI_EMAIL", "local-research@example.invalid"),
        "api_key": os.getenv("NCBI_API_KEY"),
    }
    max_records = _source_limit(topic, "pubmed")
    search_params = {
        **common,
        "db": "pubmed",
        "term": topic["pubmed_query"],
        "datetype": "mdat",
        "retmode": "json",
        "sort": "pub_date",
    }
    if topic.get("_since_date"):
        search_params["mindate"] = _window_start(topic).strftime("%Y/%m/%d")
        search_params["maxdate"] = date.today().strftime("%Y/%m/%d")
    else:
        search_params["reldate"] = topic.get("lookback_days", 14)
    pmids: list[str] = []
    page_no = 0
    while len(pmids) < max_records:
        page_size = min(100, max_records - len(pmids))
        raw_search = request_bytes(
            f"{NCBI_EUTILS}/esearch.fcgi",
            params={**search_params, "retstart": len(pmids), "retmax": page_size},
        )
        archive_raw(raw_root, "pubmed", run_id, f"esearch_{page_no:03d}.json", raw_search)
        result = json.loads(raw_search.decode("utf-8")).get("esearchresult", {})
        page_ids = result.get("idlist", [])
        pmids.extend(page_ids)
        total = int(result.get("count") or 0)
        if not page_ids or len(pmids) >= total or len(page_ids) < page_size:
            break
        page_no += 1
    if not pmids:
        return [], errors

    raw_fetch = request_bytes(
        f"{NCBI_EUTILS}/efetch.fcgi",
        params={**common, "db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
    )
    raw_path = archive_raw(raw_root, "pubmed", run_id, "efetch.xml", raw_fetch)
    root = ET.fromstring(raw_fetch)
    records: list[dict[str, Any]] = []

    for item in root.findall(".//PubmedArticle"):
        citation = item.find("MedlineCitation")
        article = item.find("MedlineCitation/Article")
        if citation is None or article is None:
            continue
        pmid = _first_text(citation, "PMID")
        title = _first_text(article, "ArticleTitle")
        if not pmid or not title:
            continue
        abstract_parts = []
        for abstract_node in article.findall("Abstract/AbstractText"):
            label = abstract_node.attrib.get("Label", "")
            text = _node_text(abstract_node)
            abstract_parts.append(f"{label}: {text}" if label and text else text)
        abstract = normalize_text(" ".join(part for part in abstract_parts if part))

        authors = []
        for author in article.findall("AuthorList/Author"):
            collective = _first_text(author, "CollectiveName")
            personal = normalize_text(
                " ".join(filter(None, [_first_text(author, "ForeName"), _first_text(author, "LastName")]))
            )
            if collective or personal:
                authors.append(collective or personal)

        article_ids = {
            node.attrib.get("IdType", "").lower(): _node_text(node)
            for node in item.findall("PubmedData/ArticleIdList/ArticleId")
        }
        doi = article_ids.get("doi", "").lower()
        pmcid = article_ids.get("pmc", "").lower()
        publication_types = [
            _node_text(node) for node in article.findall("PublicationTypeList/PublicationType")
        ]
        mesh_terms = [
            _first_text(node, "DescriptorName")
            for node in citation.findall("MeshHeadingList/MeshHeading")
        ]
        correction_types = {
            node.attrib.get("RefType", "")
            for node in citation.findall("CommentsCorrectionsList/CommentsCorrections")
        }
        lower_types = {value.lower() for value in publication_types}
        lower_title = title.lower()
        status = "active"
        if "retracted publication" in lower_types or lower_title.startswith("retraction"):
            status = "retracted"
        elif "expressionofconcernin" in {value.lower() for value in correction_types}:
            status = "expression_of_concern"
        elif "published erratum" in lower_types or lower_title.startswith(("correction", "erratum")):
            status = "corrected"

        identifiers = [("pmid", pmid)]
        if doi:
            identifiers.append(("doi", doi))
        if pmcid:
            identifiers.append(("pmcid", pmcid))

        record = {
            "source": "pubmed",
            "source_id": pmid,
            "source_version": _first_text(citation, "DateRevised/Year") + _first_text(citation, "DateRevised/Month") + _first_text(citation, "DateRevised/Day"),
            "identifiers": identifiers,
            "record_type": "paper",
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "journal": _first_text(article, "Journal/Title"),
            "publication_date": _parse_pubmed_date(article.find("Journal/JournalIssue/PubDate")),
            "updated_date": _parse_pubmed_date(citation.find("DateRevised")),
            "study_type": "; ".join(publication_types),
            "publication_types": publication_types,
            "mesh_terms": mesh_terms,
            "peer_reviewed": True,
            "status": status,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "raw_path": str(raw_path),
        }
        records.append(_with_score(record, topic))
    return records, errors


def collect_europe_pmc(
    topic: dict[str, Any], raw_root: Path, run_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    end = date.today()
    start = _window_start(topic)
    query = f"({topic['europe_pmc_query']}) AND FIRST_PDATE:[{start} TO {end}]"
    payload = request_json(
        EUROPE_PMC,
        params={
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": _source_limit(topic, "europe_pmc"),
        },
    )
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    raw_path = archive_raw(raw_root, "europe_pmc", run_id, "search.json", raw)
    records = []
    for item in payload.get("resultList", {}).get("result", []):
        title = normalize_text(item.get("title"))
        if not title:
            continue
        pmid = str(item.get("pmid") or "").strip()
        doi = str(item.get("doi") or "").strip().lower()
        pmcid = str(item.get("pmcid") or "").strip().lower()
        source_id = pmid or pmcid or doi or str(item.get("id") or "")
        identifiers = []
        if pmid:
            identifiers.append(("pmid", pmid))
        if doi:
            identifiers.append(("doi", doi))
        if pmcid:
            identifiers.append(("pmcid", pmcid))
        if not identifiers:
            identifiers.append(("europe_pmc", source_id))
        authors = [
            author.get("fullName", "")
            for author in item.get("authorList", {}).get("author", [])
            if author.get("fullName")
        ]
        publication_types = item.get("pubTypeList", {}).get("pubType", []) or []
        record = {
            "source": "europe_pmc",
            "source_id": source_id,
            "source_version": str(item.get("versionNumber") or ""),
            "identifiers": identifiers,
            "record_type": "paper" if item.get("source") == "MED" else "preprint",
            "title": title,
            "abstract": normalize_text(item.get("abstractText")),
            "authors": authors,
            "journal": normalize_text(item.get("journalTitle")),
            "publication_date": item.get("firstPublicationDate"),
            "updated_date": item.get("dateOfRevision"),
            "study_type": "; ".join(publication_types),
            "publication_types": publication_types,
            "mesh_terms": [],
            "peer_reviewed": item.get("source") == "MED",
            "status": "active",
            "url": f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', source_id)}",
            "raw_path": str(raw_path),
        }
        if matches_topic(record, topic):
            records.append(_with_score(record, topic))
    return records, []


def _medrxiv_pages(start: date, end: date, max_records: int) -> Iterable[dict[str, Any]]:
    cursor = 0
    while cursor < max_records:
        payload = request_json(f"{MEDRXIV_API}/{start}/{end}/{cursor}/json")
        yield payload
        collection = payload.get("collection", [])
        if len(collection) < 30:
            break
        cursor += len(collection)


def collect_medrxiv(
    topic: dict[str, Any], raw_root: Path, run_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    end = date.today()
    start = _window_start(topic)
    records = []
    for page_no, payload in enumerate(
        _medrxiv_pages(start, end, _source_limit(topic, "medrxiv"))
    ):
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        raw_path = archive_raw(raw_root, "medrxiv", run_id, f"page_{page_no:03d}.json", raw)
        for item in payload.get("collection", []):
            preprint_doi = str(item.get("doi") or "").strip().lower()
            published_doi = str(item.get("published") or "").strip().lower()
            if published_doi in {"", "na", "n/a"}:
                published_doi = ""
            identifiers = [("doi", preprint_doi)]
            if published_doi:
                identifiers.append(("doi", published_doi))
            record = {
                "source": "medrxiv",
                "source_id": preprint_doi,
                "source_version": str(item.get("version") or "1"),
                "identifiers": identifiers,
                "record_type": "preprint",
                "title": normalize_text(item.get("title")),
                "abstract": normalize_text(item.get("abstract")),
                "authors": [normalize_text(item.get("authors"))] if item.get("authors") else [],
                "journal": "medRxiv",
                "publication_date": item.get("date"),
                "updated_date": item.get("date"),
                "study_type": normalize_text(item.get("category")),
                "publication_types": ["Preprint"],
                "mesh_terms": [],
                "peer_reviewed": False,
                "status": "published" if published_doi else "preprint",
                "url": f"https://www.medrxiv.org/content/{preprint_doi}v{item.get('version', '1')}",
                "raw_path": str(raw_path),
            }
            if preprint_doi and matches_topic(record, topic):
                records.append(_with_score(record, topic))
    return records, []


def _trial_date(module: dict[str, Any], key: str) -> str | None:
    value = module.get(key) or {}
    return value.get("date") if isinstance(value, dict) else None


def collect_clinical_trials(
    topic: dict[str, Any], raw_root: Path, run_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    params: dict[str, Any] = {
        "query.term": topic["clinical_trials_query"],
        "pageSize": 25,
        "format": "json",
        "sort": "LastUpdatePostDate:desc",
    }
    max_records = _source_limit(topic, "clinical_trials")
    cutoff = _window_start(topic)
    records = []
    page_no = 0
    while len(records) < max_records:
        payload = request_json(CLINICAL_TRIALS_API, params=params)
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        raw_path = archive_raw(raw_root, "clinical_trials", run_id, f"page_{page_no:03d}.json", raw)
        for study in payload.get("studies", []):
            protocol = study.get("protocolSection", {})
            identification = protocol.get("identificationModule", {})
            status_module = protocol.get("statusModule", {})
            design = protocol.get("designModule", {})
            description = protocol.get("descriptionModule", {})
            arms = protocol.get("armsInterventionsModule", {})
            outcomes = protocol.get("outcomesModule", {})
            nct_id = identification.get("nctId")
            if not nct_id:
                continue
            interventions = [
                item.get("name", "") for item in arms.get("interventions", []) if item.get("name")
            ]
            primary_outcomes = [
                item.get("measure", "") for item in outcomes.get("primaryOutcomes", []) if item.get("measure")
            ]
            abstract = normalize_text(
                " ".join(
                    filter(
                        None,
                        [
                            description.get("briefSummary", ""),
                            description.get("detailedDescription", ""),
                            "Interventions: " + ", ".join(interventions),
                            "Primary outcomes: " + ", ".join(primary_outcomes),
                        ],
                    )
                )
            )
            phases = design.get("phases", []) or []
            updated_date = _trial_date(status_module, "lastUpdatePostDateStruct")
            if updated_date:
                try:
                    if date.fromisoformat(updated_date[:10]) < cutoff:
                        continue
                except ValueError:
                    pass
            record = {
                "source": "clinical_trials",
                "source_id": nct_id,
                "source_version": _trial_date(status_module, "studyFirstPostDateStruct") or "",
                "identifiers": [("nct", nct_id)],
                "record_type": "trial",
                "title": identification.get("briefTitle") or identification.get("officialTitle") or nct_id,
                "abstract": abstract,
                "authors": [identification.get("organization", {}).get("fullName", "")],
                "journal": "ClinicalTrials.gov",
                "publication_date": _trial_date(status_module, "studyFirstPostDateStruct"),
                "updated_date": updated_date,
                "study_type": "; ".join(phases + [design.get("studyType", "")]),
                "publication_types": ["Clinical Trial Registry"],
                "mesh_terms": [],
                "peer_reviewed": False,
                "status": status_module.get("overallStatus", "UNKNOWN"),
                "url": f"https://clinicaltrials.gov/study/{nct_id}",
                "raw_path": str(raw_path),
            }
            if matches_topic(record, topic):
                records.append(_with_score(record, topic))
        token = payload.get("nextPageToken")
        if not token or len(records) >= max_records:
            break
        params["pageToken"] = token
        page_no += 1
    return records[:max_records], []


COLLECTORS = {
    "europe_pmc": collect_europe_pmc,
    "medrxiv": collect_medrxiv,
    "clinical_trials": collect_clinical_trials,
    "pubmed": collect_pubmed,
}
