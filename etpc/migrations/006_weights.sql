-- Waga orzeczenia (etpc/weights.py): poziom A-D z powodami, przeliczana po
-- każdym przetworz razem z rankingiem cytowań.
CREATE TABLE IF NOT EXISTS ruling_weights (
    ruling_id INT UNSIGNED NOT NULL PRIMARY KEY,
    -- A kluczowe, B ważne, C zwykłe, D rutynowe
    tier CHAR(1) NOT NULL,
    -- dlaczego taki poziom, np. "kluczowe w HUDOC; Wielka Izba; cytowania: 418"
    reasons VARCHAR(255) NOT NULL,
    KEY ruling_weights_tier (tier, ruling_id),
    CONSTRAINT ruling_weights_ruling_fk FOREIGN KEY (ruling_id)
        REFERENCES rulings (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
