-- Orzeczenia ETPC (wyroki i decyzje) wyprowadzone z raw_records (source 'hudoc').
-- Orzeczenie = jedno ECLI; jego wersje językowe (angielska, francuska,
-- tłumaczenia) to dokumenty HUDOC, każdy z własnym itemid. Placeholdery
-- (isplaceholder=True, wersje bez treści) nie trafiają do tabel.
-- Wszystko tu da się odtworzyć parserem z raw_records.

CREATE TABLE IF NOT EXISTS rulings (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    -- ECLI, a gdy go brak: 'itemid:001-12345'
    group_key VARCHAR(100) NOT NULL,
    ecli VARCHAR(64) NULL,
    -- judgment / decision (później może advisory_opinion ...)
    kind VARCHAR(20) NOT NULL,
    -- doctypebranch: GRANDCHAMBER, CHAMBER, COMMITTEE, ADMISSIBILITY ...
    formation VARCHAR(30) NOT NULL,
    -- kpdate: data wydania
    ruling_date DATE NOT NULL,
    -- z wersji angielskiej, gdy jej brak - z francuskiej, dalej z dowolnej
    title VARCHAR(1000) NOT NULL,
    -- 1 = kluczowe (key case) ... 4 = najmniej ważne
    importance TINYINT UNSIGNED NULL,
    separate_opinion TINYINT(1) NOT NULL DEFAULT 0,
    -- kody HUDOC bez słownika (sekcja, rodzaj): originatingbody, typedescription
    originating_body SMALLINT UNSIGNED NULL,
    type_code SMALLINT UNSIGNED NULL,
    conclusion TEXT NULL,
    represented_by TEXT NULL,
    judges TEXT NULL,
    -- issue: przepisy prawa krajowego, scl: przywołane orzecznictwo ETPC
    domestic_law TEXT NULL,
    strasbourg_case_law MEDIUMTEXT NULL,
    -- największy updateddate dokumentów z HUDOC (UTC)
    source_updated_at DATETIME NULL,
    parsed_at DATETIME NOT NULL,
    UNIQUE KEY rulings_group_key (group_key),
    KEY rulings_ecli (ecli),
    KEY rulings_date (ruling_date),
    KEY rulings_kind_date (kind, ruling_date),
    KEY rulings_importance_date (importance, ruling_date),
    FULLTEXT KEY rulings_ft_title (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Wersje językowe orzeczenia: jeden wiersz na itemid, z pełnym tekstem.
CREATE TABLE IF NOT EXISTS ruling_documents (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    itemid VARCHAR(20) NOT NULL,
    ruling_id INT UNSIGNED NOT NULL,
    -- kody HUDOC (ISO 639-2/B): ENG, FRE, POL, RUS, GER, RUM ...
    language CHAR(3) NOT NULL,
    -- HEJUD, HFJUD, HEDEC, HFDEC, HJUDPOL ...
    doctype VARCHAR(20) NOT NULL,
    -- 0 = tekst urzędowy (angielski/francuski), 1 = tłumaczenie
    is_translation TINYINT(1) NOT NULL,
    title VARCHAR(1000) NOT NULL,
    conclusion TEXT NULL,
    -- contentbody: czysty tekst z HUDOC
    body LONGTEXT NOT NULL,
    body_length INT UNSIGNED NOT NULL,
    -- RAG: dzielić i liczyć embeddingi od nowa tylko, gdy zmienił się sha256
    body_sha256 CHAR(64) NOT NULL,
    source_created_at DATETIME NULL,
    source_updated_at DATETIME NULL,
    parsed_at DATETIME NOT NULL,
    UNIQUE KEY ruling_documents_itemid (itemid),
    KEY ruling_documents_ruling (ruling_id, language),
    KEY ruling_documents_language (language),
    FULLTEXT KEY ruling_documents_ft_title (title),
    FULLTEXT KEY ruling_documents_ft_body (body),
    CONSTRAINT ruling_documents_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Numery skarg (appno), np. 22716/12; sprawy zbiorowe mają ich setki.
CREATE TABLE IF NOT EXISTS ruling_applications (
    ruling_id INT UNSIGNED NOT NULL,
    appno VARCHAR(32) NOT NULL,
    -- kolejność w HUDOC, 0 = pierwsza (wiodąca) skarga
    position SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (ruling_id, appno),
    KEY ruling_applications_appno (appno),
    CONSTRAINT ruling_applications_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Pozwane państwa (respondent), kody HUDOC: POL, TUR, MDA ...
CREATE TABLE IF NOT EXISTS ruling_respondents (
    ruling_id INT UNSIGNED NOT NULL,
    state VARCHAR(10) NOT NULL,
    PRIMARY KEY (ruling_id, state),
    KEY ruling_respondents_state (state, ruling_id),
    CONSTRAINT ruling_respondents_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Artykuły Konwencji w zapisie HUDOC: 6, 6-1, 38-1-b, P1-1, 14+P1-1 ...
-- relation: concerned (article), violation, no_violation (nonviolation).
CREATE TABLE IF NOT EXISTS ruling_articles (
    ruling_id INT UNSIGNED NOT NULL,
    relation VARCHAR(20) NOT NULL,
    article VARCHAR(50) NOT NULL,
    PRIMARY KEY (ruling_id, relation, article),
    KEY ruling_articles_article (article, relation, ruling_id),
    CONSTRAINT ruling_articles_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Słowa kluczowe z tezaurusa HUDOC (kpthesaurus) - na razie same numery,
-- etykiety w osobnej tabeli, gdy znajdziemy źródło słownika.
CREATE TABLE IF NOT EXISTS ruling_keywords (
    ruling_id INT UNSIGNED NOT NULL,
    term_id INT UNSIGNED NOT NULL,
    PRIMARY KEY (ruling_id, term_id),
    KEY ruling_keywords_term (term_id, ruling_id),
    CONSTRAINT ruling_keywords_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Skargi przywołane w tekście (extractedappno, bez własnych skarg orzeczenia):
-- "kto cytuje sprawę X" = join z ruling_applications po appno.
CREATE TABLE IF NOT EXISTS ruling_citations (
    ruling_id INT UNSIGNED NOT NULL,
    cited_appno VARCHAR(32) NOT NULL,
    PRIMARY KEY (ruling_id, cited_appno),
    KEY ruling_citations_cited (cited_appno, ruling_id),
    CONSTRAINT ruling_citations_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
