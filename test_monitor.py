import unittest
from unittest.mock import Mock

import requests

from monitor import (
    fetch_optional,
    infer_location,
    parse_recipients,
    parse_open_projects,
    parse_project,
    reconcile_missing_project_urls,
)


class MonitorTests(unittest.TestCase):
    def test_recipient_lists_are_combined_and_deduplicated(self):
        self.assertEqual(
            parse_recipients(
                "miki@example.com",
                "kamil@example.com; miki@example.com\ntrzeci@example.com",
            ),
            ["miki@example.com", "kamil@example.com", "trzeci@example.com"],
        )

    def test_open_section_stops_at_closed(self):
        page = '''<h3>Otwarte</h3><ul><li><a href="/rower/audyty/audyt/nowy">Nowy</a></li></ul>
                  <h3>Zamknięte</h3><a href="/rower/audyty/audyt/stary">Stary</a>'''
        self.assertEqual(parse_open_projects(page), [("https://ztp.krakow.pl/rower/audyty/audyt/nowy", "Nowy")])

    def test_location_nominative(self):
        self.assertEqual(infer_location("Przebudowa ul. Przemysłowej w Krakowie", "x", {}), "ul. Przemysłowa")
        self.assertEqual(infer_location("Rozbudowa ulicy Kocmyrzowskiej w Krakowie", "x", {}), "ul. Kocmyrzowska")

    def test_project_documents(self):
        page = '''<main><h2>Przebudowa ul. Testowej</h2><p>Opublikowano: 1.08.2026 12:00</p>
        <p>Możliwość składania uwag do 15 sierpnia 2026 roku.</p>
        <a href="/files/plan.pdf">Plan sytuacyjny</a>
        <p>Opinia Audytu – 20.08.2026</p><a href="/files/opinia.pdf">Opinia Audytu</a>
        <p>Aktualizacja dokumentacji po uwagach Audytu – 25.08.2026</p>
        <a href="/files/plan-po.pdf">Plan sytuacyjny</a></main>'''
        project = parse_project(page, "https://ztp.krakow.pl/rower/audyty/audyt/test", "Przebudowa ul. Testowej", "ul. Testowa")
        self.assertEqual(len(project.plans), 1)
        self.assertEqual(len(project.opinions), 1)
        self.assertEqual(len(project.post_audit_plans), 1)
        self.assertEqual(project.publication_date, "1.08.2026 12:00")
        self.assertEqual(project.deadline, "15 sierpnia 2026 roku")

    def test_project_metadata_outside_heading_box(self):
        page = '''<main><div class="page-content">
        <div class="news-desc"><h2>Przebudowa ul. Testowej</h2>
        <div class="news-item-date">Opublikowano: 11.08.2026 12:53</div></div>
        <p>Możliwość składania uwag do 24 sierpnia 2026</p>
        <div><a href="/files/plan-cz-1.pdf"><span>pdf-icon</span> Plan sytuacyjny</a></div>
        <div><a href="/files/plan-cz-2.pdf"><span>pdf-icon</span> Plan sytuacyjny</a></div>
        </div></main>'''
        project = parse_project(page, "https://ztp.krakow.pl/rower/audyty/audyt/test", "Przebudowa ul. Testowej", "ul. Testowa")
        self.assertEqual(project.publication_date, "11.08.2026 12:53")
        self.assertEqual(project.deadline, "24 sierpnia 2026")
        self.assertEqual(len(project.plans), 2)
        self.assertEqual(project.plans[0].label, "Plan sytuacyjny")
        self.assertEqual(
            [plan.url for plan in project.plans],
            [
                "https://ztp.krakow.pl/files/plan-cz-1.pdf",
                "https://ztp.krakow.pl/files/plan-cz-2.pdf",
            ],
        )

    def test_reconciles_corrected_title_and_url_without_losing_state(self):
        old_url = "https://ztp.krakow.pl/rower/audyty/audyt/przebudowa-ul-skotnickiej-w-zakresie-realizacji-drogi-dla-pieszych-wraz-z-zatoka-postojowa"
        new_url = "https://ztp.krakow.pl/rower/audyty/audyt/przebudowa-ul-skotnica-w-zakresie-realizacji-drogi-dla-pieszych-wraz-z-zatoka-postojowa"
        known = {
            old_url: {
                "title": "Przebudowa ul. Skotnickiej w zakresie realizacji drogi dla pieszych wraz z zatoką postojową",
                "location": "ul. Skotnicka",
                "notified_at": "2026-08-11",
                "opinion_documents": ["https://example.com/opinia.pdf"],
                "post_audit_documents": [],
            }
        }
        title = "Przebudowa ul. Skotnica w zakresie realizacji drogi dla pieszych wraz z zatoką postojową"

        migrations = reconcile_missing_project_urls(known, {old_url}, [(new_url, title)], {})

        self.assertEqual(migrations, [(old_url, new_url)])
        self.assertNotIn(old_url, known)
        self.assertEqual(known[new_url]["title"], title)
        self.assertEqual(known[new_url]["location"], "ul. Skotnica")
        self.assertEqual(known[new_url]["notified_at"], "2026-08-11")
        self.assertEqual(known[new_url]["opinion_documents"], ["https://example.com/opinia.pdf"])

    def test_does_not_reconcile_unrelated_project(self):
        old_url = "https://ztp.krakow.pl/rower/audyty/audyt/stary"
        new_url = "https://ztp.krakow.pl/rower/audyty/audyt/nowy"
        known = {old_url: {"title": "Przebudowa ul. Starej", "location": "ul. Stara"}}

        migrations = reconcile_missing_project_urls(
            known,
            {old_url},
            [(new_url, "Budowa kładki nad Wisłą")],
            {},
        )

        self.assertEqual(migrations, [])
        self.assertIn(old_url, known)
        self.assertNotIn(new_url, known)

    def test_single_404_is_skipped(self):
        response = Mock(status_code=404)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        session = Mock()
        session.get.return_value = response

        self.assertIsNone(fetch_optional(session, "https://ztp.krakow.pl/missing"))


if __name__ == "__main__":
    unittest.main()
