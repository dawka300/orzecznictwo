-- Uruchamiane raz, przy pierwszym utworzeniu wolumenu dbdata.
-- Osobna baza na projekt (jak na hostingu) i baza do testów.
CREATE DATABASE IF NOT EXISTS nsa CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS etpc CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS szkielet_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON nsa.* TO 'szkielet'@'%';
GRANT ALL PRIVILEGES ON etpc.* TO 'szkielet'@'%';
GRANT ALL PRIVILEGES ON szkielet_test.* TO 'szkielet'@'%';
