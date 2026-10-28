-- Wektory fragmentów dokumentów teczki (akapity, rozważania sądu, fragmenty jak w bazie
-- projektu), liczone raz przy imporcie; klucz = skrót tekstu fragmentu, więc analizy
-- biorą gotowe zamiast liczyć od nowa. float32, 1024 wymiary = 4 KB.
CREATE TABLE IF NOT EXISTS document_vectors (
    document_id INT UNSIGNED NOT NULL,
    model VARCHAR(100) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (document_id, model, sha256),
    KEY lookup (model, sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
