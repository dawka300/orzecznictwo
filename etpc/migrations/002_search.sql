-- Wyszukiwarka (python -m etpc www) i statystyki.

-- Filtr "skład" z sortowaniem po dacie.
ALTER TABLE rulings ADD KEY IF NOT EXISTS rulings_formation_date (formation, ruling_date);

-- Statystyki i filtr języka: (language, ruling_id) obejmuje zapytanie bez
-- czytania wierszy z tekstem; zastępuje indeks na samym language.
ALTER TABLE ruling_documents ADD KEY IF NOT EXISTS ruling_documents_language_ruling (language, ruling_id);
ALTER TABLE ruling_documents DROP KEY IF EXISTS ruling_documents_language;

-- Liczby orzeczeń w przekrojach: ogółem, języki, składy, państwa, lata,
-- ważność, naruszone artykuły. Przeliczane od nowa po każdym przetworz
-- (etpc/stats.py), czyta je strona statystyk i listy w filtrach.
CREATE TABLE IF NOT EXISTS ruling_stats (
    -- total / language / formation / respondent / year / importance / violation
    dimension VARCHAR(20) NOT NULL,
    -- kod z danych: ENG, POL, GRANDCHAMBER, 2009, 6-1 ...; dla total: all
    value VARCHAR(50) NOT NULL,
    rulings INT UNSIGNED NOT NULL,
    judgments INT UNSIGNED NOT NULL,
    decisions INT UNSIGNED NOT NULL,
    -- tylko total i language: liczba dokumentów (wersji językowych)
    documents INT UNSIGNED NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (dimension, value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
