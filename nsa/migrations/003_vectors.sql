-- Które orzeczenia mają wektory w Qdrancie (polecenie wektory, kolumny jak
-- w judgments_core/db.py vectors_*). Liczymy od nowa, gdy zmienił się tekst
-- (body_sha256 = judgments.text_sha256) albo model. Bez klucza obcego:
-- wiersz usuniętego orzeczenia mówi, które punkty skasować z Qdranta.
CREATE TABLE IF NOT EXISTS judgment_vectors (
    judgment_id INT UNSIGNED PRIMARY KEY,
    cbosa_id VARCHAR(20) NOT NULL,
    body_sha256 CHAR(64) NOT NULL,
    model VARCHAR(100) NOT NULL,
    chunks SMALLINT UNSIGNED NOT NULL,
    embedded_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
