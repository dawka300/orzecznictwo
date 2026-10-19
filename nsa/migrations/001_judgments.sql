-- Orzeczenia sądów administracyjnych (NSA i WSA) wyprowadzone z raw_records.
-- Klucz: identyfikator CBOSA (/doc/<ID>); sygnatura się powtarza (w jednej
-- sprawie bywa kilka orzeczeń). Wszystko da się odtworzyć parserem.

CREATE TABLE IF NOT EXISTS judgments (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    cbosa_id VARCHAR(20) NOT NULL,
    docket_number VARCHAR(50) NOT NULL,
    -- np. "Wyrok WSA w Opolu", "Postanowienie NSA"
    judgment_type VARCHAR(100) NOT NULL,
    -- wyrok / postanowienie / uchwala / inne
    kind VARCHAR(20) NOT NULL,
    court VARCHAR(100) NOT NULL,
    judgment_date DATE NOT NULL,
    submission_date DATE NULL,
    -- 1 prawomocne, 0 nieprawomocne, NULL brak informacji
    is_final TINYINT(1) NULL,
    challenged_authority VARCHAR(191) NULL,
    -- treść wyniku, kolejne pozycje w osobnych wierszach
    result TEXT NULL,
    thesis MEDIUMTEXT NULL,
    sentence MEDIUMTEXT NULL,
    reasons LONGTEXT NULL,
    dissenting_opinion MEDIUMTEXT NULL,
    -- długość i sha256 tekstu (teza + sentencja + uzasadnienie + zdanie odrębne):
    -- RAG liczy wektory od nowa tylko po zmianie
    text_length INT UNSIGNED NOT NULL,
    text_sha256 CHAR(64) NOT NULL,
    -- skąd rekord: juddges (zbiór z Hugging Face) / cbosa
    source VARCHAR(20) NOT NULL,
    parsed_at DATETIME NOT NULL,
    UNIQUE KEY judgments_cbosa_id (cbosa_id),
    KEY judgments_docket (docket_number),
    KEY judgments_date (judgment_date),
    KEY judgments_court_date (court, judgment_date),
    KEY judgments_kind_date (kind, judgment_date),
    KEY judgments_authority (challenged_authority),
    FULLTEXT KEY judgments_ft_text (thesis, sentence, reasons, dissenting_opinion)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Skład: role przewodniczący / sprawozdawca (jedna osoba bywa obiema).
CREATE TABLE IF NOT EXISTS judgment_judges (
    judgment_id INT UNSIGNED NOT NULL,
    position TINYINT UNSIGNED NOT NULL,
    name VARCHAR(100) NOT NULL,
    is_presiding TINYINT(1) NOT NULL DEFAULT 0,
    is_rapporteur TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (judgment_id, position),
    KEY judgment_judges_name (name, judgment_id),
    CONSTRAINT judgment_judges_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Symbol sprawy: "6110 Podatek od towarów i usług" -> code 6110 + opis.
CREATE TABLE IF NOT EXISTS judgment_symbols (
    judgment_id INT UNSIGNED NOT NULL,
    code VARCHAR(10) NOT NULL,
    description VARCHAR(255) NOT NULL,
    PRIMARY KEY (judgment_id, code),
    KEY judgment_symbols_code (code, judgment_id),
    CONSTRAINT judgment_symbols_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Hasła tematyczne CBOSA.
CREATE TABLE IF NOT EXISTS judgment_keywords (
    judgment_id INT UNSIGNED NOT NULL,
    keyword VARCHAR(100) NOT NULL,
    PRIMARY KEY (judgment_id, keyword),
    KEY judgment_keywords_keyword (keyword, judgment_id),
    CONSTRAINT judgment_keywords_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Treść wyniku jako pojedyncze pozycje (filtr: "Oddalono skargę kasacyjną" ...).
CREATE TABLE IF NOT EXISTS judgment_results (
    judgment_id INT UNSIGNED NOT NULL,
    result VARCHAR(191) NOT NULL,
    PRIMARY KEY (judgment_id, result),
    KEY judgment_results_result (result, judgment_id),
    CONSTRAINT judgment_results_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Powołane przepisy: akt (Dz.U.), artykuły, tytuł, link do ISAP.
CREATE TABLE IF NOT EXISTS judgment_legal_bases (
    judgment_id INT UNSIGNED NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    journal VARCHAR(191) NULL,
    article TEXT NULL,
    law TEXT NULL,
    link VARCHAR(255) NULL,
    PRIMARY KEY (judgment_id, position),
    KEY judgment_legal_bases_journal (journal, judgment_id),
    CONSTRAINT judgment_legal_bases_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Orzeczenia powiązane (np. wyrok WSA i wyrok NSA w tej samej sprawie).
CREATE TABLE IF NOT EXISTS judgment_related (
    judgment_id INT UNSIGNED NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    related_cbosa_id VARCHAR(20) NULL,
    docket_number VARCHAR(50) NOT NULL,
    judgment_date DATE NULL,
    judgment_type VARCHAR(100) NULL,
    PRIMARY KEY (judgment_id, position),
    KEY judgment_related_cbosa (related_cbosa_id),
    CONSTRAINT judgment_related_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Publikacje: w urzędowym zbiorze (ONSAiWSA) i glosy.
CREATE TABLE IF NOT EXISTS judgment_publications (
    judgment_id INT UNSIGNED NOT NULL,
    -- zbior / glosa
    kind VARCHAR(10) NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    text VARCHAR(500) NOT NULL,
    PRIMARY KEY (judgment_id, kind, position),
    CONSTRAINT judgment_publications_fk FOREIGN KEY (judgment_id)
        REFERENCES judgments (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
