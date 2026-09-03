#!/usr/bin/env python3
"""Monitor otwartych audytów rowerowych ZTP i późniejszych dokumentów."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


LIST_URL = "https://ztp.krakow.pl/rower/audyty"
ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
OVERRIDES_PATH = ROOT / "location_overrides.json"
DOC_EXTENSIONS = (".pdf", ".doc", ".docx")
MONTHS = {
    1: "stycznia", 2: "lutego", 3: "marca", 4: "kwietnia",
    5: "maja", 6: "czerwca", 7: "lipca", 8: "sierpnia",
    9: "września", 10: "października", 11: "listopada", 12: "grudnia",
}


@dataclass
class Document:
    url: str
    label: str
    context: str
    publication_date: str = ""


@dataclass
class Project:
    url: str
    title: str
    location: str
    publication_date: str = ""
    deadline: str = ""
    plans: list[Document] = field(default_factory=list)
    opinions: list[Document] = field(default_factory=list)
    post_audit_plans: list[Document] = field(default_factory=list)


class MonitorError(RuntimeError):
    pass


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def fetch(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=40)
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "").lower():
        raise MonitorError(f"ZTP zwrócił nieoczekiwany format dla {url}")
    return response.text


def fetch_optional(session: requests.Session, url: str) -> str | None:
    """Return None for a removed ZTP page without aborting the whole monitor."""
    try:
        return fetch(session, url)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            print(f"OSTRZEŻENIE: ZTP zwrócił 404 dla {url}; pomijam tę podstronę.")
            return None
        raise


def parse_open_projects(page_html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    heading = next(
        (h for h in soup.find_all(re.compile(r"^h[1-6]$")) if clean(h.get_text()).lower() == "otwarte"),
        None,
    )
    if heading is None:
        raise MonitorError("Nie znaleziono sekcji „Otwarte” – ZTP mogło zmienić układ strony.")

    results: list[tuple[str, str]] = []
    for element in heading.find_all_next():
        if element is not heading and element.name and re.fullmatch(r"h[1-6]", element.name):
            if clean(element.get_text()).lower() == "zamknięte":
                break
        if element.name != "a" or not element.get("href"):
            continue
        url = canonical_url(urljoin(LIST_URL, element["href"]))
        if "/rower/audyty/audyt/" not in url:
            continue
        item = (url, clean(element.get_text()))
        if item not in results:
            results.append(item)
    return results


def extract_date(text: str) -> str:
    patterns = (
        r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{4})(?!\d)",
        r"(?<!\d)(\d{1,2}\s+(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)\s+\d{4})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def extract_labeled_date(text: str, label: str, include_time: bool = False) -> str:
    """Extract a date immediately following a known metadata label."""
    months = (
        "stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|"
        "września|października|listopada|grudnia"
    )
    date_value = rf"(?:\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{4}}|\d{{1,2}}\s+(?:{months})\s+\d{{4}})(?:\s+r(?:oku|\.)?)?"
    time_value = r"(?:\s+\d{1,2}:\d{2})?" if include_time else ""
    match = re.search(rf"{label}\s*({date_value}{time_value})", text, re.IGNORECASE)
    return clean(match.group(1)) if match else ""


def extract_documents(content: Tag, page_url: str) -> list[Document]:
    documents: list[Document] = []
    buffer: list[str] = []
    for node in content.descendants:
        if isinstance(node, NavigableString):
            parent = node.parent
            if isinstance(parent, Tag) and parent.name == "a" and parent.get("href"):
                linked_path = urlsplit(urljoin(page_url, parent["href"])).path.lower()
                if linked_path.endswith(DOC_EXTENSIONS):
                    continue
            value = clean(str(node))
            if value:
                buffer.append(value)
                buffer = buffer[-30:]
            continue
        if not isinstance(node, Tag) or node.name != "a" or not node.get("href"):
            continue
        url = canonical_url(urljoin(page_url, node["href"]))
        path = urlsplit(url).path.lower()
        if not path.endswith(DOC_EXTENSIONS):
            continue
        label = clean(node.get_text(" ", strip=True)) or Path(path).name
        label = re.sub(r"^(?:pdf|docx?)-icon\s+", "", label, flags=re.IGNORECASE)
        context = clean(" ".join(buffer[-16:]))[-1000:]
        documents.append(Document(url, label, context, extract_date(context)))
        buffer = []
    return list({doc.url: doc for doc in documents}.values())


def is_opinion(doc: Document) -> bool:
    text = f"{doc.context} {doc.label}".lower()
    return any(marker in text for marker in (
        "opinia audytu", "opinia zespołu", "opinia zespolu",
        "pismo z uwagami audytu", "pismo w sprawie opinii audytu",
    ))


def is_post_audit_plan(doc: Document) -> bool:
    text = f"{doc.context} {doc.label}".lower()
    plan = any(marker in doc.label.lower() for marker in ("plan sytu", "sytuacja", "pzt"))
    after = any(marker in text for marker in (
        "po audycie", "po uwagach audytu", "po uwagach zespołu", "po uwagach zespolu",
        "dokumentacja uzupełniająca", "dokumentacja skorygowana", "skorygowana dokumentacja",
        "aktualizacja dokumentacji", "projekt budowlany po",
    ))
    return plan and after


def adjective_to_nominative(word: str) -> str:
    # Najczęstszy przypadek w tytułach ZTP: żeński przymiotnik w dopełniaczu/bierniku.
    endings = (("skiej", "ska"), ("ckiej", "cka"), ("dzkiej", "dzka"),
               ("owej", "owa"), ("nej", "na"), ("ej", "a"), ("ą", "a"))
    lower = word.lower()
    for old, new in endings:
        if lower.endswith(old) and len(word) > len(old) + 2:
            return word[:-len(old)] + new
    return word


def normalize_street_phrase(prefix: str, value: str) -> str:
    value = clean(re.split(r"\s+(?:wraz|polegając|na odcinku|w zakresie|w Krakowie|do węzła|od skrzyżowania)\b", value, 1, flags=re.I)[0])
    words = value.strip(" ,.;:–-").split()
    if words:
        words[-1] = adjective_to_nominative(words[-1])
    normalized_prefix = {"ulicy": "ul.", "alei": "al.", "placu": "pl."}.get(prefix.lower(), prefix.lower())
    return f"{normalized_prefix} {' '.join(words)}".strip()


def infer_location(title: str, url: str, overrides: dict[str, str]) -> str:
    if url in overrides:
        return overrides[url]
    matches = re.findall(
        r"\b(ul\.|al\.|os\.|ulicy|alei|placu)\s*([^,;()]+?)(?=\s+(?:wraz|oraz|i ul\.|i al\.|w ul\.|z ul\.|do ul\.|od ul\.|na odcinku|w zakresie|w Krakowie|polegając)|$)",
        title, re.IGNORECASE,
    )
    if matches:
        normalized = [normalize_street_phrase(prefix.lower(), value) for prefix, value in matches[:2]]
        return " / ".join(dict.fromkeys(normalized))
    for noun in ("most", "węzeł", "rondo", "park", "plac", "kładka", "tunel"):
        match = re.search(rf"\b({noun}\w*)\s+([^,;]+)", title, re.IGNORECASE)
        if match:
            return clean(f"{match.group(1)} {match.group(2)}")[:90]
    return clean(title)[:90]


def normalized_title(title: str) -> str:
    value = unicodedata.normalize("NFKD", title.lower())
    value = "".join(character for character in value if not unicodedata.combining(character))
    return clean(re.sub(r"[^a-z0-9]+", " ", value))


def reconcile_missing_project_urls(
    known: dict[str, dict],
    missing_urls: set[str],
    open_items: list[tuple[str, str]],
    overrides: dict[str, str],
) -> list[tuple[str, str]]:
    """Migrate a removed URL when ZTP exposes the same, nearly identical audit under a new one."""
    available = [(url, title) for url, title in open_items if url not in known]
    migrations: list[tuple[str, str]] = []
    used_old_urls: set[str] = set()

    for new_url, new_title in available:
        ranked = sorted(
            (
                SequenceMatcher(
                    None,
                    normalized_title(known[old_url].get("title", "")),
                    normalized_title(new_title),
                ).ratio(),
                old_url,
            )
            for old_url in missing_urls
            if old_url in known and old_url not in used_old_urls
        )
        if not ranked:
            continue
        best_score, old_url = ranked[-1]
        second_score = ranked[-2][0] if len(ranked) > 1 else 0.0
        if best_score < 0.95 or best_score - second_score < 0.03:
            continue

        saved = known.pop(old_url)
        saved["title"] = new_title
        saved["location"] = infer_location(new_title, new_url, overrides)
        known[new_url] = saved
        used_old_urls.add(old_url)
        migrations.append((old_url, new_url))
        print(f"ZMIANA ADRESU: {old_url} -> {new_url}")

    return migrations


def parse_project(page_html: str, url: str, list_title: str, location: str) -> Project:
    soup = BeautifulSoup(page_html, "html.parser")
    heading = next((h for h in soup.find_all(["h1", "h2"]) if clean(h.get_text()) == list_title), None)
    if heading is None:
        heading = soup.find("h1") or soup.find("h2")
    title = clean(heading.get_text()) if heading else list_title
    # On current ZTP pages the heading sits in a small ``news-desc`` box, while
    # the deadline and downloads are its siblings inside ``page-content``.
    # Using heading.parent therefore silently discarded all three fields.
    content = (
        heading.find_parent(class_="page-content") if heading else None
    ) or (heading.find_parent("main") if heading else None) or soup.find("main") or soup.body
    full_text = clean(content.get_text(" ", strip=True))
    published = extract_labeled_date(full_text, r"Opublikowano:\s*", include_time=True)
    deadline = extract_labeled_date(full_text, r"Możliwość\s+składania\s+uwag\s+do\s*")
    docs = extract_documents(content, url)
    opinions = [doc for doc in docs if is_opinion(doc)]
    post_plans = [doc for doc in docs if not is_opinion(doc) and is_post_audit_plan(doc)]
    initial_plans = [doc for doc in docs if "plan sytu" in doc.label.lower() and doc not in post_plans]
    return Project(
        url=url, title=title, location=location,
        publication_date=published,
        deadline=deadline.rstrip("."),
        plans=initial_plans, opinions=opinions, post_audit_plans=post_plans,
    )


def polish_today() -> str:
    today = date.today()
    return f"{today.day} {MONTHS[today.month]} {today.year} r."


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def project_block(project: Project) -> str:
    plan_links = "<br>".join(link(doc.url, doc.label or "Plan sytuacyjny") for doc in project.plans)
    return (
        f"<p>{link(project.url, project.title)}<br>"
        f"<b>Data publikacji:</b> {html.escape(project.publication_date or 'brak daty na stronie ZTP')}<br>"
        f"<b>Uwagi można zgłaszać do:</b> {html.escape(project.deadline or 'brak terminu na stronie ZTP')}<br>"
        f"<b>Plan sytuacyjny:</b><br>{plan_links or 'brak odsyłacza na stronie ZTP'}</p>"
    )


def parse_recipients(*values: str) -> list[str]:
    """Parse one or more comma/semicolon/newline-separated recipient lists."""
    recipients: list[str] = []
    for value in values:
        for recipient in re.split(r"[,;\n]+", value or ""):
            recipient = recipient.strip()
            if recipient and recipient not in recipients:
                recipients.append(recipient)
    return recipients


def send_mail(subject: str, body_html: str) -> None:
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_APP_PASSWORD", "").replace(" ", "")
    recipients = parse_recipients(
        os.environ.get("MAIL_TO", ""),
        os.environ.get("MAIL_TO_EXTRA", ""),
    )
    if not smtp_user or not smtp_password or not recipients:
        raise MonitorError("Brakuje sekretu SMTP_USER, SMTP_APP_PASSWORD lub odbiorcy MAIL_TO.")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=40) as smtp:
        smtp.login(smtp_user, smtp_password)
        for recipient in recipients:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = f"Audyty rowerowe ZTP <{smtp_user}>"
            message["To"] = recipient
            message.set_content(re.sub(r"<[^>]+>", " ", body_html))
            message.add_alternative(
                f"<html><body style='font-family:Arial,sans-serif;line-height:1.5'>{body_html}</body></html>",
                subtype="html",
            )
            smtp.send_message(message)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unseen(documents: Iterable[Document], seen_urls: Iterable[str]) -> list[Document]:
    seen = set(seen_urls)
    return [doc for doc in documents if doc.url not in seen]


def run(check_only: bool = False) -> bool:
    state = load_json(STATE_PATH)
    overrides = load_json(OVERRIDES_PATH)
    session = requests.Session()
    session.headers["User-Agent"] = "ztp-audyty-monitor/1.0 (+https://github.com/mikolaj90/ztp-audyty)"

    open_items = parse_open_projects(fetch(session, LIST_URL))
    if not open_items:
        raise MonitorError("Sekcja „Otwarte” nie zawiera projektów; przerywam, aby nie zapisać błędnego stanu.")

    known = state["projects"]
    tracked_projects: list[Project] = []
    missing_urls: set[str] = set()
    for url, saved in list(known.items()):
        page_html = fetch_optional(session, url)
        if page_html is None:
            missing_urls.add(url)
            continue
        tracked_projects.append(parse_project(page_html, url, saved["title"], saved["location"]))

    migrations = reconcile_missing_project_urls(known, missing_urls, open_items, overrides)
    for _old_url, new_url in migrations:
        saved = known[new_url]
        page_html = fetch_optional(session, new_url)
        if page_html is not None:
            tracked_projects.append(parse_project(page_html, new_url, saved["title"], saved["location"]))

    new_projects: list[Project] = []
    for url, title in open_items:
        if url in known:
            continue
        page_html = fetch_optional(session, url)
        if page_html is None:
            continue
        location = infer_location(title, url, overrides)
        new_projects.append(parse_project(page_html, url, title, location))

    opinion_events: list[tuple[Project, list[Document]]] = []
    post_events: list[tuple[Project, list[Document]]] = []
    for project in tracked_projects:
        saved = known[project.url]
        new_opinions = unseen(project.opinions, saved.get("opinion_documents", []))
        new_post = unseen(project.post_audit_plans, saved.get("post_audit_documents", []))
        if new_opinions:
            opinion_events.append((project, new_opinions))
        if new_post:
            post_events.append((project, new_post))

    print(f"Otwarte: {len(open_items)}, nowe: {len(new_projects)}, nowe opinie: {len(opinion_events)}, projekty po audycie: {len(post_events)}")
    if check_only:
        for old_url, new_url in migrations:
            print(f"NOWY ADRES: {old_url} -> {new_url}")
        for project in new_projects:
            print(f"NOWY: {project.location} — {project.title}")
        for project, docs in opinion_events:
            print(f"OPINIA: {project.location} — {', '.join(d.label for d in docs)}")
        for project, docs in post_events:
            print(f"PO AUDYCIE: {project.location} — {', '.join(d.label for d in docs)}")
        return False

    if new_projects:
        subject = "Audyty rowerowe: " + ", ".join(project.location for project in new_projects)
        blocks = "<hr>".join(project_block(project) for project in new_projects)
        send_mail(subject, f"<p><b>Nowe na Audytach rowerowych ZTP:</b></p>{blocks}")
    for project, docs in opinion_events:
        links = "<br>".join(link(doc.url, doc.label or "Opinia audytu") for doc in docs)
        dated = next((doc.publication_date for doc in docs if doc.publication_date), polish_today())
        send_mail(
            f"Opinia Audytu: {project.location}",
            f"<p>{link(project.url, project.title)}<br><b>Data publikacji opinii Audytu:</b> {html.escape(dated)}<br>"
            f"<b>Opinia Audytu:</b><br>{links}</p>",
        )
    for project, docs in post_events:
        links = "<br>".join(link(doc.url, doc.label or "Plan sytuacyjny") for doc in docs)
        dated = next((doc.publication_date for doc in docs if doc.publication_date), polish_today())
        send_mail(
            f"Projekt po audycie: {project.location}",
            f"<p>{link(project.url, project.title)}<br><b>Data publikacji plików po audycie:</b> {html.escape(dated)}<br>"
            f"<b>Plan sytuacyjny po audycie:</b><br>{links}</p>",
        )

    changed = bool(migrations or new_projects or opinion_events or post_events)
    if not changed:
        return False
    today = date.today().isoformat()
    for project in new_projects:
        known[project.url] = {
            "title": project.title, "location": project.location, "notified_at": today,
            "opinion_documents": [doc.url for doc in project.opinions],
            "post_audit_documents": [doc.url for doc in project.post_audit_plans],
        }
    for project, docs in opinion_events:
        known[project.url].setdefault("opinion_documents", []).extend(doc.url for doc in docs)
    for project, docs in post_events:
        known[project.url].setdefault("post_audit_documents", []).extend(doc.url for doc in docs)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Sprawdź stronę, ale nie wysyłaj maili i nie zmieniaj stanu")
    args = parser.parse_args()
    run(check_only=args.check)


if __name__ == "__main__":
    main()
