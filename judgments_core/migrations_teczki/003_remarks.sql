-- Uwagi użytkownika o dokumencie (np. „skarga do SKO; przy porównaniu i badaniu pominąć
-- pierwszą część”), osobno od note = adnotacji z odczytu pliku (OCR ...). Tekst dowolny.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS remarks TEXT NULL;
