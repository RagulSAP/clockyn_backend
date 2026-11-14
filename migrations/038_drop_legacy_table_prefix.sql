-- 038: drop the old product prefix from three table names (and from the names of their keys, indexes and sequences)
--
--      punchapp_settings             -> settings
--      punchapp_manager_permissions  -> manager_permissions
--      punchapp_subscription         -> subscription
--
-- Data is not touched: ALTER ... RENAME only changes the name. Idempotent: an object is renamed only when the old name exists
-- and the new name is still free, so a database created from clockyn_ddl_latest.sql (which already has the new names) is left alone.
-- Unqualified names: the schema comes from the search path.
--
-- DEPLOY TOGETHER WITH THE CODE. The application now reads the new names. Running this file with the old code still serving
-- (or the new code before this file) gives "relation does not exist" errors on login, manager permissions, plans and billing.
-- Run it right before the new version starts, then restart.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            (1, 'TABLE',    'punchapp_settings',                         'settings'),
            (2, 'TABLE',    'punchapp_manager_permissions',              'manager_permissions'),
            (3, 'TABLE',    'punchapp_subscription',                     'subscription'),
            -- a primary key or unique constraint is renamed together with the index that backs it
            (4, 'INDEX',    'punchapp_settings_pkey',                    'settings_pkey'),
            (5, 'INDEX',    'punchapp_manager_permissions_pkey',         'manager_permissions_pkey'),
            (6, 'INDEX',    'punchapp_subscription_pkey',                'subscription_pkey'),
            (7, 'INDEX',    'punchapp_subscription_subscription_id_key', 'subscription_subscription_id_key'),
            -- sequences behind the identity columns keep the table's old name until renamed
            (8, 'SEQUENCE', 'punchapp_settings_id_seq',                  'settings_id_seq'),
            (9, 'SEQUENCE', 'punchapp_manager_permissions_id_seq',       'manager_permissions_id_seq'),
            (10, 'SEQUENCE', 'punchapp_subscription_id_seq',             'subscription_id_seq')
        ) AS t(step, kind, old_name, new_name)
        ORDER BY step
    LOOP
        -- looked up in the target schema only (current_schema() = first schema of the search path), never in public
        IF to_regclass(format('%I.%I', current_schema(), r.old_name)) IS NOT NULL
           AND to_regclass(format('%I.%I', current_schema(), r.new_name)) IS NULL THEN
            EXECUTE format('ALTER %s %I.%I RENAME TO %I', r.kind, current_schema(), r.old_name, r.new_name);
        END IF;
    END LOOP;
END $$;
