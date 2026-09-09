-- =============================================================================
-- sc_metric_norms — one row per metric we evaluate. Holds the fixed
-- literature-sourced threshold AND the sample-size trigger for switching to
-- a self-computed percentile from our own VALD_FD_* history. Flipping a
-- metric over later is a data change here, not a code change.
-- =============================================================================
CREATE TABLE sc_metric_norms (
    norm_id           INT AUTO_INCREMENT PRIMARY KEY,
    test_type         ENUM('IMTP', 'HJ', 'SJ', 'CMJ') NOT NULL,
    metric_column     VARCHAR(150) NOT NULL,   -- exact VALD_FD_* column name
    direction         ENUM('higher_better', 'lower_better') NOT NULL,
    -- Fixed literature threshold (used while sample size is below min_sample_size)
    low_cutoff        DOUBLE NULL,             -- e.g. RSI-modified < 0.30 = lower tier
    high_cutoff       DOUBLE NULL,             -- e.g. RSI-modified > 0.45 = upper tier
    citation          VARCHAR(255) NULL,       -- e.g. "Sole, Suchomel & Stone 2018, NCAA D1"
    -- Cutover rule
    min_sample_size   INT NOT NULL DEFAULT 30, -- once our own pool has >= this many results, switch
    active_source     ENUM('fixed_literature', 'own_pool') NOT NULL DEFAULT 'fixed_literature',
    notes             TEXT NULL,
    updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_metric (test_type, metric_column)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed the one metric we have a real published threshold for
INSERT INTO sc_metric_norms (test_type, metric_column, direction, low_cutoff, high_cutoff, citation, min_sample_size) VALUES
('CMJ', 'RSI-modified', 'higher_better', 0.30, 0.45, 'Sole, Suchomel & Stone 2018, NCAA Division I athletes', 30);
-- Everything else in the Priority Stack (asymmetry >15%, landing force <100 N/cm,
-- RFD/peak-force trend vs. previous test) is a fixed absolute number or a
-- within-athlete comparison already — those live directly in the generator's
-- logic, not this table, since they were never population-dependent.

-- =============================================================================
-- sc_assessments — one row per force-plate assessment cycle for an athlete.
-- Snapshots the computed flags/priority stack at generation time, so a later
-- retest can compare against what was actually flagged before, not re-derive
-- it from VALD data that may have changed since. Since force plates are
-- retested weekly, "most recent test per type as of assessment_date" is
-- resolved at generation time, independently per test type — these four
-- test_id columns record exactly which test got used for this snapshot.
-- =============================================================================
CREATE TABLE sc_assessments (
    assessment_id       INT AUTO_INCREMENT PRIMARY KEY,
    player_id           INT NOT NULL,            -- matches player_directory.Player_Id
    assessment_date     DATE NOT NULL,
    imtp_test_id        VARCHAR(255) NULL,
    hj_test_id          VARCHAR(255) NULL,
    sj_test_id          VARCHAR(255) NULL,
    cmj_test_id         VARCHAR(255) NULL,
    priority_flags_json JSON NOT NULL,
    assessment_summary  TEXT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- sc_programs — a lightweight catalog entry, not the program itself. The
-- actual 12-week program only ever lives in the generated PDF (GCS); this
-- table just makes past programs findable later from the "Find Programming"
-- tab: which player, when, and where to download it.
-- =============================================================================
CREATE TABLE sc_programs (
    program_id       INT AUTO_INCREMENT PRIMARY KEY,
    assessment_id    INT NOT NULL,
    player_id        INT NOT NULL,              -- matches player_directory.Player_Id
    days_per_week    TINYINT NOT NULL,
    program_pdf_path VARCHAR(500) NOT NULL,      -- GCS path
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (assessment_id) REFERENCES sc_assessments(assessment_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
