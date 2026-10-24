-- Tematy (polecenie tematy, judgments_core/topics.py): BERTopic na wektorach
-- z Qdranta. Poziom A = tematy orzeczeń (wektor orzeczenia = średnia jego
-- fragmentów), B = tematy fragmentów części THE LAW. Każde przeliczenie
-- poziomu zastępuje jego tematy i przypisania (numery tematów się zmieniają).
CREATE TABLE IF NOT EXISTS topics (
    level CHAR(1) NOT NULL,
    topic SMALLINT UNSIGNED NOT NULL,
    -- nazwa po polsku od modelu czatu; pusta = jeszcze bez nazwy (tematy --tylko-nazwy)
    label VARCHAR(255) NOT NULL DEFAULT '',
    description TEXT NULL,
    -- słowa kluczowe c-TF-IDF, po przecinku, od najważniejszego
    keywords VARCHAR(1000) NOT NULL,
    size INT UNSIGNED NOT NULL,
    -- JSON: najczęstsze wartości pól payloadu (artykuły, państwa) z udziałem
    facts TEXT NULL,
    -- JSON: orzeczenia (A) albo fragmenty "orzeczenie:numer" (B) najbliższe środkowi
    representatives TEXT NULL,
    -- JSON: wycinki tekstu najbliższych, z których model nazwał temat
    excerpts MEDIUMTEXT NULL,
    model VARCHAR(100) NOT NULL,
    built_at DATETIME NOT NULL,
    PRIMARY KEY (level, topic)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Bez kluczy obcych (jak ruling_vectors): przetworz przebudowuje orzeczenia,
-- a tematy liczy się od nowa osobno.
CREATE TABLE IF NOT EXISTS ruling_topics (
    ruling_id INT UNSIGNED NOT NULL PRIMARY KEY,
    topic SMALLINT UNSIGNED NOT NULL,
    -- podobieństwo cosinusowe wektora orzeczenia do środka tematu
    score FLOAT NOT NULL,
    KEY ruling_topics_topic (topic, score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chunk_topics (
    ruling_id INT UNSIGNED NOT NULL,
    -- numer fragmentu jak w Qdrancie (payload number)
    number SMALLINT UNSIGNED NOT NULL,
    topic SMALLINT UNSIGNED NOT NULL,
    score FLOAT NOT NULL,
    PRIMARY KEY (ruling_id, number),
    KEY chunk_topics_topic (topic, ruling_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
