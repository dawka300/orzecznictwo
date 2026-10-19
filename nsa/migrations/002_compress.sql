-- Kompresja stron InnoDB (zlib) dla tabeli z tekstami orzeczeń: polski tekst
-- zajmuje na dysku 2-3 razy mniej. Działa przez "dziurkowanie" pliku (punch
-- hole): data_length w information_schema się nie zmienia, mniejszy jest
-- plik na dysku (du). Na systemie plików bez punch hole MariaDB kompresuje,
-- ale miejsca nie odzyskuje - sprawdzić na hostingu. Przebudowuje tabelę.
ALTER TABLE judgments PAGE_COMPRESSED=1 PAGE_COMPRESSION_LEVEL=6;
