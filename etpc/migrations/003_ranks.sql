-- Ranking cytowań (etpc/ranks.py), przeliczany po każdym przetworz.
CREATE TABLE IF NOT EXISTS ruling_ranks (
    ruling_id INT UNSIGNED NOT NULL PRIMARY KEY,
    -- w ilu różnych orzeczeniach jest cytowane
    cited_by INT UNSIGNED NOT NULL,
    -- PageRank w grafie cytowań; średnio 1.0
    pagerank DOUBLE NOT NULL,
    -- miejsce w rankingu: po cited_by, przy remisie po pagerank; 1 = najczęściej cytowane
    position INT UNSIGNED NOT NULL,
    KEY ruling_ranks_position (position),
    CONSTRAINT ruling_ranks_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
