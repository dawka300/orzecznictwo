-- Które orzeczenia mają wektory w Qdrancie (polecenie wektory): jedna wersja
-- na orzeczenie (angielska, gdy jej brak - francuska). Liczymy od nowa, gdy
-- zmienił się tekst (body_sha256) albo model. Bez klucza obcego: wiersz
-- usuniętego orzeczenia mówi, które punkty skasować z Qdranta.
CREATE TABLE IF NOT EXISTS ruling_vectors (
    ruling_id INT UNSIGNED PRIMARY KEY,
    itemid VARCHAR(20) NOT NULL,
    body_sha256 CHAR(64) NOT NULL,
    model VARCHAR(100) NOT NULL,
    chunks SMALLINT UNSIGNED NOT NULL,
    embedded_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
