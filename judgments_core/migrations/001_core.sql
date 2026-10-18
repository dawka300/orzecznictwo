-- Tabele rdzenia, wspólne dla wszystkich projektów.
-- Tabele z danymi orzeczeń (DTO) każdy projekt dodaje w swoich migracjach.

-- Gdzie skończyło się pobieranie: kursor źródła, data ostatniej synchronizacji ...
CREATE TABLE IF NOT EXISTS sync_state (
    `key` VARCHAR(191) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Surowe rekordy dokładnie tak, jak przyszły ze źródła (JSON albo HTML).
-- Tabele projektu wyprowadza się z nich, więc zmiana parsera nie wymaga
-- ponownego pobierania. changed_at zmienia się tylko, gdy treść jest inna.
CREATE TABLE IF NOT EXISTS raw_records (
    source VARCHAR(30) NOT NULL,
    source_id VARCHAR(191) NOT NULL,
    url TEXT NULL,
    content_type VARCHAR(100) NOT NULL,
    body LONGTEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    fetched_at DATETIME NOT NULL,
    changed_at DATETIME NOT NULL,
    PRIMARY KEY (source, source_id),
    KEY raw_records_changed (source, changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
