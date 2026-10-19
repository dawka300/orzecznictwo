-- Paczki do liczenia wektorów na innych komputerach (python -m <projekt> paczki):
-- fragmenty orzeczeń wyeksportowane do pliku, liczone gdzie indziej (np. u kolegi
-- w dockerze), wektory wracają przez paczka-import. Orzeczenie w otwartej paczce
-- jest pomijane przez zwykłe polecenie wektory.

CREATE TABLE IF NOT EXISTS vector_packages (
    name VARCHAR(100) NOT NULL PRIMARY KEY,
    model VARCHAR(191) NOT NULL,
    judgments INT UNSIGNED NOT NULL,
    chunks INT UNSIGNED NOT NULL,
    chars BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    -- NULL = u kogoś do policzenia; ustawione = wektory zaimportowane
    imported_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Orzeczenia otwartych paczek (każde najwyżej w jednej).
CREATE TABLE IF NOT EXISTS vector_package_items (
    judgment_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    package VARCHAR(100) NOT NULL,
    KEY vector_package_items_package (package)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
