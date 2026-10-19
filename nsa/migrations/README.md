Migracje projektu NSA: `001_opis.sql`, `002_...` (trzy cyfry, podkreślnik, nazwa).

Uruchamiane po migracjach rdzenia (`judgments_core/migrations/`) przez `python -m nsa migracje`; zapisane w `schema_migrations` jako `nsa/001_opis.sql`.

`001_judgments.sql`: orzeczenia (klucz: identyfikator CBOSA) i tabele do filtrów (sędziowie, symbole, hasła, wynik, przepisy, powiązane, publikacje).
