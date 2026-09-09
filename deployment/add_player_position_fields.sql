-- Adds athlete position/handedness fields to the shared player_directory
-- table (used by HitTrax, Blast, VALD, and the mobility webpage already),
-- so any tool — not just the S&C generator — benefits from this once
-- entered. All nullable: this is additive and non-breaking for every
-- existing consumer of player_directory.

ALTER TABLE player_directory
    ADD COLUMN position_type ENUM('hitter', 'pitcher', 'both') NULL,
    ADD COLUMN bats ENUM('left', 'right', 'switch') NULL,
    ADD COLUMN throws ENUM('left', 'right') NULL;
