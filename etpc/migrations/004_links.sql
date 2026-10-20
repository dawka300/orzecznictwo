-- Powiązania orzeczenie -> orzeczenie z ruling_citations (numery skarg
-- rozwiązane jak w etpc/ranks.py); przebudowywane razem z ruling_ranks.
CREATE TABLE IF NOT EXISTS ruling_links (
    citing_id INT UNSIGNED NOT NULL,
    cited_id INT UNSIGNED NOT NULL,
    PRIMARY KEY (citing_id, cited_id),
    KEY ruling_links_cited (cited_id, citing_id),
    CONSTRAINT ruling_links_citing_fk FOREIGN KEY (citing_id)
        REFERENCES rulings (id) ON DELETE CASCADE,
    CONSTRAINT ruling_links_cited_fk FOREIGN KEY (cited_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
