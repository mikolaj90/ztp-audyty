import unittest

from monitor import infer_location, parse_open_projects, parse_project


class MonitorTests(unittest.TestCase):
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
        self.assertEqual(project.deadline, "15 sierpnia 2026 roku")


if __name__ == "__main__":
    unittest.main()
