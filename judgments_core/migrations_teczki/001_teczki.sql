-- Teczka sprawy (baza teczki, wspólna dla projektów): katalogi, dokumenty z tekstem
-- odczytanym raz (także OCR) i oryginał pliku w częściach (max_allowed_packet 16 MB).
CREATE TABLE IF NOT EXISTS catalogs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    parent_id INT UNSIGNED NULL,
    name VARCHAR(191) NOT NULL,
    created_at DATETIME NOT NULL,
    KEY parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS documents (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    catalog_id INT UNSIGNED NULL,
    title VARCHAR(255) NOT NULL,
    filename VARCHAR(255) NOT NULL DEFAULT '',
    role VARCHAR(16) NOT NULL DEFAULT 'inny',
    doc_date DATE NULL,
    note VARCHAR(255) NOT NULL DEFAULT '',
    sha256 CHAR(64) NOT NULL,
    chars INT UNSIGNED NOT NULL,
    size INT UNSIGNED NOT NULL DEFAULT 0,
    text LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL,
    KEY catalog (catalog_id),
    KEY sha (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS document_parts (
    document_id INT UNSIGNED NOT NULL,
    part SMALLINT UNSIGNED NOT NULL,
    data MEDIUMBLOB NOT NULL,
    PRIMARY KEY (document_id, part)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
