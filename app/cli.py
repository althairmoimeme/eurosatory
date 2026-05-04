"""Top-level CLI: ``python -m app.cli <command>``.

Commands
--------
init         create the SQLite schema
scrape       fetch the Eurosatory catalog (countries, categories, exhibitors)
enrich       enrich exhibitors with the Finderr v3 detail endpoint
enrich-web   enrich exhibitors from their official website
classify     run taxonomy classification + commercial scoring
report       print the quality report (JSON)
export       write a CSV / XLSX export
all          run the full pipeline (init -> scrape -> enrich -> enrich-web -> classify)
api          start the FastAPI server (uvicorn)
ui           start the Streamlit interface
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from app.config import settings
from app.logging_setup import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def init() -> None:
    """Create database tables."""
    configure_logging()
    from app.database import init_db
    init_db()
    rprint("[green]ok[/green] database initialised at", settings.database_url)


@app.command()
def scrape() -> None:
    """Fetch the Eurosatory exhibitor catalog."""
    configure_logging()
    from app.pipelines.scrape import run_scrape
    summary = asyncio.run(run_scrape())
    rprint(summary)


@app.command()
def enrich(limit: Optional[int] = typer.Option(None, help="Cap the number of exhibitors")) -> None:
    """Enrich exhibitors with the Finderr v3 detail endpoint."""
    configure_logging()
    from app.pipelines.enrich_finderr import run_enrich_finderr
    summary = asyncio.run(run_enrich_finderr(limit=limit))
    rprint(summary)


@app.command("enrich-web")
def enrich_web(
    limit: Optional[int] = typer.Option(50, help="Cap the number of websites to fetch"),
    only_missing_email: bool = typer.Option(True, help="Skip exhibitors that already have an email"),
) -> None:
    """Enrich exhibitors from their official website."""
    configure_logging()
    from app.pipelines.enrich_websites import run_enrich_websites
    summary = asyncio.run(run_enrich_websites(limit=limit, only_missing_email=only_missing_email))
    rprint(summary)


@app.command()
def classify(limit: Optional[int] = typer.Option(None)) -> None:
    """Run taxonomy classification and commercial scoring."""
    configure_logging()
    from app.pipelines.classify import run_classify
    summary = run_classify(limit=limit)
    rprint(summary)


@app.command()
def report(out: Optional[Path] = typer.Option(None, help="Write JSON to this file too")) -> None:
    """Print a quality report."""
    configure_logging()
    from app.pipelines.report import build_report
    r = build_report()
    rprint(r)
    if out:
        out.write_text(json.dumps(r, indent=2, ensure_ascii=False))


@app.command("crm-export")
def crm_export(
    fmt: str = typer.Option(
        "xlsx",
        help="full-csv | full-xlsx | dynamics | salesforce | hubspot | airtable | prospecting"
    ),
    out: Optional[Path] = typer.Option(None, help="Optional path; default writes to data/exports/"),
) -> None:
    """Export the CRM-ready dataframe in the requested format."""
    configure_logging()
    from app.exports.crm_exports import (
        export_airtable_csv,
        export_dynamics_csv,
        export_full_csv,
        export_full_xlsx,
        export_hubspot_csv,
        export_prospecting_csv,
        export_salesforce_csv,
    )
    fn_map = {
        "full-csv": export_full_csv,
        "full-xlsx": export_full_xlsx,
        "dynamics": export_dynamics_csv,
        "salesforce": export_salesforce_csv,
        "hubspot": export_hubspot_csv,
        "airtable": export_airtable_csv,
        "prospecting": export_prospecting_csv,
    }
    fn = fn_map.get(fmt)
    if fn is None:
        rprint(f"[red]Unknown format[/red] '{fmt}'. Pick: {', '.join(fn_map)}")
        raise typer.Exit(code=1)
    p = fn(path=out)
    rprint(f"[green]exported[/green] {p}")


@app.command("attendance-template")
def attendance_template(
    out: Path = typer.Argument(..., help="Path where the CSV template is written"),
) -> None:
    """Write a CSV template for bulk-importing attendance signals."""
    configure_logging()
    from app.attendance.seed import write_template
    p = write_template(out)
    rprint(f"[green]template written[/green] {p}")


@app.command("attendance-import")
def attendance_import(
    csv_path: Path = typer.Argument(..., help="CSV produced from manual OSINT collection"),
) -> None:
    """Bulk-import a CSV of raw signals (one row per public source).

    The CSV must have the columns produced by ``attendance-template``.  Missing
    optional columns are tolerated.  Each row is scored, classified and
    deduplicated before being written.
    """
    configure_logging()
    from app.attendance.seed import import_csv
    summary = import_csv(csv_path)
    rprint(summary)


@app.command("attendance-queries")
def attendance_queries(
    years: str = typer.Option("2024,2025,2026", help="Comma-separated years"),
    no_social: bool = typer.Option(False, help="Skip LinkedIn / X queries"),
    out: Optional[Path] = typer.Option(None, help="Write queries to a file as well"),
) -> None:
    """Print the documented OSINT search queries for the operator to run manually."""
    configure_logging()
    from app.attendance.queries import queries_for_years
    yrs = [int(y.strip()) for y in years.split(",") if y.strip()]
    qs = queries_for_years(yrs, include_social=not no_social)
    for q in qs:
        rprint(q)
    if out:
        out.write_text("\n".join(qs))
        rprint(f"\n[green]written to[/green] {out}")


@app.command("attendance-auto-collect")
def attendance_auto_collect(
    year: int = typer.Option(2026, help="Edition cible"),
    limit: Optional[int] = typer.Option(
        None, help="Nombre max d'exposants à scanner (vide = tous)"
    ),
) -> None:
    """Scanne les sites web des exposants pour des mentions ``Eurosatory
    <année>`` et ingère les hits comme signaux ``company_announcement``.

    Le résultat alimente directement la liste consolidée affichée dans
    l'onglet Attendance Signals — l'opération tourne en back, l'UI ne
    sert qu'à consulter la liste finale.
    """
    configure_logging()
    from app.attendance.auto_collect import collect_corporate_signals

    def _cb(scanned: int, total: int, hits: int) -> None:
        if scanned % 25 == 0 or scanned == total:
            rprint(f"  scanned {scanned}/{total} · hits={hits}")

    res = collect_corporate_signals(
        target_year=year, limit=limit, only_with_website=True,
        progress_cb=_cb,
    )
    rprint(res)


@app.command("gicat-online-enrich")
def gicat_online_enrich_cli(
    dry_run: bool = typer.Option(
        True, help="Pass --no-dry-run to actually commit."
    ),
    limit: Optional[int] = typer.Option(
        None, help="Max number of GICAT companies to fetch."
    ),
) -> None:
    """Scrape the GICAT online directory detail pages
    (``hubj2c.com/getView``) for LinkedIn URLs + extra contacts that
    aren't in the PDF, then write back to the catalog.
    """
    configure_logging()
    from app.scrapers.gicat_online_enrich import (
        crawl_gicat_details, write_enrichment,
    )

    def _cb(scanned: int, total: int, ok: int) -> None:
        if scanned % 25 == 0 or scanned == total:
            rprint(f"  scanned {scanned}/{total}  parsed_ok={ok}")

    details = crawl_gicat_details(limit=limit, progress_cb=_cb)
    rprint(f"crawled {len(details)} detail pages")
    res = write_enrichment(details, dry_run=dry_run)
    rprint(res)


@app.command("gicat-pdf-import")
def gicat_pdf_import_cli(
    pdf: Path = typer.Argument(
        Path("/Users/bertantoine/Desktop/GICAT_2025_BD.pdf"),
        help="Path to the GICAT 2025 directory PDF.",
    ),
    dry_run: bool = typer.Option(
        True,
        help="If True (default), report counts without writing. Pass "
        "--no-dry-run to actually commit.",
    ),
    create_new: bool = typer.Option(
        True,
        help="Create Exhibitor rows for GICAT companies absent from the "
        "Eurosatory catalog.",
    ),
    skip_signals: bool = typer.Option(
        False,
        help="Skip the AttendanceSignals creation (companies-only import).",
    ),
) -> None:
    """Import the GICAT 2025 PDF directory : enrich existing exhibitors,
    create new ones, generate attendance signals for the 1000+ named
    persons (correspondents + executives).
    """
    configure_logging()
    from app.scrapers.gicat_pdf_import import import_gicat_pdf
    res = import_gicat_pdf(
        pdf, dry_run=dry_run, create_new_companies=create_new,
        create_signals=not skip_signals,
    )
    rprint(res.to_dict())


@app.command("attendance-scout-visitors")
def attendance_scout_visitors(
    year: int = typer.Option(2026, help="Edition cible"),
) -> None:
    """Scoute les visiteurs potentiels (PAS les exposants) en scannant les
    sites de presse défense + sites gouvernementaux/militaires pour des
    mentions d'Eurosatory + patterns de visite (delegation, attending,
    will visit, …). Distinct du ``attendance-auto-collect`` qui, lui,
    scrute les sites des exposants déjà connus.
    """
    configure_logging()
    from app.attendance.visitor_scout import scout_visitors

    def _cb(done: int, total: int, hits: int) -> None:
        rprint(f"  seed {done}/{total} · hits={hits}")

    res = scout_visitors(target_year=year, progress_cb=_cb)
    rprint(res)


@app.command("attendance-rocketreach-credits")
def attendance_rocketreach_credits_cli() -> None:
    """Show the RocketReach credit balance (no API cost)."""
    configure_logging()
    import asyncio
    from app.attendance.rocketreach_client import _DirectClient, get_account
    from app.config import settings

    async def go():
        async with _DirectClient(settings.rocketreach_api_key) as c:
            return await get_account(c, settings.rocketreach_api_key)

    acct = asyncio.run(go())
    if not acct:
        rprint("[red]Could not fetch account info[/red]")
        return
    rprint(f"Email   : {acct.get('email')}")
    rprint(f"State   : {acct.get('state')}")
    rprint("Credits :")
    for cu in acct.get("credit_usage") or []:
        rprint(
            f"  {cu.get('credit_type'):20s} "
            f"allocated={cu.get('allocated')} "
            f"used={cu.get('used')} "
            f"remaining={cu.get('remaining')}"
        )


@app.command("attendance-rocketreach")
def attendance_rocketreach_cli(
    limit: int = typer.Option(
        4, help="Max persons to enrich. 1 standard credit per lookup."
    ),
) -> None:
    """Use RocketReach to find LinkedIn URLs (and confirm titles) for
    high-value persons in our DB. Standard tier does NOT include emails
    — those need ``premium_lookup`` credits.
    """
    configure_logging()
    from app.attendance.rocketreach_client import enrich_via_rocketreach

    def _progress(msg: str) -> None:
        rprint(msg)

    res = enrich_via_rocketreach(limit=limit, progress_cb=_progress)
    rprint(res.to_dict())


@app.command("attendance-fullenrich-credits")
def attendance_fullenrich_credits_cli() -> None:
    """Show the FullEnrich credit balance (no API cost)."""
    configure_logging()
    import asyncio
    from app.attendance.fullenrich_client import _check_credits
    from app.scrapers.http_client import HttpClient
    from app.config import settings

    async def go():
        async with HttpClient(timeout=15, max_retries=1) as c:
            return await _check_credits(c, settings.fullenrich_api_key)

    n = asyncio.run(go())
    rprint(f"FullEnrich credits available: {n}")


@app.command("attendance-fullenrich")
def attendance_fullenrich_cli(
    limit: int = typer.Option(
        50, help="Max signals to enrich in this batch."
    ),
    dry_run: bool = typer.Option(
        False,
        help="Print the candidates that would be sent without calling "
        "the API.",
    ),
) -> None:
    """Use FullEnrich.com to find work emails + mobile phones for the
    AttendanceSignals that don't already have an email in their notes.

    Reads the API key from ``settings.fullenrich_api_key`` (env :
    ``FULLENRICH_API_KEY``). Costs : 1 credit / work email,
    10 credits / mobile phone.
    """
    configure_logging()
    from app.attendance.fullenrich_client import (
        _select_candidates, enrich_signals_via_fullenrich,
    )
    if dry_run:
        cands = _select_candidates(limit=limit)
        rprint(f"Would send {len(cands)} contacts:")
        for c in cands[:10]:
            rprint(f"  {c.get('first_name')} {c.get('last_name')} @ "
                   f"{c.get('domain') or c.get('company_name')} "
                   f"(linkedin={bool(c.get('linkedin_url'))})")
        if len(cands) > 10:
            rprint(f"  …and {len(cands) - 10} more")
        return

    def _progress(msg: str) -> None:
        rprint(f"  {msg}")

    res = enrich_signals_via_fullenrich(limit=limit, progress_cb=_progress)
    rprint(res.to_dict())


@app.command("attendance-enrich")
def attendance_enrich(
    refetch: bool = typer.Option(
        False,
        help="Re-fetch source URLs to extract person/role from fresh HTML "
        "(slower).",
    ),
    refetch_limit: int = typer.Option(
        200, help="Max signals to refetch in the slow phase."
    ),
    ner: bool = typer.Option(
        False,
        help="Also run spaCy NER on the article body — broader recall but "
        "noisier on defense press (vehicle / product names get tagged as "
        "PERSON). Use cautiously.",
    ),
) -> None:
    """Complete the missing fields on existing ``AttendanceSignal`` rows.

    Phase A : country fill from the Exhibitor catalog (instant, no
    network). Phase B : person + role extraction from stored
    snippet/title/URL slugs (instant). Phase C (optional) : re-fetch
    source URLs to parse fresh HTML — by default with the regex
    extractor only ; pass ``--ner`` to also run spaCy NER.
    """
    configure_logging()
    from app.attendance.enrich import enrich_signals, set_ner_enabled
    set_ner_enabled(ner)
    res = enrich_signals(include_refetch=refetch, refetch_limit=refetch_limit)
    rprint(res.to_dict())


@app.command("attendance-paste")
def attendance_paste(
    urls_file: Path = typer.Argument(
        ..., help="Fichier texte avec une URL par ligne"
    ),
    year: int = typer.Option(2026, help="Edition cible"),
) -> None:
    """Importe une liste d'URLs (1 par ligne) — fetch + extract + upsert
    en signaux. Plus rapide que le CSV manuel pour les enrichissements
    ad-hoc.
    """
    configure_logging()
    from app.attendance.auto_collect import bulk_paste_signals
    urls = [
        u.strip() for u in urls_file.read_text().splitlines() if u.strip()
    ]
    if not urls:
        rprint("[yellow]aucune URL dans le fichier[/yellow]")
        return
    res = bulk_paste_signals(urls, target_year=year)
    rprint(res)


@app.command("attendance-export")
def attendance_export(
    fmt: str = typer.Option("crm", help="crm | full"),
    out: Optional[Path] = typer.Option(None),
) -> None:
    """Export attendance signals (CRM-ready CSV by default)."""
    configure_logging()
    from app.exports.attendance_export import (
        export_attendance_crm_csv,
        export_attendance_full_csv,
    )
    fn = export_attendance_crm_csv if fmt == "crm" else export_attendance_full_csv
    p = fn(path=out)
    rprint(f"[green]exported[/green] {p}")


@app.command("custom-list-create")
def custom_list_create(
    name: str = typer.Argument(...),
    description: Optional[str] = typer.Option(None),
    owner: Optional[str] = typer.Option(None),
) -> None:
    """Create a custom commercial list."""
    configure_logging()
    from app.crm.repository import create_custom_list
    cl_id = create_custom_list(name=name, description=description, owner=owner)
    rprint(f"[green]created[/green] list_id={cl_id} name={name!r}")


@app.command("custom-list-add")
def custom_list_add(
    list_id: int = typer.Argument(...),
    exhibitor_ids: list[int] = typer.Argument(...),
) -> None:
    """Add exhibitors (DB ids) to a custom list."""
    configure_logging()
    from app.crm.repository import add_to_list
    n = add_to_list(list_id, exhibitor_ids)
    rprint(f"[green]added {n} new members to list {list_id}[/green]")


@app.command("custom-list-export")
def custom_list_export(
    list_id: int = typer.Argument(...),
    fmt: str = typer.Option("csv", help="csv | xlsx"),
) -> None:
    """Export a single custom list."""
    configure_logging()
    from app.exports.crm_exports import export_custom_list_csv
    p = export_custom_list_csv(list_id, fmt=fmt)
    rprint(f"[green]exported[/green] {p}")


@app.command()
def export(
    fmt: str = typer.Option("xlsx", help="csv | xlsx | intelligence-xlsx | intelligence-csv | airtable | crm"),
    country: Optional[str] = typer.Option(None, help="Comma-separated ISO2 codes"),
    priority: Optional[str] = typer.Option(None, help="Comma-separated priority levels (A+,A,B,C,D)"),
    status: Optional[str] = typer.Option(None, help="Comma-separated statuses"),
    min_score: Optional[float] = typer.Option(None),
    defense_category: Optional[str] = typer.Option(None, help="Comma-separated defense category filter"),
) -> None:
    """Export the exhibitor list."""
    configure_logging()
    from app.exports.exporters import (
        export_airtable_csv,
        export_crm_csv,
        export_csv,
        export_intelligence_csv,
        export_intelligence_xlsx,
        export_xlsx,
    )
    filters: dict = {}
    if country:
        filters["country_iso2"] = [c.strip().upper() for c in country.split(",")]
    prio_list = [p.strip().upper() for p in priority.split(",")] if priority else None
    if prio_list:
        filters["priority_level"] = prio_list
        filters["defense_priority_level"] = prio_list
    if status:
        filters["status"] = [s.strip() for s in status.split(",")]
    if min_score is not None:
        filters["min_score"] = min_score
        filters["min_defense_score"] = min_score
    if defense_category:
        filters["defense_categories"] = [c.strip() for c in defense_category.split(",")]

    fn_map = {
        "csv": export_csv,
        "xlsx": export_xlsx,
        "intelligence-xlsx": export_intelligence_xlsx,
        "intelligence-csv": export_intelligence_csv,
        "airtable": export_airtable_csv,
        "crm": export_crm_csv,
    }
    fn = fn_map.get(fmt)
    if fn is None:
        rprint(f"[red]Unknown format[/red] '{fmt}'.  Pick one of: {', '.join(fn_map)}")
        raise typer.Exit(code=1)
    p = fn(filters)
    rprint(f"[green]exported[/green] {p}")


@app.command()
def intelligence(
    limit: Optional[int] = typer.Option(None, help="Number of exhibitors to deep-crawl + analyze"),
    only_missing: bool = typer.Option(True, help="Skip exhibitors already analyzed"),
    max_pages: int = typer.Option(12, help="Max HTML pages to fetch per company"),
    max_pdfs: int = typer.Option(2, help="Max PDFs to fetch per company"),
    concurrency: int = typer.Option(3, help="Companies in parallel"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable Claude refinement (rule-based only)"),
) -> None:
    """Deep-crawl + extract built/sold/buying intelligence + score on the defense scale."""
    configure_logging()
    from app.pipelines.intelligence import run_intelligence
    summary = asyncio.run(
        run_intelligence(
            limit=limit,
            only_missing=only_missing,
            max_pages=max_pages,
            max_pdfs=max_pdfs,
            concurrency=concurrency,
            use_llm=not no_llm,
        )
    )
    rprint(summary)


@app.command("regenerate-cards")
def regenerate_cards() -> None:
    """Re-derive sales card / targeting fields on existing intelligence rows (no crawl)."""
    configure_logging()
    from app.pipelines.regenerate_cards import regenerate_all
    summary = regenerate_all()
    rprint(summary)


@app.command("sales-card")
def sales_card(
    exhibitor_id: int = typer.Argument(..., help="Exhibitor DB id"),
) -> None:
    """Print the verbatim sales card for one exhibitor."""
    configure_logging()
    from app.pipelines.intelligence import render_card
    card = render_card(exhibitor_id)
    if card is None:
        rprint("[red]No intelligence record for that exhibitor.[/red]")
        raise typer.Exit(code=1)
    print(card)


@app.command("all")
def run_all(
    enrich_web_limit: int = typer.Option(50, help="Limit website enrichment"),
) -> None:
    """Run init -> scrape -> enrich -> enrich-web -> classify -> report."""
    configure_logging()
    from app.database import init_db
    from app.pipelines.classify import run_classify
    from app.pipelines.enrich_finderr import run_enrich_finderr
    from app.pipelines.enrich_websites import run_enrich_websites
    from app.pipelines.report import build_report
    from app.pipelines.scrape import run_scrape

    init_db()
    rprint("[bold]1/5[/bold] scrape catalog")
    asyncio.run(run_scrape())
    rprint("[bold]2/5[/bold] enrich (finderr details)")
    asyncio.run(run_enrich_finderr())
    rprint("[bold]3/5[/bold] enrich (websites, limit={})".format(enrich_web_limit))
    asyncio.run(run_enrich_websites(limit=enrich_web_limit))
    rprint("[bold]4/5[/bold] classify + score")
    run_classify()
    rprint("[bold]5/5[/bold] quality report")
    rprint(build_report())


@app.command()
def api() -> None:
    """Start the FastAPI server."""
    configure_logging()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            settings.api_host,
            "--port",
            str(settings.api_port),
        ],
        check=False,
    )


@app.command()
def ui() -> None:
    """Start the Streamlit UI."""
    configure_logging()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app/ui/streamlit_app.py",
            "--server.port",
            str(settings.streamlit_port),
        ],
        check=False,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
