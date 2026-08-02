# Powiadomienia o audytach rowerowych ZTP

Automat raz dziennie sprawdza [Audyty rowerowe ZTP](https://ztp.krakow.pl/rower/audyty) i wysyła wiadomość tylko wtedy, gdy:

- w sekcji „Otwarte” pojawi się nowy projekt;
- przy śledzonym projekcie pojawi się opinia audytu;
- przy śledzonym projekcie pojawi się nowy plan sytuacyjny po audycie.

Samo przeniesienie projektu z „Otwartych” do „Zamkniętych” nie wywołuje wiadomości. Bez nowego zdarzenia automat nie zmienia `state.json` i nie tworzy commita.

## Jednorazowa konfiguracja Gmaila

1. Na technicznym koncie Gmail włącz weryfikację dwuetapową.
2. Utwórz **hasło aplikacji** dla poczty. Skopiuj wygenerowane 16-znakowe hasło.
3. W repozytorium wejdź w **Settings → Secrets and variables → Actions → New repository secret**.
4. Dodaj trzy sekrety:

   - `SMTP_USER` – adres technicznej skrzynki Gmail;
   - `SMTP_APP_PASSWORD` – 16-znakowe hasło aplikacji;
   - `MAIL_TO` – adres, na który mają przychodzić powiadomienia.

Nie wpisuj zwykłego hasła do Gmaila. Hasła aplikacji nie należy dodawać do plików repozytorium.

## Uruchomienie testowe

Po dodaniu sekretów otwórz **Actions → Monitor audytów ZTP → Run workflow**. Obecne projekty są zapisane jako stan początkowy z 2 sierpnia 2026 r., więc pierwsze uruchomienie nie powinno wysłać wiadomości ani utworzyć commita, o ile ZTP nie opublikuje w międzyczasie czegoś nowego.

Harmonogram jest ustawiony na 06:17 UTC, czyli około 08:17 czasu polskiego latem i 07:17 zimą. GitHub zastrzega możliwość kilkuminutowego opóźnienia zadań cyklicznych.

## Ręczne poprawienie nazwy lokalizacji

Automat odmienia najczęstsze nazwy ulic do mianownika. Nietypową nazwę można wymusić w `location_overrides.json`, wpisując adres podstrony projektu i żądaną nazwę, np.:

```json
{
  "https://ztp.krakow.pl/rower/audyty/audyt/przykladowy-projekt": "most Grunwaldzki"
}
```

## Kontrola bez wysyłania maili

Lokalnie można wykonać:

```bash
pip install -r requirements.txt
python -m unittest -v
python monitor.py --check
```
