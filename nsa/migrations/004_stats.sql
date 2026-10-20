-- Liczby orzeczeń do strony /statystyki i list w formularzu, przeliczane po
-- przetworz (NsaDeriver.after): wymiar total / court / kind / year / symbol.
CREATE TABLE IF NOT EXISTS judgment_stats (
    dimension VARCHAR(20) NOT NULL,
    value VARCHAR(191) NOT NULL,
    judgments INT UNSIGNED NOT NULL,
    wyroki INT UNSIGNED NOT NULL,
    postanowienia INT UNSIGNED NOT NULL,
    nsa INT UNSIGNED NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (dimension, value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
