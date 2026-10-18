-- Dziennik pracy: przebiegi poleceń, zdarzenia (blokady, błędy) i rekordy
-- z problemem. Czytany przez "python -m <projekt> stan" albo wprost SQL-em.

-- Jeden wiersz na przebieg pobierz / przetworz / sprawdz.
CREATE TABLE IF NOT EXISTS fetch_runs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    command VARCHAR(20) NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    -- trwa / ok / limit / czas / blad (NULL finished_at + trwa = przerwany z zewnątrz)
    status VARCHAR(10) NOT NULL DEFAULT 'trwa',
    pages INT UNSIGNED NOT NULL DEFAULT 0,
    records INT UNSIGNED NOT NULL DEFAULT 0,
    -- tylko pobierz: nowe i zmienione rekordy (pozostałe bez zmian treści)
    records_new INT UNSIGNED NOT NULL DEFAULT 0,
    records_changed INT UNSIGNED NOT NULL DEFAULT 0,
    problems INT UNSIGNED NOT NULL DEFAULT 0,
    events INT UNSIGNED NOT NULL DEFAULT 0,
    cursor_start TEXT NULL,
    cursor_end TEXT NULL,
    error TEXT NULL,
    KEY fetch_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Blokady i błędy źródła: Cloudflare, HTTP 429/5xx, sieć, odrzucone zapytania.
CREATE TABLE IF NOT EXISTS fetch_events (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    run_id INT UNSIGNED NULL,
    at DATETIME NOT NULL,
    -- cloudflare / http_429 / http_5xx / siec / odrzucone / przerwano
    kind VARCHAR(20) NOT NULL,
    message TEXT NULL,
    url TEXT NULL,
    -- ile sekund czekaliśmy przed ponowieniem (NULL = bez ponowienia)
    wait_s INT UNSIGNED NULL,
    KEY fetch_events_at (at),
    KEY fetch_events_kind (kind, at),
    KEY fetch_events_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Konkretne rekordy z problemem; resolved_at ustawiane, gdy kolejny przebieg
-- przejdzie bez problemu. stage: pobierz / przetworz.
CREATE TABLE IF NOT EXISTS record_problems (
    source VARCHAR(30) NOT NULL,
    source_id VARCHAR(191) NOT NULL,
    stage VARCHAR(20) NOT NULL,
    -- brak_w_bazie / blad_parsera / pusty_tekst ...
    problem VARCHAR(30) NOT NULL,
    details TEXT NULL,
    first_seen DATETIME NOT NULL,
    last_seen DATETIME NOT NULL,
    times INT UNSIGNED NOT NULL DEFAULT 1,
    resolved_at DATETIME NULL,
    PRIMARY KEY (source, source_id, stage),
    KEY record_problems_open (resolved_at, stage, problem)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
