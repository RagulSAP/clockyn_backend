-- Find shifts whose computed duration is suspiciously long — the signature
-- of a wrong AM/PM entry (e.g. end_time meant to be PM but stored as AM)
SELECT shift_id, org_id, shift_name, start_time, end_time,
       CASE
         WHEN end_time >= start_time THEN end_time - start_time
         ELSE (end_time - start_time) + INTERVAL '24 hours'
       END AS computed_duration
FROM shift
WHERE status = 1
ORDER BY computed_duration DESC;


UPDATE shift
SET end_time = '18:00:00', updated_at = CURRENT_TIMESTAMP
WHERE shift_id = <id_from_above>;